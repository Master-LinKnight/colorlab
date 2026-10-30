"""从"原图 + 目标图"自动拟合一版 Look（初稿用）。

思路：不建立逐像素对应（两张图内容不同，没有对应关系），而是比较两边的**统计量**：

1. 影调  —— 亮度分位数匹配（p0.5 … p99.5），再按 amount 向恒等映射收缩
2. 色相  —— 按 OKLCh 色相分桶，比较每桶的亮度中位、色度中位、色相圆均值，
            反解出 Look 需要的 luma / sat / rot 系数
3. 分离  —— 暗部和高光区的平均 RGB 偏移

**能力边界（实测结论）**：这套方法对齐的是"全局统计"，所以对**同场景、同机位**的
曝光/白平衡/饱和度差异效果不错；但跨场景的"风格"转移会失灵 —— 参考图好看往往来自
光位、明暗结构、局部光，这些是空间信息，逐像素曲线表达不了。

所以 derive 定位是**初稿**：给一个能跑通的起点，真正的调子还是走
compare → 改曲线 → grade 迭代。
"""

from __future__ import annotations

import numpy as np

from .colorspace import oklch
from .curves import MonotoneCurve
from .look import Look

# 影调匹配用的分位点
_QUANTILES = (0.5, 2, 5, 10, 20, 35, 50, 65, 80, 90, 95, 98, 99.5)


def _clean_knots(x, y):
    """去掉重复/非递增的 x，并补上 0 和 1 两端。"""
    order = np.argsort(x, kind="stable")
    x, y = np.asarray(x, float)[order], np.asarray(y, float)[order]
    keep = np.r_[True, np.diff(x) > 1e-6]
    x, y = x[keep], y[keep]
    if x[0] > 0.0:
        x = np.r_[0.0, x]
        y = np.r_[0.0, y]
    if x[-1] < 1.0:
        x = np.r_[x, 1.0]
        y = np.r_[y, 1.0]
    return x, np.clip(y, 0.0, 1.0)


def fit_tone(src_rgb, ref_rgb, amount=0.8):
    """亮度分位数匹配 -> 影调控制点。"""
    from .colorspace import rgb_to_oklab

    ls = rgb_to_oklab(src_rgb)[..., 0].ravel()
    lr = rgb_to_oklab(ref_rgb)[..., 0].ravel()

    xs = np.percentile(ls, _QUANTILES)
    ys = np.percentile(lr, _QUANTILES)
    xs, ys = _clean_knots(xs, ys)

    # 向恒等映射收缩，避免把两张不同内容的图硬拉成同一条直方图
    ys = np.clip(xs + amount * (ys - xs), 0.0, 1.0)

    # 斜率保护：曲线太陡会毁掉渐变
    for _ in range(6):
        curve = MonotoneCurve(xs, ys)
        dense = curve(np.linspace(0, 1, 256))
        slope = np.diff(dense) * 255
        worst = slope.max() if slope.size else 1.0
        if worst <= 3.0:
            break
        amount *= 0.8
        ys = np.clip(xs + amount * (ys - xs), 0.0, 1.0)

    return [(float(a), float(b)) for a, b in zip(xs, ys)]


def _hue_bins(rgb, n_bins=24, min_chroma=0.04, stride=3):
    """按 OKLCh 色相分桶，返回每桶的 (占比, 亮度中位, 色度中位, 色相圆均值)。"""
    L, C, h = oklch(rgb)
    L, C, h = L.ravel()[::stride], C.ravel()[::stride], h.ravel()[::stride]
    colorful = C > min_chroma
    total = max(int(colorful.sum()), 1)

    edges = np.linspace(0, 360, n_bins + 1)
    share = np.zeros(n_bins)
    med_l = np.full(n_bins, np.nan)
    med_c = np.full(n_bins, np.nan)
    mean_h = np.full(n_bins, np.nan)

    idx = np.clip(np.digitize(h, edges) - 1, 0, n_bins - 1)
    for b in range(n_bins):
        sel = colorful & (idx == b)
        n = int(sel.sum())
        share[b] = n / total
        if n < 200:
            continue
        med_l[b] = float(np.median(L[sel]))
        med_c[b] = float(np.median(C[sel]))
        rad = np.radians(h[sel])
        mean_h[b] = float(np.degrees(np.arctan2(np.sin(rad).mean(), np.cos(rad).mean())) % 360)

    return share, med_l, med_c, mean_h, edges


def _fill_nan(values, edges, periodic=True):
    """把没有对应关系的桶用邻居线性插值补上。"""
    v = np.array(values, float)
    ok = ~np.isnan(v)
    if ok.sum() == 0:
        return np.ones_like(v) if periodic else np.zeros_like(v)
    if ok.sum() == 1:
        return np.full_like(v, v[ok][0])
    centers = (edges[:-1] + edges[1:]) / 2
    if periodic:
        x = np.r_[centers[ok] - 360, centers[ok], centers[ok] + 360]
        y = np.r_[v[ok], v[ok], v[ok]]
    else:
        x, y = centers[ok], v[ok]
    out = np.interp(centers, x, y)
    return out


def _smooth_periodic(values, sigma=1.0):
    """环形平滑，保证曲线不在桶边界抖动。"""
    if sigma <= 0 or values.size < 3:
        return values
    r = max(1, int(round(sigma * 3)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    padded = np.concatenate([values[-r:], values, values[:r]])
    return np.convolve(padded, k, mode="valid")


def fit_hue(src_rgb, ref_rgb, tone_curve, amount=0.8, n_bins=24, min_share=0.002):
    """按色相桶反解 luma / sat / rot 系数。"""
    s_share, s_l, s_c, s_h, edges = _hue_bins(src_rgb, n_bins)
    r_share, r_l, r_c, r_h, _ = _hue_bins(ref_rgb, n_bins)

    usable = (s_share > min_share) & (r_share > min_share)
    centers = (edges[:-1] + edges[1:]) / 2

    # L 是"过完 tone 之后"的亮度，所以先把 src 的亮度推到调色后的位置
    tone_l = tone_curve(np.clip(np.nan_to_num(s_l, nan=0.0), 0, 1))

    f_l = np.full(n_bins, np.nan)
    f_c = np.full(n_bins, np.nan)
    d_h = np.full(n_bins, np.nan)
    for b in np.where(usable)[0]:
        if tone_l[b] > 1e-3:
            f_l[b] = np.clip(r_l[b] / tone_l[b], 0.5, 1.8)
        if s_c[b] > 1e-4:
            f_c[b] = np.clip(r_c[b] / s_c[b], 0.5, 2.2)
        d_h[b] = ((r_h[b] - s_h[b] + 180) % 360) - 180

    f_l = _smooth_periodic(_fill_nan(f_l, edges))
    f_c = _smooth_periodic(_fill_nan(f_c, edges))
    d_h = _smooth_periodic(_fill_nan(d_h, edges))

    # amount 收缩到"不变"
    f_l = 1.0 + amount * (f_l - 1.0)
    f_c = 1.0 + amount * (f_c - 1.0)
    d_h = np.clip(amount * d_h, -25.0, 25.0)

    luma = [(float(h), float(v)) for h, v in zip(centers, f_l)]
    sat = [(float(h), float(v)) for h, v in zip(centers, f_c)]
    rot = [(float(h), float(v)) for h, v in zip(centers, d_h)]
    return luma, sat, rot


def fit_split(src_rgb, ref_rgb, amount=0.8):
    """暗部 / 高光的平均 RGB 偏移 -> 分离调色控制点。"""
    from .colorspace import rgb_to_oklab

    ls = rgb_to_oklab(src_rgb)[..., 0]
    lr = rgb_to_oklab(ref_rgb)[..., 0]

    def zone(rgb, L, lo, hi):
        m = (L >= lo) & (L < hi)
        if m.sum() < 500:
            return None
        return rgb[m].reshape(-1, 3).mean(0)

    pairs = []
    for lo, hi, pos in ((0.0, 0.25, 0.0), (0.75, 1.01, 1.0)):
        a, b = zone(src_rgb, ls, lo, hi), zone(ref_rgb, lr, lo, hi)
        if a is None or b is None:
            pairs.append((pos, np.zeros(3)))
        else:
            pairs.append((pos, np.clip((b - a) * amount, -0.04, 0.04)))

    (p0, d0), (p1, d1) = pairs
    return [
        (float(p0), tuple(float(v) for v in d0)),
        (0.25, tuple(float(v) * 0.6 for v in d0)),
        (0.5, (0.0, 0.0, 0.0)),
        (0.75, tuple(float(v) * 0.6 for v in d1)),
        (float(p1), tuple(float(v) for v in d1)),
    ]


def derive_look(src_rgb, ref_rgb, name="derived", amount=0.6) -> Look:
    """从两张图拟合一版 Look。amount 控制强度（0 = 不变，1 = 完全按统计结果）。"""
    amount = float(np.clip(amount, 0.0, 1.0))
    tone_pts = fit_tone(src_rgb, ref_rgb, amount)
    tone_curve = MonotoneCurve([p[0] for p in tone_pts], [p[1] for p in tone_pts])
    luma, sat, rot = fit_hue(src_rgb, ref_rgb, tone_curve, amount)
    split = fit_split(src_rgb, ref_rgb, amount)
    return Look(
        name=name,
        tone=tuple(tone_pts),
        luma=tuple(luma),
        sat=tuple(sat),
        rot=tuple(rot),
        split=tuple(split),
        strength=1.0,
    )
