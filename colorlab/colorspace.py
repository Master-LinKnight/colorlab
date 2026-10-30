"""色彩空间基础转换：sRGB 编码值 <-> 线性 <-> OKLab。

所有函数都接受 numpy 数组，最后一维是通道（RGB 或 Lab），
取值范围：编码 sRGB / 显示参考 0..1；线性 RGB 0..1（可超过）。
"""

from __future__ import annotations

import numpy as np

# Rec.709 亮度权重
LUMA_W = np.array([0.2126, 0.7152, 0.0722])


def srgb_to_linear(x):
    """编码 sRGB -> 线性 sRGB。"""
    x = np.asarray(x, dtype=np.float64)
    return np.where(x <= 0.04045, x / 12.92, ((np.maximum(x, 0.0) + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x):
    """线性 sRGB -> 编码 sRGB（先截断到 0..1，避免负值产生 NaN）。"""
    x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)


def luminance(rgb):
    """Rec.709 亮度（输入为编码或线性值均可，只做加权求和）。"""
    return np.asarray(rgb)[..., :3] @ LUMA_W


def rgb_to_oklab(rgb):
    """编码 sRGB -> OKLab（Björn Ottosson 的矩阵）。"""
    r, g, b = (srgb_to_linear(np.asarray(rgb)[..., i]) for i in range(3))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    return np.stack(
        [
            0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
        ],
        axis=-1,
    )


def oklab_to_linear(L, a, b):
    """OKLab -> 线性 sRGB（可能超出 0..1）。"""
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3
    return np.stack(
        [
            4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
        ],
        axis=-1,
    )


def oklab_to_rgb(L, a, b, compress=True):
    """OKLab -> 编码 sRGB。

    compress=True 时对越界颜色做平滑的色度压缩（按亮度缩放色差），
    保证输出连续、可被 3D LUT 采样，不会出现硬裁切断层。
    """
    lin = oklab_to_linear(L, a, b)
    if compress:
        lum = lin @ LUMA_W
        d = lin - lum[..., None]
        over = np.maximum(
            np.maximum(d.max(-1, keepdims=True) - (1.0 - lum[..., None]),
                       -d.min(-1, keepdims=True) - lum[..., None]),
            0.0,
        )
        lin = lum[..., None] + d * (1.0 / (1.0 + 3.0 * over))
    return linear_to_srgb(lin)


def oklch(rgb):
    """编码 sRGB -> (L, C, h[度])。"""
    lab = rgb_to_oklab(rgb)
    L = lab[..., 0]
    a, b = lab[..., 1], lab[..., 2]
    return L, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360.0


def from_oklch(L, C, h_deg):
    """(L, C, h[度]) -> 编码 sRGB。"""
    r = np.radians(h_deg)
    return oklab_to_rgb(L, C * np.cos(r), C * np.sin(r))
