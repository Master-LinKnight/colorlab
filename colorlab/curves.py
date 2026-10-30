"""曲线工具：单调三次插值、按色相的周期表、分离调色表。

不依赖 scipy —— 只用到 numpy 就能跑，方便分发。
"""

from __future__ import annotations

import numpy as np


class MonotoneCurve:
    """单调三次 Hermite 插值（Fritsch–Carlson），不会过冲。

    控制点之外的输入被夹到端点值，保证 LUT 在 0 和 1 上稳定。
    """

    def __init__(self, x, y):
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if x.ndim != 1 or y.ndim != 1 or x.size != y.size:
            raise ValueError("x / y 必须是一维且长度相同")
        if x.size < 2:
            raise ValueError("至少需要 2 个控制点")
        if np.any(np.diff(x) <= 0):
            raise ValueError("x 必须严格递增")

        h = np.diff(x)
        delta = np.diff(y) / h
        m = np.zeros_like(y)

        if x.size == 2:
            m[:] = delta[0]
        else:
            # 端点用三点公式（与 scipy 的 PchipInterpolator 一致）
            m[0] = self._end_slope(h[0], h[1], delta[0], delta[1])
            m[-1] = self._end_slope(h[-1], h[-2], delta[-1], delta[-2])

        # 内部节点：同号取加权调和平均，异号或为零取 0（保证单调）
        for i in range(1, x.size - 1):
            d0, d1 = delta[i - 1], delta[i]
            if d0 * d1 > 0:
                w1 = 2 * h[i] + h[i - 1]
                w2 = h[i] + 2 * h[i - 1]
                m[i] = (w1 + w2) / (w1 / d0 + w2 / d1)
            else:
                m[i] = 0.0

        self.x, self.y, self.m = x, y, m

    @staticmethod
    def _end_slope(h0, h1, d0, d1):
        """端点斜率：三点估计 + 保形修正。"""
        d = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if np.sign(d) != np.sign(d0):
            return 0.0
        if np.sign(d0) != np.sign(d1) and abs(d) > 3 * abs(d0):
            return 3 * d0
        return d

    def __call__(self, xq):
        xq = np.asarray(xq, dtype=np.float64)
        x, y, m = self.x, self.y, self.m
        xc = np.clip(xq, x[0], x[-1])
        idx = np.clip(np.searchsorted(x, xc, side="right") - 1, 0, x.size - 2)
        h = x[idx + 1] - x[idx]
        t = (xc - x[idx]) / h
        h00 = 2 * t**3 - 3 * t**2 + 1
        h10 = t**3 - 2 * t**2 + t
        h01 = -2 * t**3 + 3 * t**2
        h11 = t**3 - t**2
        out = (h00 * y[idx] + h10 * h * m[idx] + h01 * y[idx + 1] + h11 * h * m[idx + 1])
        # 端点外直接返回端点值
        return np.where(xq < x[0], y[0], np.where(xq > x[-1], y[-1], out))


HUE_STEPS = 361  # 0..360 的整数色相表
_HUE_AXIS = np.arange(HUE_STEPS, dtype=np.float64)


def _smooth_circular(values, sigma):
    """环形高斯平滑（sigma 单位为采样点）。"""
    if sigma <= 0:
        return values
    radius = max(1, int(round(sigma * 3)))
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()
    padded = np.concatenate([values[-radius:], values, values[:radius]])
    return np.convolve(padded, k, mode="valid")


def hue_table(points, smooth=9.0):
    """按色相（0..360，周期）定义的参数表。

    points: [(hue_deg, value), ...]，色相需递增，最后一个点之后自动绕回第一个点。
    返回 shape (361,) 的稠密表，可被 hue_lookup 查询。
    """
    pts = sorted(points, key=lambda p: p[0])
    hs = np.array([p[0] for p in pts], dtype=np.float64)
    vs = np.array([p[1] for p in pts], dtype=np.float64)
    if hs[0] < 0 or hs[-1] > 360:
        raise ValueError("色相控制点必须在 0..360 之间")

    # 补上绕回段，让 0 和 360 连续
    hs_ext = np.concatenate([hs - 360, hs, hs + 360])
    vs_ext = np.concatenate([vs, vs, vs])
    dense = np.interp(_HUE_AXIS[:-1], hs_ext, vs_ext)   # 0..359，360 与 0 同值
    dense = _smooth_circular(dense, smooth)
    return np.concatenate([dense, dense[:1]])


def hue_lookup(table, hue_deg):
    """在色相表上取值（线性插值）。"""
    return np.interp(hue_deg, _HUE_AXIS, table)


def split_tables(points, n=129):
    """阴影/高光分离调色表。

    points: [(luma, (dr, dg, db)), ...]，luma 递增。
    返回 shape (n, 3)，索引为 luma 0..1。
    """
    xs = np.array([p[0] for p in points], dtype=np.float64)
    grid = np.linspace(0.0, 1.0, n)
    return np.stack(
        [
            np.interp(grid, xs, [p[1][i] for p in points])
            for i in range(3)
        ],
        axis=-1,
    )


def strength_blend(original, graded, strength):
    """按强度在原始结果和调色结果之间线性混合。"""
    if strength >= 1.0:
        return graded
    if strength <= 0.0:
        return original
    return original * (1.0 - strength) + graded * strength
