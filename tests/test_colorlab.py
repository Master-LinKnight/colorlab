import numpy as np
import pytest

from colorlab.colorspace import from_oklch, oklch, rgb_to_oklab, srgb_to_linear, linear_to_srgb
from colorlab.curves import MonotoneCurve, hue_lookup, hue_table
from colorlab.look import Look
from colorlab.lut import apply_lut, audit, bake, read_cube, write_cube

LOOK_PATH = "looks/lotus_sunset_dusk.json"


# --------------------------------------------------------------- 色彩空间
def test_srgb_roundtrip():
    x = np.linspace(0, 1, 1000)
    assert np.allclose(linear_to_srgb(srgb_to_linear(x)), x, atol=1e-9)


def test_oklab_roundtrip():
    rng = np.random.default_rng(0)
    rgb = rng.random((64, 64, 3))
    L, C, h = oklch(rgb)
    back = from_oklch(L, C, h)
    # 越界颜色会被压缩，只检查色域内的
    inside = (rgb.min(-1) > 0.02) & (rgb.max(-1) < 0.98) & (C < 0.15)
    assert np.abs(back - rgb)[inside].max() < 0.01


def test_neutral_is_neutral():
    grey = np.repeat(np.linspace(0, 1, 64)[:, None], 3, axis=1)
    L, C, h = oklch(grey)
    assert np.abs(C).max() < 1e-6


# --------------------------------------------------------------- 曲线
def test_monotone_curve_passes_knots_and_stays_monotone():
    x = [0.0, 0.2, 0.5, 1.0]
    y = [0.0, 0.3, 0.4, 1.0]
    c = MonotoneCurve(x, y)
    assert np.allclose(c(np.array(x)), y)
    dense = c(np.linspace(0, 1, 2000))
    assert (np.diff(dense) >= -1e-12).all()
    assert c(np.array([-1.0]))[0] == pytest.approx(0.0)
    assert c(np.array([2.0]))[0] == pytest.approx(1.0)


def test_hue_table_is_periodic():
    t = hue_table([(0, 1.0), (120, 0.5), (240, 0.8)], smooth=6.0)
    assert t.shape == (361,)
    assert abs(hue_lookup(t, 0.0) - hue_lookup(t, 360.0)) < 1e-9
    assert np.isfinite(t).all()


# --------------------------------------------------------------- Look
def test_look_json_roundtrip(tmp_path):
    lk = Look.from_json(LOOK_PATH)
    path = tmp_path / "look.json"
    lk.to_json(path)
    again = Look.from_json(path)
    assert again.to_dict() == lk.to_dict()


def test_look_strength_blend():
    full = Look.from_json(LOOK_PATH)
    half = full.with_strength(0.5)
    ramp = np.repeat(np.linspace(0, 1, 32)[:, None], 3, axis=1)
    a, b = full.apply(ramp), half.apply(ramp)
    assert np.allclose(b, (ramp + a) / 2, atol=1e-9)


def test_look_keeps_grey_grey():
    lk = Look.from_json(LOOK_PATH)
    grey = np.repeat(np.linspace(0, 1, 50)[:, None], 3, axis=1)
    out = lk.apply(grey)
    # 只允许分离调色带来的整体偏移，不允许出现色相串扰
    spread = out.max(1) - out.min(1)
    assert spread.max() < 0.05


# --------------------------------------------------------------- LUT
def test_cube_roundtrip(tmp_path):
    lk = Look.from_json(LOOK_PATH)
    l = bake(lk, 9)
    path = write_cube(tmp_path / "t.cube", l, title="t")
    back = read_cube(path)
    assert back.shape == l.shape
    assert np.allclose(back, l, atol=1e-6)


def test_baked_lut_matches_analytic():
    lk = Look.from_json(LOOK_PATH)
    rng = np.random.default_rng(1)
    # 接近真实素材的分布：亮度随机，色度适中（不是纯饱和色）
    base = rng.random((160, 160, 1)) ** 1.4
    tint = (rng.random((160, 160, 3)) - 0.5) * 0.5
    img = np.clip(base + tint, 0, 1)
    l33, l65 = bake(lk, 33), bake(lk, 65)
    e33 = np.abs(apply_lut(img, l33) - lk.apply(img)).max(-1)
    e65 = np.abs(apply_lut(img, l65) - lk.apply(img)).max(-1)
    assert np.percentile(e33, 99.9) < 0.03
    assert np.percentile(e65, 99.9) < 0.015


def test_lut_audit_reports_small_folds():
    lk = Look.from_json(LOOK_PATH)
    info = audit(bake(lk, 33))
    assert info["range"][0] >= 0.0 and info["range"][1] <= 1.0
    # 按色相降饱和会带来折返，但只在极饱和颜色里出现，幅度要小
    assert info["max_fold"] > -0.12
    assert info["max_second_diff"] < 0.25
