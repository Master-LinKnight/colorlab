import numpy as np
import pytest

from colorlab.analyze import chroma_mean, luma_percentiles
from colorlab.colorspace import oklab_to_rgb, rgb_to_oklab
from colorlab.derive import derive_look, fit_tone
from colorlab.lut import bake, write_cube


def synthetic(h=120, w=160, seed=3):
    rng = np.random.default_rng(seed)
    ys, xs = np.mgrid[0:h, 0:w]
    hueish = (xs / w)
    img = np.stack(
        [
            0.15 + 0.6 * hueish,
            0.2 + 0.5 * (1 - hueish),
            0.25 + 0.4 * (ys / h),
        ],
        -1,
    )
    return np.clip(img + rng.normal(0, 0.01, img.shape), 0, 1)


def test_fit_tone_is_monotone_and_bounded():
    a, b = synthetic(seed=1), synthetic(seed=2)
    pts = fit_tone(a, b, amount=0.8)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert xs[0] == 0.0 and xs[-1] == 1.0
    assert all(np.diff(xs) > 0)
    assert all(np.diff(ys) >= -1e-9)
    assert min(ys) >= 0.0 and max(ys) <= 1.0


def test_derive_recovers_exposure_and_saturation():
    """目标图 = 原图压暗 + 加饱和，derive 应该朝这个方向走。"""
    src = synthetic()
    lab = rgb_to_oklab(src)
    darker = lab.copy()
    darker[..., 0] *= 0.82
    darker[..., 1:] *= 1.35
    ref = oklab_to_rgb(darker[..., 0], darker[..., 1], darker[..., 2])

    lk = derive_look(src, ref, amount=0.9)
    out = lk.apply(src)

    l_before = luma_percentiles(src)[50]
    l_after = luma_percentiles(out)[50]
    l_ref = luma_percentiles(ref)[50]
    assert l_after < l_before                      # 变暗了
    assert abs(l_after - l_ref) < abs(l_before - l_ref)   # 更接近目标

    c_before, c_after, c_ref = chroma_mean(src), chroma_mean(out), chroma_mean(ref)
    assert c_after > c_before
    assert abs(c_after - c_ref) < abs(c_before - c_ref)


def test_derived_look_bakes(tmp_path):
    src = synthetic()
    ref = synthetic(seed=9)
    lk = derive_look(src, ref, name="t")
    path = write_cube(tmp_path / "d.cube", bake(lk, 9), title="t")
    assert path.exists()


def test_amount_zero_is_identity():
    src = synthetic()
    ref = synthetic(seed=5)
    lk = derive_look(src, ref, amount=0.0)
    out = lk.apply(src)
    assert np.abs(out - src).max() < 0.03   # 只剩分离调色的极小偏移
