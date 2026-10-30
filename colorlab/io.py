"""图片读写：统一成 float32 的 0..1 RGB 数组。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def load_image(path, max_width: int = None) -> np.ndarray:
    """读图 -> (H, W, 3) float32，0..1。max_width 用于缩放到工作尺寸。"""
    im = Image.open(path)
    im = im.convert("RGB")
    if max_width and im.width > max_width:
        scale = max_width / im.width
        im = im.resize((max_width, max(1, round(im.height * scale))), Image.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255.0


def save_image(path, rgb: np.ndarray, max_width: int = None, quality: int = 95) -> Path:
    """0..1 浮点图 -> 8bit 图片（jpg/png 由后缀决定）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    im = Image.fromarray((arr * 255.0).round().astype(np.uint8), mode="RGB")
    if max_width and im.width > max_width:
        scale = max_width / im.width
        im = im.resize((max_width, max(1, round(im.height * scale))), Image.LANCZOS)
    kwargs = {"quality": quality} if path.suffix.lower() in (".jpg", ".jpeg") else {}
    im.save(path, **kwargs)
    return path


def crop_fraction(rgb: np.ndarray, spec) -> np.ndarray:
    """按比例裁剪：spec = "x0,y0,x1,y1"，取值 0..1，例如 "0.02,0.08,0.98,0.75"。"""
    if not spec:
        return rgb
    x0, y0, x1, y1 = (float(v) for v in str(spec).split(","))
    h, w = rgb.shape[:2]
    return rgb[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def stack_vertical(images, width: int = 1200, gap: int = 14, bg=(18, 18, 18)):
    """把多张 0..1 图纵向拼成一张对比图。"""
    panels = []
    for img in images:
        arr = (np.clip(np.asarray(img), 0, 1) * 255).round().astype(np.uint8)
        im = Image.fromarray(arr, mode="RGB")
        scale = width / im.width
        im = im.resize((width, max(1, round(im.height * scale))), Image.LANCZOS)
        panels.append(im)
    height = sum(p.height for p in panels) + gap * (len(panels) + 1)
    sheet = Image.new("RGB", (width, height), bg)
    y = gap
    for p in panels:
        sheet.paste(p, (0, y))
        y += p.height + gap
    return np.asarray(sheet, dtype=np.float32) / 255.0
