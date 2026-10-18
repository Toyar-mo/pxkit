# -*- coding: utf-8 -*-
"""像素网格检测与降采样。

移植自 rika-ai.com index.pretty.js:22049-22180 的 `p` / `x` / `y` / `fw`，
并补上了原版缺失的「自动推断块大小」。

## 原版 fw() 的真实语义（已实测验证）
fw(image, gridSize) 不是「精灵表切格」，而是：
  预测块大小 -> 算亮度边缘剖面 -> 在预期位置附近找边缘峰 -> 用峰作为网格线
  -> 每格取中心像素 -> 得到「真实分辨率」的图

实测结论（见 _re_rika/_spike/grid_report.txt）：
- 当图像确实是 gridSize 倍整数放大时，切点严格落在倍数上，输出干净
- 当图像不是整数倍放大时，切点会漂移 -> 输出被破坏
所以原版**必须外部告诉它块大小**，它自己不会判断。这是它的缺陷，
本工具用 auto_block_size() 补上这一环。

## 本工具的策略
1. `auto_block_size()`：块内一致性检测，精确、快、无参数
2. `detect_block_size()`：边缘剖面法，作为一致性检测失败时的兜底
   （能容忍轻微非整数倍、抗噪）
3. `depixelate()`：完整流程，先定块大小再降采样
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "luma_alpha", "edge_profiles", "find_cuts",
    "auto_block_size", "detect_block_size", "depixelate",
]


def luma_alpha(rgba: np.ndarray) -> np.ndarray:
    """亮度 + 透明度加权，转成「边缘强度」图。对应 p()。

    原式：v = 0.299r+0.587g+0.114b，  (v/255)*0.7 + (1-a/255)*0.3
    含义：越不透明、越亮的像素边缘贡献越大；完全透明处边缘权重低
          （防止抠图残留的边缘噪点干扰网格判断）。
    """
    a = rgba[..., 3].astype(np.float64) / 255.0
    lum = (rgba[..., 0].astype(np.float64) * 0.299
           + rgba[..., 1].astype(np.float64) * 0.587
           + rgba[..., 2].astype(np.float64) * 0.114) / 255.0
    return lum * 0.7 + (1.0 - a) * 0.3


def edge_profiles(rgba: np.ndarray):
    """算逐列 / 逐行的相邻像素差分强度。对应 x()。

    返回 (col_profile, row_profile)，长度为 width / height。
    """
    m = luma_alpha(rgba)
    h, w = m.shape
    col = np.zeros(w)
    row = np.zeros(h)
    if w > 1:
        col[1:] = np.abs(np.diff(m, axis=1)).sum(axis=0)
    if h > 1:
        row[1:] = np.abs(np.diff(m, axis=0)).sum(axis=1)
    return col, row


def find_cuts(profile: np.ndarray, expected: int, tol: float = 0.32) -> list:
    """在预期等分位置附近找边缘峰当作切点。对应 y()。

    expected : 预期的块大小（像素）
    tol      : 搜索半宽 = max(1, round(expected*tol))

    与原版一致的三个细节：
    - 首尾强制包含 0 与 len(profile)
    - 从 expected 起按步长跳跃，所以在非整数倍时会累积漂移
    - 没有峰时退回预期位置
    """
    n = len(profile)
    if expected < 1 or n < 2:
        return [0, n]
    band = max(1, int(round(expected * tol)))
    cuts = [0]
    pos = expected
    while pos < n:
        lo = max(0, pos - band)
        hi = min(n - 1, pos + band)
        best_i, best_v = pos, -1.0
        if hi >= lo:
            seg = profile[lo:hi + 1]
            k = int(np.argmax(seg))
            best_v = float(seg[k])
            best_i = lo + k
        # 峰值太弱 -> 退回理论位置（原版同样行为）
        if best_v <= 0:
            best_i = pos
        if best_i > cuts[-1]:
            cuts.append(int(best_i))
        pos += expected
    if cuts[-1] < n:
        cuts.append(n)
    return cuts


def _block_units(rgba: np.ndarray, b: int) -> float:
    """块内一致性：每个像素与所在块左上角的相同比例（只看不透明像素）。"""
    h, w = rgba.shape[:2]
    if b < 1 or h < b or w < b:
        return 0.0
    nh, nw = h // b, w // b
    core = rgba[:nh * b, :nw * b]
    ref = core[::b, ::b]                                  # (nh,nw,4)
    ref = np.repeat(np.repeat(ref, b, axis=0), b, axis=1)  # 铺回原尺寸
    diff = np.abs(core.astype(np.int16) - ref.astype(np.int16)).max(axis=2)
    mask = core[..., 3] >= 128
    if mask.sum() == 0:
        return 0.0
    return float((diff[mask] == 0).mean())


def auto_block_size(rgba: np.ndarray, max_block: int = 64,
                    threshold: float = 0.985) -> tuple:
    """自动推断放大倍数。返回 (block, score, candidates)。

    原理：整数倍放大的像素图，每个 bxb 块内部**完全**同色。
    因此对每个候选 b 算块内一致率，取「一致率达标的最大 b」。
    之所以取最大而不是最小：若图是 8 倍放大，则 4、2、1 也一致，
    但只有 8 能还原真实分辨率。

    candidates 里给出每一档的分数，便于人工复核。
    """
    h, w = rgba.shape[:2]
    hi = min(max_block, h, w)
    cands = []
    for b in range(2, hi + 1):
        cands.append((b, _block_units(rgba, b)))
    ok = [b for b, s in cands if s >= threshold]
    if ok:
        b = max(ok)
        return b, dict(cands)[b], cands
    return 1, _block_units(rgba, 1) if hi >= 1 else 0.0, cands


def detect_block_size(rgba: np.ndarray, max_block: int = 64) -> int:
    """边缘剖面法兜底：找使边缘能量最集中于网格线上的块大小。

    思路：对候选 b，用 find_cuts 定切点，累计切点处的剖面值 / 全图剖面总和。
    集中度越高说明这个 b 越符合图的固有网格。
    """
    col, row = edge_profiles(rgba)
    if col.sum() <= 0 and row.sum() <= 0:
        return 1
    best_b, best_score = 1, -1.0
    for b in range(2, min(max_block, rgba.shape[0], rgba.shape[1]) + 1):
        c = find_cuts(col, b)
        r = find_cuts(row, b)
        sc = 0.0
        if col.sum() > 0:
            sc += sum(col[i] for i in c[1:-1] if 0 <= i < len(col)) / col.sum()
        if row.sum() > 0:
            sc += sum(row[i] for i in r[1:-1] if 0 <= i < len(row)) / row.sum()
        ncuts = max(1, (len(c) - 2) + (len(r) - 2))
        score = sc / ncuts * 1000.0    # 归一化到「每个切点的平均集中度」
        if score > best_score:
            best_score, best_b = score, b
    return best_b


def depixelate(rgba: np.ndarray, block: int = 0, max_block: int = 64,
               tol: float = 0.32, fallback: bool = True):
    """把放大过的像素图还原到真实分辨率。

    block=0 表示自动检测。返回 (out_rgba, info)。
    info 含 block / method / score / 尺寸变化，便于写进报告。
    """
    h, w = rgba.shape[:2]
    info = {"src_size": [w, h], "block": 1, "method": "none", "score": 1.0}

    if block and block > 1:
        b, method, score = block, "user", 1.0
    else:
        b, score, _cands = auto_block_size(rgba, max_block)
        method = "block-uniformity"
        if b <= 1 and fallback:
            b = detect_block_size(rgba, max_block)
            method = "edge-profile"
            score = 0.0
    info.update(block=b, method=method, score=round(float(score), 4))

    if b <= 1:
        info["out_size"] = [w, h]
        return rgba, info

    # 精确整数倍：直接取每块左上角（等价于取中心，块内同色）
    nh, nw = h // b, w // b
    if nh >= 1 and nw >= 1 and (h % b == 0) and (w % b == 0):
        out = rgba[::b, ::b][:nh, :nw]
        info["method"] += "+exact"
    else:
        # 兜底：走边缘剖面切点，容忍非整数倍
        col, row = edge_profiles(rgba)
        ys = find_cuts(row, b, tol)
        xs = find_cuts(col, b, tol)
        cells = []
        for i in range(len(ys) - 1):
            line = []
            for j in range(len(xs) - 1):
                y0, y1, x0, x1 = ys[i], ys[i + 1], xs[j], xs[j + 1]
                if y1 <= y0 or x1 <= x0:
                    line.append(np.zeros(4, dtype=np.uint8))
                else:
                    line.append(rgba[(y0 + y1) // 2, (x0 + x1) // 2])
            cells.append(line)
        out = np.array(cells, dtype=np.uint8)
        info["method"] += "+profile"

    info["out_size"] = [int(out.shape[1]), int(out.shape[0])]
    return out, info
