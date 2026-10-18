# -*- coding: utf-8 -*-
"""图像读写与统一的数据表示。

内部统一用 RGBA ndarray (H,W,4) uint8。所有算法都按这个约定工作，
所以任何格式进来先转 RGBA，任何结果出去再按需转。
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image

__all__ = ["load_rgba", "save_rgba", "list_images", "ensure_dir"]

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tga"}


def load_rgba(path) -> np.ndarray:
    """读成 (H,W,4) uint8。JPG 等无 alpha 的补 255。"""
    im = Image.open(path)
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    return np.asarray(im, dtype=np.uint8).copy()


def save_rgba(rgba: np.ndarray, path, scale: int = 1, bg=None):
    """写盘。scale>1 时用最近邻整数放大（预览用，不改变数据）。

    bg=(r,g,b) 时合成到该底色上并按此底色存 RGB；bg=None 保持 RGBA。
    """
    if scale and scale > 1:
        rgba = np.repeat(np.repeat(rgba, scale, axis=0), scale, axis=1)
    if bg is not None:
        base = np.zeros_like(rgba[..., :3])
        base[:, :] = np.array(bg, dtype=np.uint8)
        a = rgba[..., 3:4].astype(np.float64) / 255.0
        rgb = (rgba[..., :3].astype(np.float64) * a
               + base.astype(np.float64) * (1 - a))
        im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    else:
        im = Image.fromarray(rgba, "RGBA")
    p = Path(path)
    ensure_dir(p.parent)
    im.save(p)
    return str(p)


def list_images(root, recursive: bool = True):
    """收集目录下所有图片，按文件名自然序（img2 < img10）。"""
    root = Path(root)
    if root.is_file():
        return [root]
    it = root.rglob("*") if recursive else root.glob("*")
    files = [p for p in it if p.is_file() and p.suffix.lower() in IMG_EXT]

    def key(p: Path):
        import re
        stem = p.stem
        parts = re.split(r"(\d+)", stem)
        return [int(s) if s.isdigit() else s.lower() for s in parts]

    return sorted(files, key=key)


def ensure_dir(p):
    if p and str(p) not in ("", "."):
        os.makedirs(p, exist_ok=True)
