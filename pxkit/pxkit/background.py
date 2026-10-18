# -*- coding: utf-8 -*-
"""背景检测 / 抠图 / 去色边。

移植自 rika-ai.com index.pretty.js:22276-22309 的四角取样 + 距离容差抠图，
并补上了原版没有的「去色边」（despill）——抠图后边缘常留半透明底色调，
放大成像素图后会看到一圈脏边。
"""
from __future__ import annotations

import numpy as np

from .color import pack

__all__ = ["corner_background", "remove_background", "despill_edges",
           "outline_alpha"]


def corner_background(rgba: np.ndarray, radius: int = 1):
    """取四角 radius×radius 区域的中位数作为背景色。对应 22276-22309。

    用中位数而非均值：角落里如果有小图元溢出，中位数不会被带偏。
    返回 (r,g,b, a_mean) —— a_mean 用来判断底图是否本身带透明。
    """
    h, w = rgba.shape[:2]
    r = max(1, min(radius, h // 2 or 1, w // 2 or 1))
    patches = [rgba[0:r, 0:r], rgba[0:r, w - r:w],
               rgba[h - r:h, 0:r], rgba[h - r:h, w - r:w]]
    px = np.concatenate([p.reshape(-1, 4) for p in patches], axis=0)
    med = np.median(px[:, :3], axis=0)
    a_mean = float(px[:, 3].mean())
    return (int(round(med[0])), int(round(med[1])), int(round(med[2])), a_mean)


def remove_background(rgba: np.ndarray, bg=None, tol: float = 12.0,
                      soft: float = 24.0, keep_larger_than: int = 0):
    """按颜色容差把背景变透明。

    bg  : None -> 自动四角取样；False -> 不做；(r,g,b) -> 指定
    tol : 硬容差，距离 <= tol 直接全透明
    soft: 软容差上界，tol~soft 之间按线性做半透明羽化，避免锯齿

    这是「颜色键控」，不是 AI 抠图。对纯色底足够，对复杂底无能为力
    —— 原版也一样，它只做颜色键控。
    """
    if bg is False or bg is None and False:
        return rgba.copy()
    if bg is None:
        r, g, b, a_mean = corner_background(rgba)
        if a_mean < 250:
            # 底图本来就有透明通道，没必要键控
            return rgba.copy()
        bg = (r, g, b)

    out = rgba.copy()
    diff = rgba[..., :3].astype(np.float64) - np.array(bg, dtype=np.float64)
    dist = np.sqrt((diff ** 2).sum(axis=2))
    alpha = out[..., 3].astype(np.float64)

    hard = dist <= tol
    fade = (dist > tol) & (dist <= soft)
    new_a = alpha.copy()
    new_a[hard] = 0.0
    if soft > tol:
        k = (dist[fade] - tol) / (soft - tol)
        new_a[fade] = alpha[fade] * k
    out[..., 3] = np.clip(new_a, 0, 255).astype(np.uint8)

    if keep_larger_than > 0:
        out = _drop_small_islands(out, keep_larger_than)
    return out


def despill_edges(rgba: np.ndarray, iterations: int = 1) -> np.ndarray:
    """去掉半透明边缘上残留的背景色。

    做法：对每个半透明像素，用其不透明邻居的颜色替换 RGB（保 alpha 不变）。
    这一步在量化之前做很重要 —— 否则背景色会污染调色板。
    """
    out = rgba.copy()
    h, w = out.shape[:2]
    for _ in range(max(1, iterations)):
        a = out[..., 3]
        semi = (a > 0) & (a < 250)
        if not semi.any():
            break
        solid = a >= 250
        src = out.copy()
        for y, x in zip(*np.where(semi)):
            nb = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and solid[yy, xx]:
                        nb.append(src[yy, xx, :3].astype(np.float64))
            if nb:
                out[y, x, :3] = np.round(np.mean(nb, axis=0)).astype(np.uint8)
    return out


def _drop_small_islands(rgba: np.ndarray, min_pixels: int) -> np.ndarray:
    """去掉小于 min_pixels 的不透明连通块（4 邻接）。用于清掉抠图残渣。"""
    from collections import deque
    h, w = rgba.shape[:2]
    mask = rgba[..., 3] >= 8
    seen = np.zeros((h, w), dtype=bool)
    out = rgba.copy()
    for y0 in range(h):
        for x0 in range(w):
            if not mask[y0, x0] or seen[y0, x0]:
                continue
            q = deque([(y0, x0)])
            seen[y0, x0] = True
            comp = []
            while q:
                y, x = q.popleft()
                comp.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        q.append((yy, xx))
            if len(comp) < min_pixels:
                for y, x in comp:
                    out[y, x, 3] = 0
    return out


def outline_alpha(rgba: np.ndarray) -> np.ndarray:
    """生成 1px 描边用的 alpha 掩膜（外扩一圈）。"""
    a = (rgba[..., 3] >= 128).astype(np.uint8)
    h, w = a.shape
    pad = np.zeros((h + 2, w + 2), dtype=np.uint8)
    pad[1:-1, 1:-1] = a
    dil = np.zeros_like(a)
    for dy in range(3):
        for dx in range(3):
            dil |= pad[dy:dy + h, dx:dx + w]
    return (dil * 255).astype(np.uint8)
