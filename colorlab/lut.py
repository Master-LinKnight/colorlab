"""3D LUT：烘焙、读写 .cube、三线性应用。

数组约定（和文件里的存储顺序故意区分开，避免踩坑）：
    LUT 内部统一用 [r, g, b, 3] —— 三个轴依次是输入 R / G / B。
    .cube 文件要求 R 变化最快，write_cube / read_cube 负责转换。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .look import Look


# --------------------------------------------------------------------- bake
def bake(look: Look, size: int = 33) -> np.ndarray:
    """把 Look 烘焙成 size^3 的 3D LUT，返回 [size, size, size, 3]。"""
    axis = np.linspace(0.0, 1.0, size)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    grid = np.stack([r, g, b], axis=-1)
    return np.clip(look.apply(grid), 0.0, 1.0)


# --------------------------------------------------------------------- write
def write_cube(path, lut: np.ndarray, title: str = None, domain=(0.0, 1.0)) -> Path:
    """写出 Resolve / Nuke / OBS 都能读的 .cube（3D）。"""
    path = Path(path)
    lut = np.asarray(lut, dtype=np.float64)
    if lut.ndim != 4 or lut.shape[-1] != 3 or len(set(lut.shape[:3])) != 1:
        raise ValueError("lut 必须是 [n, n, n, 3]")
    n = lut.shape[0]

    lines = [
        f'TITLE "{title or path.stem}"',
        f"LUT_3D_SIZE {n}",
        f"DOMAIN_MIN {domain[0]} {domain[0]} {domain[0]}",
        f"DOMAIN_MAX {domain[1]} {domain[1]} {domain[1]}",
        "",
    ]
    # R 变化最快
    flat = lut.reshape(n, n, n, 3).transpose(2, 1, 0, 3).reshape(-1, 3)
    lines.extend(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------- read
def read_cube(path) -> np.ndarray:
    """读取 .cube，返回 [n, n, n, 3]（轴顺序 r, g, b）。"""
    size = None
    data = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split(None, 1)[0].upper()
        if head == "LUT_3D_SIZE":
            size = int(line.split()[1])
            continue
        if head.startswith(("TITLE", "DOMAIN_", "LUT_1D", "LUT_IN_")):
            continue
        data.append([float(v) for v in line.split()])
    if size is None:
        raise ValueError(f"{path} 里没有 LUT_3D_SIZE")
    arr = np.asarray(data, dtype=np.float64)
    if arr.shape != (size**3, 3):
        raise ValueError(f"数据行数不对：{arr.shape[0]} != {size ** 3}")
    # 文件里 r 最快 -> reshape(b, g, r, 3) -> 转置回 (r, g, b, 3)
    return arr.reshape(size, size, size, 3).transpose(2, 1, 0, 3)


# --------------------------------------------------------------------- apply
def apply_lut(rgb, lut: np.ndarray) -> np.ndarray:
    """三线性插值应用 LUT（用于校验烘焙结果）。"""
    x = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    n = lut.shape[0]
    pos = x * (n - 1)
    i0 = np.floor(pos).astype(int)
    f = pos - i0
    i1 = np.minimum(i0 + 1, n - 1)

    out = np.zeros(x.shape, dtype=np.float64)
    for dr in (0, 1):
        wr = f[..., 0] if dr else 1.0 - f[..., 0]
        ir = i1[..., 0] if dr else i0[..., 0]
        for dg in (0, 1):
            wg = f[..., 1] if dg else 1.0 - f[..., 1]
            ig = i1[..., 1] if dg else i0[..., 1]
            for db in (0, 1):
                wb = f[..., 2] if db else 1.0 - f[..., 2]
                ib = i1[..., 2] if db else i0[..., 2]
                out += (wr * wg * wb)[..., None] * lut[ir, ig, ib]
    return np.clip(out, 0.0, 1.0)


# --------------------------------------------------------------------- audit
def audit(lut: np.ndarray) -> dict:
    """检查 LUT 的值域、折返幅度和二阶差分（banding 风险）。

    max_fold：最大的"负台阶"。理想值为 0 或极小负数。
    按色相降饱和的 look 天然会有很小的折返（比如越绿越暗时 G 通道
    略微回落），只要幅度小（< 0.03）就没有可见副作用。
    """
    lut = np.asarray(lut, dtype=np.float64)
    steps = [
        np.diff(lut[..., 0], axis=0),
        np.diff(lut[..., 1], axis=1),
        np.diff(lut[..., 2], axis=2),
    ]
    return {
        "size": lut.shape[0],
        "range": (float(lut.min()), float(lut.max())),
        "max_fold": float(min(s.min() for s in steps)),
        "max_second_diff": float(
            max(np.abs(np.diff(lut[..., i], 2, axis=i)).max() for i in range(3))
        ),
    }
