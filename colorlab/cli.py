"""命令行入口：python -m colorlab ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from . import analyze, io, lut, look


def _load_look(args) -> look.Look:
    lk = look.Look.from_json(args.look)
    strength = getattr(args, "strength", None)
    if strength is not None:
        lk = lk.with_strength(strength)
    return lk


def cmd_analyze(args) -> int:
    for path in args.images:
        img = io.load_image(path, max_width=args.width)
        rep = analyze.report(img, name=Path(path).name)
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(analyze.format_report(rep))
    return 0


def cmd_compare(args) -> int:
    src = io.load_image(args.src, max_width=args.width)
    ref = io.crop_fraction(io.load_image(args.ref, max_width=args.width), args.ref_crop)
    diff = analyze.compare(src, ref)
    if args.json:
        print(json.dumps(diff, ensure_ascii=False, indent=2))
        return 0
    print("== 影调差（参考 - 原片）==")
    print("  " + "  ".join(f"p{int(q)}={v:+.3f}" for q, v in diff["luma_ref_minus_src"].items()))
    print(f"  mean chroma  原片 {diff['mean_chroma']['src']}  ->  参考 {diff['mean_chroma']['ref']}")
    print("== 色相差异（按 OKLCh 20° 分桶）==")
    for row in sorted(diff["hue_deltas"], key=lambda r: -abs(r["d_L"])):
        print(
            f"  hue {row['hue']}  share{row['d_share']:+.1%}  "
            f"dL{row['d_L']:+.3f}  dC{row['d_C']:+.3f}"
        )
    return 0


def cmd_grade(args) -> int:
    src = io.load_image(args.src, max_width=args.width)
    lk = _load_look(args)
    graded = lk.apply(src)
    io.save_image(args.out, graded, max_width=args.width, quality=args.quality)
    print(f"-> {args.out}")
    if args.lut:
        l = lut.bake(lk, args.size)
        lut.write_cube(args.lut, l, title=lk.name)
        print(f"-> {args.lut}  ({args.size}^3)")
    return 0


def cmd_bake(args) -> int:
    lk = _load_look(args)
    l = lut.bake(lk, args.size)
    lut.write_cube(args.out, l, title=args.title or lk.name)
    info = lut.audit(l)
    print(f"-> {args.out}  {args.size}^3  range={info['range']}")
    print(
        f"   折返: 最大负台阶 {info['max_fold']:+.4f}，"
        f"最大二阶差分 {info['max_second_diff']:.4f}"
    )
    if args.verify:
        src = io.load_image(args.verify, max_width=1000)
        err = np.abs(lut.apply_lut(src, l) - lk.apply(src)).max(-1)
        print(
            "   烘焙误差: "
            + "  ".join(f"p{int(q)}={np.percentile(err, q):.4f}" for q in (50, 99, 99.9))
            + f"  max={err.max():.4f}"
        )
    return 0


def cmd_apply(args) -> int:
    img = io.load_image(args.image, max_width=args.width)
    l = lut.read_cube(args.lut)
    io.save_image(args.out, lut.apply_lut(img, l), max_width=args.width)
    print(f"-> {args.out}")
    return 0


def cmd_audit(args) -> int:
    l = lut.read_cube(args.lut)
    info = lut.audit(l)
    for k, v in info.items():
        print(f"  {k}: {v}")
    return 0


def cmd_sheet(args) -> int:
    panels = []
    if args.ref:
        panels.append(io.crop_fraction(io.load_image(args.ref, max_width=args.width), args.ref_crop))
    src = io.load_image(args.src, max_width=args.width)
    panels.append(src)
    if args.lut:
        panels.append(lut.apply_lut(src, lut.read_cube(args.lut)))
    else:
        panels.append(_load_look(args).apply(src))
    io.save_image(args.out, io.stack_vertical(panels, width=args.width), max_width=args.width)
    print(f"-> {args.out}")
    return 0


def cmd_derive(args) -> int:
    from .derive import derive_look

    src = io.load_image(args.src, max_width=args.width)
    ref = io.crop_fraction(io.load_image(args.ref, max_width=args.width), args.ref_crop)
    lk = derive_look(src, ref, name=Path(args.out).stem, amount=args.amount)
    lk.to_json(args.out)
    print(f"-> {args.out}  (amount={args.amount})")

    graded = lk.apply(src)
    before, after = analyze.gap(ref, src), analyze.gap(ref, graded)
    print(f"   亮度中位  原片 {before['p50'][1]:.3f} -> 调色 {after['p50'][1]:.3f}   目标 {before['p50'][0]:.3f}")
    print(f"   平均色度  原片 {before['chroma'][1]:.3f} -> 调色 {after['chroma'][1]:.3f}   目标 {before['chroma'][0]:.3f}")

    if args.preview:
        io.save_image(args.preview, graded, max_width=args.width)
        print(f"-> {args.preview}")
    if args.lut:
        l = lut.bake(lk, args.size)
        lut.write_cube(args.lut, l, title=lk.name)
        print(f"-> {args.lut}  ({args.size}^3)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("colorlab", description="色彩分析 / Look 设计 / 3D LUT 生成")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, width=1200):
        sp.add_argument("--width", type=int, default=width, help="处理宽度（默认 %(default)s）")

    a = sub.add_parser("analyze", help="分析一张或多张图的色彩特征")
    a.add_argument("images", nargs="+")
    a.add_argument("--json", action="store_true")
    common(a)
    a.set_defaults(func=cmd_analyze)

    c = sub.add_parser("compare", help="对比原片和参考图")
    c.add_argument("src")
    c.add_argument("ref")
    c.add_argument("--json", action="store_true")
    c.add_argument("--ref-crop", help="参考图裁剪比例 x0,y0,x1,y1（去掉黑边/水印）")
    common(c)
    c.set_defaults(func=cmd_compare)

    g = sub.add_parser("grade", help="用 Look 预演调色（可顺便烘焙 LUT）")
    g.add_argument("src")
    g.add_argument("-o", "--out", required=True)
    g.add_argument("--look", required=True)
    g.add_argument("--lut", help="同时烘焙一份 .cube")
    g.add_argument("--size", type=int, default=33)
    g.add_argument("--strength", type=float)
    g.add_argument("--quality", type=int, default=95)
    common(g)
    g.set_defaults(func=cmd_grade)

    b = sub.add_parser("bake", help="只烘焙 LUT")
    b.add_argument("-o", "--out", required=True)
    b.add_argument("--look", required=True)
    b.add_argument("--size", type=int, default=65)
    b.add_argument("--strength", type=float)
    b.add_argument("--title")
    b.add_argument("--verify", metavar="IMAGE", help="用一张图验证烘焙误差")
    b.set_defaults(func=cmd_bake)

    ap = sub.add_parser("apply", help="把现成 LUT 套到图上")
    ap.add_argument("image")
    ap.add_argument("--lut", required=True)
    ap.add_argument("-o", "--out", required=True)
    common(ap)
    ap.set_defaults(func=cmd_apply)

    au = sub.add_parser("audit", help="检查 LUT 的单调性 / 值域")
    au.add_argument("lut")
    au.set_defaults(func=cmd_audit)

    s = sub.add_parser("sheet", help="生成 参考/原片/调色 对比图")
    s.add_argument("src")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--ref")
    s.add_argument("--look")
    s.add_argument("--lut")
    s.add_argument("--ref-crop", help="参考图裁剪比例 x0,y0,x1,y1")
    common(s)
    s.set_defaults(func=cmd_sheet)

    d = sub.add_parser("derive", help="从原图 + 目标图自动拟合一版 Look")
    d.add_argument("src")
    d.add_argument("ref")
    d.add_argument("-o", "--out", required=True, help="输出的 Look JSON")
    d.add_argument("--amount", type=float, default=0.6, help="强度 0..1，默认 0.6")
    d.add_argument("--ref-crop", help="参考图裁剪比例 x0,y0,x1,y1")
    d.add_argument("--preview", help="顺便输出一张调色预览")
    d.add_argument("--lut", help="顺便烘焙一份 .cube")
    d.add_argument("--size", type=int, default=65)
    common(d)
    d.set_defaults(func=cmd_derive)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
