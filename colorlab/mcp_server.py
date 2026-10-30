"""MCP 服务：让 AI agent 直接操作 colorlab。

启动（stdio，给 agent 调用）：
    python3 -m colorlab.mcp_server
或装好后：
    colorlab-mcp

在 OpenCode 里注册（opencode.json，把路径换成仓库实际所在位置）：

    {
      "mcp": {
        "servers": {
          "colorlab": {
            "type": "local",
            "command": ["python3", "-m", "colorlab.mcp_server"],
            "environment": { "PYTHONPATH": "/path/to/colorlab" },
            "enabled": true
          }
        }
      }
    }

工具全部返回 JSON（dict），路径一律用绝对路径。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import analyze, io, lut
from .look import Look

try:  # pragma: no cover - 只有在装了 mcp 时才好用
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少 mcp 依赖，先运行：python3 -m pip install -e \".[mcp]\""
    ) from exc


mcp = FastMCP("colorlab")

LOOKS_DIR = Path(__file__).resolve().parent.parent / "looks"


# --------------------------------------------------------------------- 帮助
def _resolve_look(look_path: str) -> Look:
    p = Path(look_path).expanduser()
    if not p.exists():
        name = p.name if p.suffix else f"{p.name}.json"
        p = LOOKS_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"找不到 Look 文件：{look_path}（已查 {LOOKS_DIR}）")
    return Look.from_json(p)


def _stats(rgb) -> dict:
    return {
        "luma": {str(q): round(v, 4) for q, v in analyze.luma_percentiles(rgb).items()},
        "mean_rgb": [round(float(v), 4) for v in rgb.reshape(-1, 3).mean(0)],
        "mean_chroma": round(analyze.chroma_mean(rgb), 4),
    }


# --------------------------------------------------------------------- 工具
@mcp.tool()
def look_schema() -> dict:
    """返回 Look 参数的 schema 和示例。

    agent 在写/改 Look 之前应该先读这个：它说明了每个字段的含义、取值范围和单位。
    """
    return {
        "description": "Look = 一组调色曲线，全部在 Rec.709 / Gamma 2.4 显示参考空间里生效",
        "fields": {
            "name": {"type": "string", "desc": "Look 名称"},
            "tone": {
                "type": "[[L_in, L_out], ...]",
                "desc": "影调曲线，OKLab 的 L（0..1）。必须从 0 开始、到 1 结束，x 递增",
                "example": [[0, 0], [0.2, 0.186], [0.5, 0.47], [1, 1]],
            },
            "luma": {
                "type": "[[hue_deg, factor], ...]",
                "desc": "按色相（0..360）的亮度系数，1.0 不变，<1 压暗。会按色度淡出（灰不受影响）",
                "example": [[0, 1.03], [130, 0.74], [345, 0.99]],
            },
            "sat": {
                "type": "[[hue_deg, factor], ...]",
                "desc": "按色相的饱和度系数，1.0 不变",
                "example": [[0, 1.4], [130, 0.7], [350, 1.5]],
            },
            "rot": {
                "type": "[[hue_deg, degrees], ...]",
                "desc": "按色相的旋转角度（度），正=顺时针向红/黄方向",
                "example": [[0, 6], [170, 11], [340, 20]],
            },
            "split": {
                "type": "[[luma, [dR, dG, dB]], ...]",
                "desc": "分离调色，按亮度加偏移（显示参考值，约 ±0.03 以内）",
                "example": [[0, [-0.008, 0.012, 0.014]], [1, [0.028, 0.012, -0.014]]],
            },
            "global_sat": {"type": "float", "desc": "全局饱和度，默认 1.0"},
            "shadow_sat": {"type": "float", "desc": "暗部额外饱和，默认 0.0"},
            "chroma_floor": {"type": "float", "desc": "色度低于此值完全不做色相操作，默认 0.015"},
            "chroma_full": {"type": "float", "desc": "色度达到此值色相操作全量生效，默认 0.06"},
            "strength": {"type": "float", "desc": "整体强度 0..1，默认 1.0"},
        },
        "notes": [
            "色相控制点用 OKLCh 的色相：0 红、40 橙、90 黄、140 绿、200 青、260 蓝、330 品红",
            "小图预演 -> 确认 -> 再烘焙 LUT，不要直接对大图反复烘焙",
            "LUT 是逐像素映射，暗角/局部光必须靠 DaVinci Resolve 的窗口补",
        ],
    }


@mcp.tool()
def list_looks() -> dict:
    """列出 looks/ 目录下所有可用的 Look。"""
    items = []
    for p in sorted(LOOKS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append({"name": data.get("name", p.stem), "path": str(p)})
        except Exception:
            items.append({"name": p.stem, "path": str(p), "error": "JSON 解析失败"})
    return {"looks": items, "count": len(items)}


@mcp.tool()
def read_look(look_path: str) -> dict:
    """读取一个 Look 的完整参数（用于在它的基础上微调）。"""
    return _resolve_look(look_path).to_dict()


@mcp.tool()
def write_look(look_path: str, look: dict) -> dict:
    """把 Look 参数字典保存到文件（会校验字段和曲线合法性）。"""
    obj = Look.from_dict(look)
    obj.to_json(Path(look_path).expanduser())
    return {"saved": str(Path(look_path).expanduser()), "name": obj.name}


@mcp.tool()
def analyze_image(image_path: str, width: int = 1200) -> dict:
    """分析一张图的色彩特征：亮度分位、分区统计、OKLCh 色相剖面。

    调色第一步：先知道素材和参考图的数字差异。
    """
    rgb = io.load_image(Path(image_path).expanduser(), max_width=width)
    rep = analyze.report(rgb, name=Path(image_path).name)
    return rep


@mcp.tool()
def compare_images(src: str, ref: str, ref_crop: str = "", width: int = 1200) -> dict:
    """对比素材和参考图，给出影调差和每个色相的亮度/色度差异。

    ref_crop: 参考图裁剪比例 "x0,y0,x1,y1"（0..1），用于去掉黑边和水印。
    """
    s = io.load_image(Path(src).expanduser(), max_width=width)
    r = io.crop_fraction(io.load_image(Path(ref).expanduser(), max_width=width), ref_crop)
    return analyze.compare(s, r)


@mcp.tool()
def grade_image(
    src: str,
    out: str,
    look_path: str,
    strength: float = None,
    width: int = 0,
    bake: bool = False,
    lut_size: int = 65,
) -> dict:
    """用 Look 给一张图调色，输出预览图；bake=True 时顺便烘焙同名 .cube。

    返回调色前后的亮度统计，方便 agent 判断是否接近参考图。
    """
    lk = _resolve_look(look_path)
    if strength is not None:
        lk = lk.with_strength(strength)
    w = width or None
    src_path = Path(src).expanduser()
    rgb = io.load_image(src_path, max_width=w)
    graded = lk.apply(rgb)
    out_path = Path(out).expanduser()
    io.save_image(out_path, graded, max_width=w)
    result = {"out": str(out_path), "before": _stats(rgb), "after": _stats(graded)}
    if bake:
        cube = out_path.with_suffix(".cube")
        l = lut.bake(lk, lut_size)
        lut.write_cube(cube, l, title=f"{lk.name} {lut_size}")
        result["cube"] = str(cube)
        result["audit"] = lut.audit(l)
    return result


@mcp.tool()
def bake_lut(
    out: str,
    look_path: str,
    size: int = 65,
    strength: float = None,
    verify_image: str = "",
    title: str = "",
) -> dict:
    """把 Look 烘焙成 DaVinci Resolve 可导入的 .cube。

    verify_image 给一张素材图时，会同时返回烘焙误差（LUT 结果 vs 解析结果）。
    """
    lk = _resolve_look(look_path)
    if strength is not None:
        lk = lk.with_strength(strength)
    l = lut.bake(lk, size)
    out_path = Path(out).expanduser()
    lut.write_cube(out_path, l, title=title or lk.name)
    result = {"out": str(out_path), "size": size, "audit": lut.audit(l)}
    if verify_image:
        img = io.load_image(Path(verify_image).expanduser(), max_width=1000)
        err = np.abs(lut.apply_lut(img, l) - lk.apply(img)).max(-1)
        result["bake_error"] = {
            "p50": round(float(np.percentile(err, 50)), 4),
            "p99": round(float(np.percentile(err, 99)), 4),
            "p99_9": round(float(np.percentile(err, 99.9)), 4),
            "max": round(float(err.max()), 4),
        }
    return result


@mcp.tool()
def apply_lut_file(image_path: str, lut_path: str, out: str, width: int = 0) -> dict:
    """把现成的 .cube 套到图上（三线性插值），用于检查 LUT 效果。"""
    img = io.load_image(Path(image_path).expanduser(), max_width=width or None)
    l = lut.read_cube(Path(lut_path).expanduser())
    out_path = Path(out).expanduser()
    io.save_image(out_path, lut.apply_lut(img, l), max_width=width or None)
    return {"out": str(out_path), "lut_size": int(l.shape[0])}


@mcp.tool()
def audit_lut(lut_path: str) -> dict:
    """检查 LUT 的值域、折返幅度（max_fold）和二阶差分（banding 风险）。"""
    return lut.audit(lut.read_cube(Path(lut_path).expanduser()))


@mcp.tool()
def make_comparison_sheet(
    src: str,
    out: str,
    ref: str = "",
    look_path: str = "",
    lut_path: str = "",
    ref_crop: str = "",
    width: int = 1200,
) -> dict:
    """生成「参考图 / 原片 / 调色后」三联对比图。

    look_path 和 lut_path 二选一；都不给就只拼「参考 / 原片」。
    """
    panels = []
    if ref:
        panels.append(
            io.crop_fraction(io.load_image(Path(ref).expanduser(), max_width=width), ref_crop)
        )
    src_img = io.load_image(Path(src).expanduser(), max_width=width)
    panels.append(src_img)
    if lut_path:
        panels.append(lut.apply_lut(src_img, lut.read_cube(Path(lut_path).expanduser())))
    elif look_path:
        panels.append(_resolve_look(look_path).apply(src_img))
    out_path = Path(out).expanduser()
    io.save_image(out_path, io.stack_vertical(panels, width=width), max_width=width)
    return {"out": str(out_path), "panels": len(panels)}


@mcp.tool()
def derive_look(
    src: str,
    ref: str,
    out: str,
    amount: float = 0.6,
    ref_crop: str = "",
    bake_size: int = 0,
) -> dict:
    """从「原图 + 目标图」自动拟合一版 Look（初稿用）。

    只对齐全局统计，适合同场景同机位的曝光/白平衡/饱和度统一；
    跨场景的「风格」转移会失灵（参考图的气质多来自光位和明暗结构）。
    拿到初稿后仍然建议 compare + 改曲线再交付。
    """
    from .derive import derive_look as _derive

    src_img = io.load_image(Path(src).expanduser(), max_width=1200)
    ref_img = io.crop_fraction(io.load_image(Path(ref).expanduser(), max_width=1200), ref_crop)
    lk = _derive(src_img, ref_img, name=Path(out).stem, amount=amount)
    out_path = Path(out).expanduser()
    lk.to_json(out_path)
    graded = lk.apply(src_img)
    result = {
        "look": str(out_path),
        "before": _stats(src_img),
        "after": _stats(graded),
        "ref": _stats(ref_img),
    }
    if bake_size:
        cube = out_path.with_suffix(".cube")
        l = lut.bake(lk, bake_size)
        lut.write_cube(cube, l, title=lk.name)
        result["cube"] = str(cube)
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
