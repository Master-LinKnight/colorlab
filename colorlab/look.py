"""Look：一套可序列化的调色参数 + 它的逐像素映射。

调色流程（全部在显示参考空间里做，对应 Resolve 里 LUT 节点所在的位置）：

1. 影调    —— OKLab 的 L 上套一条单调 S 曲线
2. 亮度    —— 按色相加权压暗（例如绿色压下去，暗调背景更沉）
3. 色度    —— 按色相加权增减饱和度
4. 色相    —— 按色相做小幅旋转（例如洋红粉转暖红粉）
5. 分离调色 —— 阴影加青绿、高光加暖

近中性像素（灰）不参与 2/3/4 步，避免灰阶被色相操作污染。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .colorspace import LUMA_W, from_oklch, rgb_to_oklab
from .curves import MonotoneCurve, hue_lookup, hue_table, split_tables, strength_blend


@dataclass
class Look:
    """一套调色参数。所有字段都能直接写进 JSON。"""

    name: str = "untitled"

    # 影调：控制点 (x, y)，OKLab L
    tone: tuple = (
        (0.00, 0.00), (0.06, 0.050), (0.12, 0.112), (0.20, 0.186),
        (0.28, 0.270), (0.36, 0.358), (0.46, 0.470), (0.56, 0.588),
        (0.66, 0.706), (0.76, 0.824), (0.86, 0.922), (0.94, 0.976), (1.00, 1.00),
    )
    # 按色相的亮度系数
    luma: tuple = ()
    # 按色相的饱和度系数
    sat: tuple = ()
    # 按色相的旋转角度（度）
    rot: tuple = ()
    # 分离调色点
    split: tuple = ()

    global_sat: float = 1.0
    shadow_sat: float = 0.0
    chroma_floor: float = 0.012   # 低于该色度视为中性，完全不做色相操作
    chroma_full: float = 0.06     # 达到该色度时色相操作全量生效
    hue_smooth: float = 9.0       # 色相表平滑半径（度）
    strength: float = 1.0

    _tone: MonotoneCurve = field(init=False, repr=False, default=None)
    _luma_t: np.ndarray = field(init=False, repr=False, default=None)
    _sat_t: np.ndarray = field(init=False, repr=False, default=None)
    _rot_t: np.ndarray = field(init=False, repr=False, default=None)
    _split_t: np.ndarray = field(init=False, repr=False, default=None)

    def __post_init__(self):
        self.tone = tuple(tuple(float(v) for v in p) for p in self.tone)
        self.luma = tuple(tuple(float(v) for v in p) for p in self.luma)
        self.sat = tuple(tuple(float(v) for v in p) for p in self.sat)
        self.rot = tuple(tuple(float(v) for v in p) for p in self.rot)
        self.split = tuple((float(p[0]), tuple(float(v) for v in p[1])) for p in self.split)

        self._tone = MonotoneCurve([p[0] for p in self.tone], [p[1] for p in self.tone])
        self._luma_t = hue_table(self.luma, self.hue_smooth) if self.luma else np.ones(361)
        self._sat_t = hue_table(self.sat, self.hue_smooth) if self.sat else np.ones(361)
        self._rot_t = (
            hue_table(self.rot, self.hue_smooth * 1.4) if self.rot else np.zeros(361)
        )
        self._split_t = (
            split_tables(self.split) if self.split else np.zeros((129, 3))
        )

    # ---------------------------------------------------------------- io
    @classmethod
    def from_dict(cls, data: dict) -> "Look":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @classmethod
    def from_json(cls, path) -> "Look":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "tone": [list(p) for p in self.tone],
            "luma": [list(p) for p in self.luma],
            "sat": [list(p) for p in self.sat],
            "rot": [list(p) for p in self.rot],
            "split": [[p[0], list(p[1])] for p in self.split],
            "global_sat": self.global_sat,
            "shadow_sat": self.shadow_sat,
            "chroma_floor": self.chroma_floor,
            "chroma_full": self.chroma_full,
            "hue_smooth": self.hue_smooth,
            "strength": self.strength,
        }

    def to_json(self, path) -> Path:
        path = Path(path)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def with_strength(self, strength: float) -> "Look":
        clone = Look.from_dict(self.to_dict())
        clone.strength = float(strength)
        return clone

    # ---------------------------------------------------------------- grade
    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """对编码 sRGB 图像（HxWx3 或 Nx3，0..1）应用整套 look。"""
        x = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
        lab = rgb_to_oklab(x)
        L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]
        C = np.hypot(A, B)
        h = np.degrees(np.arctan2(B, A)) % 360.0

        # 色度越低、亮度越高，色相相关操作越弱
        # —— 中性灰、以及接近纯白的微偏色高光，不该被"绿色压暗"之类的规则波及
        lo = max(self.chroma_floor, 1e-6)
        hi = max(self.chroma_full, lo + 1e-6)
        t = np.clip((C - lo) / (hi - lo), 0.0, 1.0)
        w = t * t * (3.0 - 2.0 * t)
        fade = np.clip((L - 0.75) / 0.25, 0.0, 1.0)
        w = w * (1.0 - fade * fade * (3.0 - 2.0 * fade))

        L2 = self._tone(np.clip(L, 0.0, 1.0)) * (1.0 + w * (hue_lookup(self._luma_t, h) - 1.0))

        C2 = (
            C
            * (1.0 + w * (hue_lookup(self._sat_t, h) - 1.0))
            * self.global_sat
            * (1.0 + self.shadow_sat * np.exp(-L / 0.16))
        )
        h2 = h + w * hue_lookup(self._rot_t, h)

        out = from_oklch(L2, C2, h2)

        if self._split_t is not None and np.any(self._split_t):
            grid = np.linspace(0.0, 1.0, self._split_t.shape[0])
            for i in range(3):
                out[..., i] += np.interp(L2, grid, self._split_t[:, i])

        out = np.clip(out, 0.0, 1.0)
        return strength_blend(x, out, self.strength)


def luma_of(rgb):
    return np.asarray(rgb)[..., :3] @ LUMA_W
