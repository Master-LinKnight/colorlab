"""图像色彩分析：影调分布、色彩分区、OKLCh 色相剖面。

这些函数是"看片"的工具：拿到一张参考图，先知道它的数字特征，
再决定 Look 的曲线该往哪边推。
"""

from __future__ import annotations

import numpy as np

from .colorspace import luminance, oklch


def luma_percentiles(rgb, qs=(1, 5, 10, 25, 50, 75, 90, 95, 99, 99.9)) -> dict:
    """亮度分位数：判断"黑场压到哪、高光顶到哪、中位在哪"。"""
    L = luminance(rgb)
    return {
        (int(q) if float(q).is_integer() else float(q)): float(np.percentile(L, q))
        for q in qs
    }


def zone_stats(rgb) -> dict:
    """按亮度分区统计平均 RGB 和平均饱和度（挑分离调色方向用）。"""
    L = luminance(rgb)
    out = {}
    for lo, hi, tag in [
        (0.00, 0.12, "shadow"),
        (0.12, 0.40, "lower"),
        (0.40, 0.70, "mid"),
        (0.70, 0.92, "high"),
        (0.92, 1.01, "specular"),
    ]:
        m = (L >= lo) & (L < hi)
        if m.sum() < 50:
            continue
        px = rgb[m].reshape(-1, 3)
        mx, mn = px.max(1), px.min(1)
        sat = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0.0)
        out[tag] = {
            "coverage": float(m.mean()),
            "rgb": tuple(round(float(v), 4) for v in px.mean(0)),
            "sat": round(float(sat.mean()), 4),
        }
    return out


def hue_profile(rgb, bin_deg: int = 20, min_chroma: float = 0.03) -> list:
    """按 OKLCh 色相分桶的剖面：每段占多少画面、亮度中位、色度中位。

    这是设计"按色相调色"的主要依据：先看画面由哪些色相构成，
    再决定哪个色相该压暗、该降饱和、该旋转。
    """
    L, C, h = oklch(rgb)
    L, C, h = L.ravel(), C.ravel(), h.ravel()
    chroma_mask = C > min_chroma
    rows = []
    for start in range(0, 360, bin_deg):
        end = start + bin_deg
        sel = chroma_mask & (h >= start) & (h < end)
        if sel.sum() < 200:
            continue
        rows.append(
            {
                "hue": f"{start:03d}-{end % 360:03d}",
                "share": round(float(sel.mean()), 4),
                "L": round(float(np.median(L[sel])), 4),
                "C": round(float(np.median(C[sel])), 4),
            }
        )
    return rows


def chroma_mean(rgb, min_chroma: float = 0.03) -> float:
    _, C, _ = oklch(rgb)
    sel = C > min_chroma
    return float(C[sel].mean()) if sel.any() else 0.0


def report(rgb, name: str = "image") -> dict:
    """一次性给出所有分析数据。"""
    return {
        "name": name,
        "size": list(rgb.shape[:2]),
        "luma": luma_percentiles(rgb),
        "zones": zone_stats(rgb),
        "mean_chroma": round(chroma_mean(rgb), 4),
        "mean_rgb": tuple(round(float(v), 4) for v in rgb.reshape(-1, 3).mean(0)),
        "hues": hue_profile(rgb),
    }


def compare(src, ref) -> dict:
    """两张图的差异摘要：亮度分位差 + 各色相亮度差。"""
    sl, rl = luma_percentiles(src), luma_percentiles(ref)
    sp, rp = hue_profile(src), hue_profile(ref)
    src_by_hue = {r["hue"]: r for r in sp}
    deltas = []
    for row in rp:
        key = row["hue"]
        if key in src_by_hue:
            s = src_by_hue[key]
            deltas.append(
                {
                    "hue": key,
                    "d_share": round(row["share"] - s["share"], 4),
                    "d_L": round(row["L"] - s["L"], 4),
                    "d_C": round(row["C"] - s["C"], 4),
                }
            )
    return {
        "luma_ref_minus_src": {q: round(rl[q] - sl[q], 4) for q in sl},
        "mean_chroma": {"src": round(chroma_mean(src), 4), "ref": round(chroma_mean(ref), 4)},
        "hue_deltas": deltas,
    }


def gap(ref, img) -> dict:
    """关键统计的对照（参考, 图像），用来判断自动调色有没有靠近目标。"""
    lr, li = luma_percentiles(ref), luma_percentiles(img)
    return {
        "p10": (lr[10], li[10]),
        "p50": (lr[50], li[50]),
        "p90": (lr[90], li[90]),
        "chroma": (chroma_mean(ref), chroma_mean(img)),
    }


def format_report(rep: dict, title: str = None) -> str:
    """把 report() 的结果排成人能读的文本。"""
    lines = [title or f"== {rep.get('name', 'image')} {tuple(rep.get('size', []))} =="]
    lines.append("  luma  " + "  ".join(f"p{int(q)}={v:.3f}" for q, v in rep["luma"].items()))
    for tag, z in rep["zones"].items():
        lines.append(
            f"  {tag:9s} {z['coverage']:5.1%}  rgb={z['rgb']}  sat={z['sat']:.3f}"
        )
    lines.append(f"  mean C {rep['mean_chroma']:.3f}   mean rgb {rep['mean_rgb']}")
    for row in rep["hues"]:
        lines.append(
            f"  hue {row['hue']}  share={row['share']:5.1%}  L={row['L']:.3f}  C={row['C']:.3f}"
        )
    return "\n".join(lines)
