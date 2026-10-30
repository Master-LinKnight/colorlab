"""colorlab —— 影视调色的色彩分析 / Look 设计与 3D LUT 生成。"""

from .analyze import compare, format_report, hue_profile, luma_percentiles, report, zone_stats
from .colorspace import from_oklch, oklch, oklab_to_rgb, rgb_to_oklab
from .curves import MonotoneCurve, hue_table, split_tables
from .derive import derive_look
from .io import load_image, save_image, stack_vertical
from .look import Look
from .lut import apply_lut, audit, bake, read_cube, write_cube

__version__ = "0.1.0"

__all__ = [
    "Look",
    "MonotoneCurve",
    "apply_lut",
    "audit",
    "bake",
    "compare",
    "derive_look",
    "format_report",
    "from_oklch",
    "hue_profile",
    "hue_table",
    "load_image",
    "luma_percentiles",
    "oklab_to_rgb",
    "oklch",
    "read_cube",
    "report",
    "rgb_to_oklab",
    "save_image",
    "split_tables",
    "stack_vertical",
    "write_cube",
    "zone_stats",
]
