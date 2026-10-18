# -*- coding: utf-8 -*-
"""精灵表切分与打包。

移植自 rika-ai.com index.pretty.js 的 `mw`（等分切格）与 `Tg`（4 列拼表）。

## 原版 mw 的取舍
按**均匀网格**切，不做内容识别（不找连通域、不对齐包围盒）。
好处：序号稳定、速度恒定、无参数。
坏处：留白多、图元大小不一。
这两个缺点原版都没解决，本工具额外提供 `trim=True` 用透明度包围盒裁边，
以及 `align` 选项把裁边后的图元统一居中/贴底，这样拼回去才整齐。
"""
from __future__ import annotations

import numpy as np

__all__ = ["slice_uniform", "bbox_of", "trim_rgba", "pack_sheet",
           "resize_nearest"]


def slice_uniform(rgba: np.ndarray, rows: int, cols: int,
                  order: str = "row"):
    """等分成 rows×cols 格。对应 mw()。

    order: "row"（先行后列，原版行为）或 "col"。
    尺寸不能整除时，多余的边缘像素分配给前面的格（原版 nth-child 语义）。
    """
    h, w = rgba.shape[:2]
    ys = np.linspace(0, h, rows + 1).round().astype(int)
    xs = np.linspace(0, w, cols + 1).round().astype(int)
    out = []
    if order == "row":
        coords = [(r, c) for r in range(rows) for c in range(cols)]
    else:
        coords = [(r, c) for c in range(cols) for r in range(rows)]
    for r, c in coords:
        tile = rgba[ys[r]:ys[r + 1], xs[c]:xs[c + 1]]
        out.append(((r, c), tile))
    return out


def bbox_of(rgba: np.ndarray, alpha_thresh: int = 1):
    """不透明像素的包围盒 (x0,y0,x1,y1)；全透明返回 None。"""
    a = rgba[..., 3] > alpha_thresh
    if not a.any():
        return None
    ys = np.where(a.any(axis=1))[0]
    xs = np.where(a.any(axis=0))[0]
    return (int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1)


def trim_rgba(rgba: np.ndarray, padding: int = 0,
              square: bool = False, alpha_thresh: int = 1):
    """按包围盒裁边；square=True 时补成正方形（居中）。

    像素图**绝不能**用双线性缩放裁切，所以这里只做整数像素的切片与补边。
    """
    bb = bbox_of(rgba, alpha_thresh)
    if bb is None:
        return rgba
    x0, y0, x1, y1 = bb
    x0 = max(0, x0 - padding)
    y0 = max(0, y0 - padding)
    x1 = min(rgba.shape[1], x1 + padding)
    y1 = min(rgba.shape[0], y1 + padding)
    t = rgba[y0:y1, x0:x1]
    if not square:
        return t
    s = max(t.shape[0], t.shape[1])
    canvas = np.zeros((s, s, 4), dtype=np.uint8)
    oy = (s - t.shape[0]) // 2
    ox = (s - t.shape[1]) // 2
    canvas[oy:oy + t.shape[0], ox:ox + t.shape[1]] = t
    return canvas


def resize_nearest(rgba: np.ndarray, scale: int) -> np.ndarray:
    """最近邻整数放大。像素图只能用最近邻，任何插值都会糊边。"""
    if scale <= 1:
        return rgba.copy()
    return np.repeat(np.repeat(rgba, scale, axis=0), scale, axis=1)


def pack_sheet(tiles, cols: int = 4, cell=None, fit: str = "center",
               bg=(0, 0, 0, 0), gap: int = 0):
    """把一堆图元拼成精灵表。对应 Tg()（原版固定 4 列）。

    tiles : [(name, rgba), ...]
    cell  : 每格边长；None 时取所有图元的最大边长
    fit   : center / topleft / bottom（贴底，角色类常用）
    返回 (sheet_rgba, meta)，meta 含每格行列与在表中的像素矩形。
    """
    if not tiles:
        raise ValueError("没有可打包的图元")
    if cell is None:
        cell = max(max(t.shape[0], t.shape[1]) for _, t in tiles)
    for _, t in tiles:
        if t.shape[0] > cell or t.shape[1] > cell:
            raise ValueError(
                "图元 %dx%d 超过格大小 %d，请调大 cell" % (t.shape[1], t.shape[0], cell))
    n = len(tiles)
    rows = (n + cols - 1) // cols
    W = cols * cell + (cols - 1) * gap
    H = rows * cell + (rows - 1) * gap
    sheet = np.zeros((H, W, 4), dtype=np.uint8)
    sheet[:, :] = np.array(bg, dtype=np.uint8)

    meta = []
    for i, (name, t) in enumerate(tiles):
        r, c = divmod(i, cols)
        ox = c * (cell + gap)
        oy = r * (cell + gap)
        if fit == "center":
            px = ox + (cell - t.shape[1]) // 2
            py = oy + (cell - t.shape[0]) // 2
        elif fit == "bottom":
            px = ox + (cell - t.shape[1]) // 2
            py = oy + (cell - t.shape[0])
        else:
            px, py = ox, oy
        sheet[py:py + t.shape[0], px:px + t.shape[1]] = t
        meta.append({"name": name, "row": r, "col": c,
                     "rect": [int(px), int(py), int(t.shape[1]), int(t.shape[0])],
                     "cell_rect": [int(ox), int(oy), int(cell), int(cell)]})
    return sheet, {"cols": cols, "rows": rows, "cell": int(cell),
                   "sheet_size": [int(W), int(H)], "tiles": meta}
