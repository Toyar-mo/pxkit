# -*- coding: utf-8 -*-
"""pxkit 命令行入口。

设计原则：每个子命令只做一件事，全部支持 --json 便于脚本串联。
破坏性操作（覆盖已有文件）默认拒绝，要 --force。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from . import background as bgm
from . import grid, imgio, palette, sheet
from .color import hex2rgb, rgb2hex


def _p(out_obj):
    print(json.dumps(out_obj, ensure_ascii=False, indent=2))


def _stat(rgba):
    flat = rgba.reshape(-1, 4)
    keys = (flat[:, 0].astype(np.int64) << 16) | \
           (flat[:, 1].astype(np.int64) << 8) | flat[:, 2].astype(np.int64)
    opaque = flat[:, 3] >= 128
    return {
        "size": [int(rgba.shape[1]), int(rgba.shape[0])],
        "unique_colors": int(len(np.unique(keys[opaque]))) if opaque.any() else 0,
        "opaque_ratio": round(float(opaque.mean()), 4),
        "has_alpha": bool((flat[:, 3] < 250).any()),
    }


# ---------------- 子命令 ----------------

def cmd_info(a):
    rgba = imgio.load_rgba(a.image)
    info = _stat(rgba)
    info["file"] = str(a.image)
    bg = bgm.corner_background(rgba)
    info["corner_bg"] = {"hex": rgb2hex(bg[:3]), "rgb": list(bg[:3]),
                         "alpha_mean": round(bg[3], 1)}
    if a.json:
        _p(info)
    else:
        print("文件 : %s" % info["file"])
        print("尺寸 : %dx%d" % tuple(info["size"]))
        print("颜色数: %d" % info["unique_colors"])
        print("不透明占比: %.1f%%" % (info["opaque_ratio"] * 100))
        print("带透明通道: %s" % ("是" if info["has_alpha"] else "否"))
        print("四角底色: %s" % info["corner_bg"]["hex"])
    return 0


def cmd_detect(a):
    rgba = imgio.load_rgba(a.image)
    b, score, cands = grid.auto_block_size(rgba, a.max_block)
    method = "block-uniformity"
    if b <= 1:
        b2 = grid.detect_block_size(rgba, a.max_block)
        if b2 > 1:
            b, method = b2, "edge-profile"
    h, w = rgba.shape[:2]
    res = {
        "file": str(a.image),
        "size": [w, h],
        "block": int(b),
        "method": method,
        "score": round(float(score), 4),
        "true_size": [w // b if b else w, h // b if b else h],
        "top_candidates": [
            {"block": int(c), "score": round(float(s), 4)}
            for c, s in sorted(cands, key=lambda t: -t[1])[:8]
        ],
    }
    if a.json:
        _p(res)
    else:
        print("文件     : %s" % res["file"])
        print("原图尺寸 : %dx%d" % tuple(res["size"]))
        print("推断倍数 : x%d   (%s, 一致率 %.3f)" % (b, method, score))
        print("真实分辨率: %dx%d" % tuple(res["true_size"]))
        print("候选     : " + ", ".join(
            "x%d=%.3f" % (c["block"], c["score"]) for c in res["top_candidates"]))
    return 0


def cmd_pixelate(a):
    rgba = imgio.load_rgba(a.image)
    if a.bg != "none":
        bg = None if a.bg == "auto" else hex2rgb(a.bg)
        rgba = bgm.remove_background(rgba, bg=bg, tol=a.tol, soft=a.soft)
        if a.despill:
            rgba = bgm.despill_edges(rgba)
    out, info = grid.depixelate(rgba, block=a.block or 0,
                               max_block=a.max_block)
    dst = Path(a.out) if a.out else Path(a.image).with_name(
        Path(a.image).stem + "_px.png")
    if dst.exists() and not a.force:
        print("拒绝覆盖已存在文件: %s  (加 --force)" % dst, file=sys.stderr)
        return 2
    imgio.save_rgba(out, dst, scale=a.scale)
    res = {"file": str(a.image), "out": str(dst), **info,
           "scale_out": a.scale}
    if a.json:
        _p(res)
    else:
        print("输入 %s  %dx%d" % (a.image, *info["src_size"]))
        print("倍数 x%d  (%s)" % (info["block"], info["method"]))
        print("输出 %s  %dx%d  (放大 x%d)" % (dst, *info["out_size"], a.scale))
    return 0


def cmd_quantize(a):
    rgba = imgio.load_rgba(a.image)
    bg = None if a.bg == "auto" else (False if a.bg == "none" else hex2rgb(a.bg))
    if bg is None:
        bc = bgm.corner_background(rgba)
        bg = bc[:3] if bc[3] >= 250 else False
    out, pal = palette.quantize(rgba, a.colors, bg if bg else None)
    dst = Path(a.out) if a.out else Path(a.image).with_name(
        Path(a.image).stem + "_q%d.png" % a.colors)
    if dst.exists() and not a.force:
        print("拒绝覆盖已存在文件: %s  (加 --force)" % dst, file=sys.stderr)
        return 2
    imgio.save_rgba(out, dst, scale=a.scale)
    res = {"file": str(a.image), "out": str(dst), "want": a.colors,
           "got": len(pal), "palette": palette.palette_report(pal),
           "before": _stat(rgba), "after": _stat(out)}
    if a.palette_out:
        Path(a.palette_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.palette_out).write_text(
            "\n".join(r["hex"] for r in res["palette"]) + "\n", encoding="utf-8")
        res["palette_file"] = str(a.palette_out)
    if a.json:
        _p(res)
    else:
        print("调色板 %d 色:" % len(pal))
        for r in res["palette"]:
            print("  %s  %s" % (r["hex"], r["lab"]))
        print("颜色数 %d -> %d" % (res["before"]["unique_colors"],
                                   res["after"]["unique_colors"]))
        print("输出 %s" % dst)
    return 0


def cmd_clean(a):
    """一站式：抠底 -> 去色边 -> 降分辨率 -> 限色。"""
    rgba = imgio.load_rgba(a.image)
    steps = []

    if a.bg != "none":
        b = None if a.bg == "auto" else hex2rgb(a.bg)
        before = _stat(rgba)
        rgba = bgm.remove_background(rgba, bg=b, tol=a.tol, soft=a.soft)
        steps.append({"step": "remove_bg", "opaque_ratio":
                      _stat(rgba)["opaque_ratio"],
                      "was": before["opaque_ratio"]})
    if a.despill:
        rgba = bgm.despill_edges(rgba)
        steps.append({"step": "despill"})
    if a.trim:
        rgba = sheet.trim_rgba(rgba, padding=a.pad, square=a.square)
        steps.append({"step": "trim", "size": _stat(rgba)["size"]})

    dep = {"block": 1, "method": "none"}
    if a.block != "0":
        rgba, dep = grid.depixelate(
            rgba, block=int(a.block) if a.block != "auto" else 0,
            max_block=a.max_block)
        steps.append({"step": "depixelate", **dep})

    pal = []
    if a.colors > 0:
        b2 = None if a.bg == "auto" else (
            False if a.bg == "none" else hex2rgb(a.bg))
        if b2 is None:
            bc = bgm.corner_background(rgba)
            b2 = bc[:3] if bc[3] >= 250 else False
        rgba, pal = palette.quantize(rgba, a.colors, b2 if b2 else None)
        steps.append({"step": "quantize", "colors": len(pal)})

    if a.out_size:
        tw, th = (int(x) for x in a.out_size.lower().split("x"))
        sh, sw = rgba.shape[:2]
        f = max(1, min(tw // max(1, sw), th // max(1, sh)))
        if f > 1:
            rgba = sheet.resize_nearest(rgba, f)
            steps.append({"step": "upscale", "factor": f})

    dst = Path(a.out) if a.out else Path(a.image).with_name(
        Path(a.image).stem + "_clean.png")
    if dst.exists() and not a.force:
        print("拒绝覆盖已存在文件: %s  (加 --force)" % dst, file=sys.stderr)
        return 2
    imgio.save_rgba(rgba, dst)
    res = {"file": str(a.image), "out": str(dst), "steps": steps,
           "final": _stat(rgba),
           "palette": palette.palette_report(pal) if pal else []}
    if a.json:
        _p(res)
    else:
        print("步骤:")
        for s in steps:
            print("  " + "  ".join("%s=%s" % (k, v) for k, v in s.items()))
        print("最终 %dx%d  颜色 %d" % (*res["final"]["size"],
                                      res["final"]["unique_colors"]))
        print("输出 %s" % dst)
    return 0


def cmd_slice(a):
    rgba = imgio.load_rgba(a.image)
    if a.rows < 1 or a.cols < 1:
        print("行列数必须 >= 1 (拿到 rows=%d cols=%d)" % (a.rows, a.cols),
              file=sys.stderr)
        return 1
    if a.rows > rgba.shape[0] or a.cols > rgba.shape[1]:
        print("行列数超过图片像素尺寸 %dx%d" % (rgba.shape[1], rgba.shape[0]),
              file=sys.stderr)
        return 1
    tiles = sheet.slice_uniform(rgba, a.rows, a.cols, order=a.order)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    meta = []
    for i, ((r, c), t) in enumerate(tiles):
        tt = t
        if a.trim:
            tt = sheet.trim_rgba(t, padding=a.pad, square=a.square)
        name = "%s%02d_r%dc%d.png" % (a.prefix, i, r, c)
        p = outdir / name
        if p.exists() and not a.force:
            print("拒绝覆盖: %s (加 --force)" % p, file=sys.stderr)
            return 2
        imgio.save_rgba(tt, p, scale=a.scale)
        meta.append({"index": i, "row": r, "col": c, "file": name,
                     "size": [int(tt.shape[1]), int(tt.shape[0])],
                     "opaque_ratio": _stat(tt)["opaque_ratio"]})
    res = {"sheet": str(a.image), "rows": a.rows, "cols": a.cols,
           "count": len(meta), "outdir": str(outdir), "tiles": meta}
    (outdir / "_slice_index.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.json:
        _p(res)
    else:
        empty = [m["file"] for m in meta if m["opaque_ratio"] < 0.001]
        print("切分 %dx%d = %d 张 -> %s" % (a.rows, a.cols, len(meta), outdir))
        if empty:
            print("空格 %d 个: %s" % (len(empty), ", ".join(empty[:8])))
    return 0


def cmd_pack(a):
    if a.cols < 1:
        print("--cols 必须 >= 1 (拿到 %d)" % a.cols, file=sys.stderr)
        return 1
    files = imgio.list_images(a.src, recursive=a.recursive)
    if not files:
        print("没找到图片: %s" % a.src, file=sys.stderr)
        return 2
    tiles = []
    for f in files:
        t = imgio.load_rgba(f)
        if a.trim:
            t = sheet.trim_rgba(t, padding=a.pad, square=a.square)
        tiles.append((f.stem, t))
    sh, meta = sheet.pack_sheet(tiles, cols=a.cols,
                                cell=a.cell or None, fit=a.fit, gap=a.gap)
    dst = Path(a.out)
    if dst.exists() and not a.force:
        print("拒绝覆盖已存在文件: %s  (加 --force)" % dst, file=sys.stderr)
        return 2
    imgio.save_rgba(sh, dst)
    mf = dst.with_suffix(".json")
    mf.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    res = {"out": str(dst), "count": len(tiles), "meta_file": str(mf), **meta}
    if a.json:
        _p(res)
    else:
        print("打包 %d 张 -> %s  (%dx%d, %d 列, 格 %d)" % (
            len(tiles), dst, meta["sheet_size"][0], meta["sheet_size"][1],
            meta["cols"], meta["cell"]))
        print("索引 %s" % mf)
    return 0


def cmd_selftest(a):
    """不依赖外部文件的自检：合成一张像素图，走完整闭环。"""
    ok = True
    log = []

    def chk(name, cond, extra=""):
        nonlocal ok
        ok = ok and bool(cond)
        log.append("%s %s %s" % ("PASS" if cond else "FAIL", name, extra))

    # 1. 合成 8x 放大的 16x16 图
    rng = np.random.RandomState(7)
    small = np.zeros((16, 16, 4), dtype=np.uint8)
    small[..., 3] = 255
    for _ in range(5):
        c = rng.randint(0, 256, 3)
        y, x = rng.randint(0, 12, 2)
        small[y:y + 4, x:x + 4, :3] = c
    big = np.repeat(np.repeat(small, 8, axis=0), 8, axis=1)

    b, score, _ = grid.auto_block_size(big)
    chk("auto_block_size", b == 8, "got=%d score=%.3f" % (b, score))

    back, info = grid.depixelate(big)
    chk("depixelate 尺寸", back.shape[:2] == (16, 16),
        "got=%s" % str(back.shape[:2]))
    chk("depixelate 无损", np.array_equal(back, small), info["method"])

    # 2. Lab 往返
    from .color import srgb_to_lab
    L, A, B = srgb_to_lab(255, 0, 0)
    chk("Lab 纯红", abs(L - 53.24) < 0.5 and abs(A - 80.09) < 0.5,
        "L=%.2f a=%.2f b=%.2f" % (L, A, B))

    # 3. 量化：颜色数必须收敛
    pal = palette.extract_palette(small, 4)
    chk("抽色 <= 4", 0 < len(pal) <= 4, "got=%d" % len(pal))
    q, _ = palette.quantize(small, 4)
    n_after = _stat(q)["unique_colors"]
    chk("量化后颜色 <= 5", n_after <= 5, "got=%d" % n_after)

    # 4. 切分 + 打包往返
    tiles = sheet.slice_uniform(big, 2, 2)
    chk("切分数量", len(tiles) == 4, "got=%d" % len(tiles))
    sh, meta = sheet.pack_sheet(tiles, cols=2, cell=64)
    chk("拼表尺寸", sh.shape[:2] == (128, 128), "got=%s" % str(sh.shape[:2]))

    # 5. 背景抠除
    plain = np.zeros((32, 32, 4), dtype=np.uint8)
    plain[..., :3] = (20, 200, 40)
    plain[..., 3] = 255
    plain[12:20, 12:20, :3] = (200, 20, 60)
    cut = bgm.remove_background(plain, tol=10, soft=10)
    chk("抠底 角透明", cut[0, 0, 3] == 0)
    chk("抠底 主体保留", cut[15, 15, 3] == 255)

    print("\n".join(log))
    print("\n%s  %d/%d" % ("全部通过" if ok else "有失败",
                           sum(1 for l in log if l.startswith("PASS")), len(log)))
    return 0 if ok else 1


# ---------------- 参数 ----------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="pxkit",
        description="像素素材工具：降分辨率 / 限色 / 切表 / 拼表 / 抠底")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--json", action="store_true", help="输出 JSON")
        sp.add_argument("--force", action="store_true", help="允许覆盖已有文件")
        return sp

    s = common(sub.add_parser("info", help="看图：尺寸/颜色数/底色"))
    s.add_argument("image")
    s.set_defaults(func=cmd_info)

    s = common(sub.add_parser("detect", help="推断放大倍数与真实分辨率"))
    s.add_argument("image")
    s.add_argument("--max-block", type=int, default=64)
    s.set_defaults(func=cmd_detect)

    s = common(sub.add_parser("pixelate", help="还原真实分辨率（降采样）"))
    s.add_argument("image")
    s.add_argument("-o", "--out")
    s.add_argument("--block", type=int, default=0, help="0=自动")
    s.add_argument("--max-block", type=int, default=64)
    s.add_argument("--scale", type=int, default=1, help="输出再放大倍数")
    s.add_argument("--bg", default="none", help="auto / none / #rrggbb")
    s.add_argument("--tol", type=float, default=12.0)
    s.add_argument("--soft", type=float, default=24.0)
    s.add_argument("--despill", action="store_true")
    s.set_defaults(func=cmd_pixelate)

    s = common(sub.add_parser("quantize", help="限色（Lab k-means）"))
    s.add_argument("image")
    s.add_argument("-o", "--out")
    s.add_argument("-c", "--colors", type=int, default=16)
    s.add_argument("--bg", default="auto", help="auto / none / #rrggbb")
    s.add_argument("--scale", type=int, default=1)
    s.add_argument("--palette-out", help="把调色板写成 hex 文本")
    s.set_defaults(func=cmd_quantize)

    s = common(sub.add_parser("clean", help="一站式：抠底+降分辨率+限色"))
    s.add_argument("image")
    s.add_argument("-o", "--out")
    s.add_argument("--block", default="auto", help="auto / 0 / 整数")
    s.add_argument("--max-block", type=int, default=64)
    s.add_argument("-c", "--colors", type=int, default=16)
    s.add_argument("--bg", default="auto", help="auto / none / #rrggbb")
    s.add_argument("--tol", type=float, default=12.0)
    s.add_argument("--soft", type=float, default=24.0)
    s.add_argument("--despill", action="store_true", default=True)
    s.add_argument("--trim", action="store_true")
    s.add_argument("--square", action="store_true")
    s.add_argument("--pad", type=int, default=0)
    s.add_argument("--out-size", help="目标尺寸 如 512x512（整数倍放大）")
    s.set_defaults(func=cmd_clean)

    s = common(sub.add_parser("slice", help="精灵表等分切格"))
    s.add_argument("image")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--rows", type=int, required=True)
    s.add_argument("--cols", type=int, required=True)
    s.add_argument("--order", default="row", choices=["row", "col"])
    s.add_argument("--prefix", default="tile_")
    s.add_argument("--trim", action="store_true")
    s.add_argument("--square", action="store_true")
    s.add_argument("--pad", type=int, default=0)
    s.add_argument("--scale", type=int, default=1)
    s.set_defaults(func=cmd_slice)

    s = common(sub.add_parser("pack", help="多张图拼成精灵表"))
    s.add_argument("src")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--cols", type=int, default=4)
    s.add_argument("--cell", type=int, default=0, help="0=自动取最大")
    s.add_argument("--fit", default="center",
                   choices=["center", "topleft", "bottom"])
    s.add_argument("--gap", type=int, default=0)
    s.add_argument("--trim", action="store_true")
    s.add_argument("--square", action="store_true")
    s.add_argument("--pad", type=int, default=0)
    s.add_argument("--recursive", action="store_true", default=True)
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser("selftest", help="内置自检（不依赖外部文件）")
    s.set_defaults(func=cmd_selftest)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print("找不到文件: %s" % (e.filename or e), file=sys.stderr)
        return 1
    except ValueError as e:
        print("输入有问题: %s" % e, file=sys.stderr)
        return 1
    except PermissionError as e:
        print("没有权限读写: %s" % (e.filename or e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
