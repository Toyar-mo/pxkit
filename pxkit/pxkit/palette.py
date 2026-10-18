# -*- coding: utf-8 -*-
"""调色板提取与量化 —— 这是这套算法最有价值的部分。

移植自 rika-ai.com index.pretty.js 的 `uw` / `eh` / `fh` / `d0`。

## 四个设计取舍（搬过来就是因为这四点）
1. 距离算在 **CIELAB**：人眼感知空间。RGB 欧氏距离会把深蓝和亮紫判为
   接近，Lab 不会，所以跨色相过渡不会糊。
2. 权重用 **count^0.7**：不是线性 count。线性权重下大面积纯色背景会
   独占调色板，细节色一个都留不下。
3. 质心回映射到**图里真实存在的颜色**：不产生图像里没有的新颜色，
   替换后不会出现「脏色」（这点和普通 k-means 量化有本质差别）。
4. 输出按 **L 分量排序**：调色板可读、可复现、可跨图对比。

## 与原版的差异
- 向量化：用权重矩阵一次性算所有点对所有质心的距离，不再双重 for 循环
- k-means++ 的采样改为确定性（原版是确定性 argmax，这里保持）
- 增加 `preserve_bg`：可选保留透明 / 背景色不被量化
"""
from __future__ import annotations

import numpy as np

from .color import pack, unpack, srgb_to_lab_array, srgb_to_lab, lab_dist2

__all__ = [
    "color_histogram", "extract_palette", "apply_palette",
    "quantize", "palette_report",
]


def color_histogram(rgba: np.ndarray, bg_rgb=None):
    """统计颜色直方图。alpha<128 与背景色跳过。返回 (keys, counts)。

    keys 按打包整数升序 —— 与原版一致，保证可复现。
    """
    flat = rgba.reshape(-1, 4)
    keep = flat[flat[:, 3] >= 128]
    if keep.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    keys = (keep[:, 0].astype(np.int64) << 16) | \
           (keep[:, 1].astype(np.int64) << 8) | \
           keep[:, 2].astype(np.int64)
    if bg_rgb is not None:
        bgk = pack(*bg_rgb)
        keys = keys[keys != bgk]
    uk, cnt = np.unique(keys, return_counts=True)
    return uk.astype(np.int64), cnt.astype(np.int64)


def extract_palette(rgba: np.ndarray, want: int, bg_rgb=None) -> list:
    """从图中抽出 ≤want 个代表色。对应 uw()。返回按 L 排序的 [(r,g,b),...]。

    流程：直方图 -> count^0.7 加权 -> k-means++ 初始化 -> 20 轮 Lloyd
          -> 质心回映射到真实色 -> 强制保留最高频色 -> 去重 + 按 L 排序
    """
    keys, counts = color_histogram(rgba, bg_rgb)
    n = len(keys)
    if n == 0:
        return []
    want = max(1, int(round(want)))
    m = min(want, n)

    rgb = np.stack([(keys >> 16) & 255, (keys >> 8) & 255, keys & 255], axis=1)
    labs = srgb_to_lab_array(rgb)              # (n,3)
    weights = counts.astype(np.float64) ** 0.7  # 关键：0.7 次幂而非线性

    # --- k-means++ 式确定性初始化 ---
    top = int(np.argmax(counts))
    centers = labs[top][None, :].copy()        # (1,3)
    d2 = ((labs - centers[0]) ** 2).sum(axis=1)  # (n,)
    while centers.shape[0] < m:
        score = d2 * np.sqrt(weights)
        j = int(np.argmax(score))
        newc = labs[j]
        centers = np.vstack([centers, newc])
        d2 = np.minimum(d2, ((labs - newc) ** 2).sum(axis=1))

    # --- Lloyd 迭代（权重加权质心，在 Lab 空间）---
    for _ in range(20):
        dist = ((labs[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)  # (n,m)
        assign = np.argmin(dist, axis=1)
        new_centers = centers.copy()
        for j in range(m):
            sel = assign == j
            if sel.any():
                w = weights[sel]
                new_centers[j] = (labs[sel] * w[:, None]).sum(axis=0) / w.sum()
        if np.allclose(new_centers, centers, atol=1e-9):
            centers = new_centers
            break
        centers = new_centers

    # --- 质心回映射到真实存在的颜色 ---
    dist = ((labs[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    picked = []
    for j in range(m):
        i = int(np.argmin(dist[:, j]))
        k = int(keys[i])
        if k not in picked:
            picked.append(k)

    # --- 强制保留最高频色（原版同样处理）---
    top_key = int(keys[top])
    if top_key not in picked:
        tl = srgb_to_lab(*unpack(top_key))
        best, bd = 0, float("inf")
        for idx, k in enumerate(picked):
            d = lab_dist2(tl, srgb_to_lab(*unpack(k)))
            if d < bd:
                bd, best = d, idx
        picked[best] = top_key

    uniq = sorted(set(picked))
    uniq.sort(key=lambda k: srgb_to_lab(*unpack(k))[0])   # 按 L 升序
    return [unpack(k) for k in uniq]


def apply_palette(rgba: np.ndarray, palette: list, bg_rgb=None) -> np.ndarray:
    """把每个像素替换为调色板中 Lab 最近的颜色。对应 fh()。

    实现上先建「唯一色 -> 调色板索引」查找表，再整体索引，避免逐像素循环。
    """
    if not palette:
        return rgba.copy()
    pal = np.array(palette, dtype=np.uint8)
    pal_lab = srgb_to_lab_array(pal)

    rgb = rgba[..., :3].reshape(-1, 3)
    keys = (rgb[:, 0].astype(np.int64) << 16) | \
           (rgb[:, 1].astype(np.int64) << 8) | \
           rgb[:, 2].astype(np.int64)
    uk, inv = np.unique(keys, return_inverse=True)

    urgb = np.stack([(uk >> 16) & 255, (uk >> 8) & 255, uk & 255], axis=1)
    ulab = srgb_to_lab_array(urgb.astype(np.uint8))
    d = ((ulab[:, None, :] - pal_lab[None, :, :]) ** 2).sum(axis=2)  # (U,P)
    nearest = np.argmin(d, axis=1)

    out = pal[nearest][inv].reshape(rgba[..., :3].shape).astype(np.uint8)
    res = rgba.copy()
    res[..., :3] = out

    # 透明像素与背景色不参与替换（与原版一致）
    if bg_rgb is not None:
        bgk = pack(*bg_rgb)
        isbg = (keys == bgk).reshape(rgba.shape[0], rgba.shape[1])
        res[..., :3][isbg] = rgba[..., :3][isbg]
    alpha = rgba[..., 3] < 128
    res[..., :3][alpha] = rgba[..., :3][alpha]
    return res


def quantize(rgba: np.ndarray, colors: int, bg_rgb=None):
    """一步到位：抽色 + 应用。对应 d0 = fh(eh(...))。返回 (out, palette)。"""
    pal = extract_palette(rgba, colors, bg_rgb)
    return apply_palette(rgba, pal, bg_rgb), pal


def palette_report(palette: list) -> list:
    """把调色板转成可写进 JSON / 文本报告的记录（含 Lab 与 hex）。"""
    rows = []
    for r, g, b in palette:
        L, A, B = srgb_to_lab(r, g, b)
        rows.append({
            "hex": "#%02x%02x%02x" % (r, g, b),
            "rgb": [int(r), int(g), int(b)],
            "lab": [round(L, 2), round(A, 2), round(B, 2)],
        })
    return rows
