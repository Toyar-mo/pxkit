# -*- coding: utf-8 -*-
"""颜色基础工具：打包、sRGB->CIELAB、Lab 距离。

逐行移植自 rika-ai.com 前端 index.pretty.js:22023-22048。
Lab 距离是这套算法「跨色相过渡自然」的根本原因——RGB 欧氏距离会把
暗蓝和亮紫判为接近，Lab 不会。
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "pack", "unpack", "srgb_to_lab", "lab_dist2",
    "srgb_to_lab_array", "hex2rgb", "rgb2hex",
]


def pack(r, g, b) -> int:
    """三通道打包成 24bit 整数，用作颜色哈希键。对应 hc()。"""
    return (int(r) << 16) | (int(g) << 8) | int(b)


def unpack(k: int):
    """24bit 整数拆回 (r,g,b)。对应 Od()。"""
    k = int(k)
    return ((k >> 16) & 255, (k >> 8) & 255, k & 255)


def _srgb_to_linear(c):
    """sRGB 反 gamma。标量或 ndarray 均可。"""
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92,
                    ((c + 0.055) / 1.055) ** 2.4)


def srgb_to_lab(r, g, b):
    """单个颜色 sRGB -> CIELAB(D65)。对应 hr()。返回 (L, a, b) 三元组。"""
    u = float(_srgb_to_linear(r))
    d = float(_srgb_to_linear(g))
    f = float(_srgb_to_linear(b))
    xr = (u * 0.4124564 + d * 0.3575761 + f * 0.1804375) / 0.95047
    yr = u * 0.2126729 + d * 0.7151522 + f * 0.0721750
    zr = (u * 0.0193339 + d * 0.1191920 + f * 0.9503041) / 1.08883

    def f_(t):
        return t ** (1.0 / 3.0) if t > 0.008856451679 \
            else (903.296296296 * t + 16) / 116.0

    fx, fy, fz = f_(xr), f_(yr), f_(zr)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def srgb_to_lab_array(rgb: np.ndarray) -> np.ndarray:
    """批量版：(N,3) uint8 -> (N,3) float64 Lab。向量化，比逐点快得多。"""
    rgb = np.asarray(rgb, dtype=np.float64)
    u = _srgb_to_linear(rgb[:, 0])
    d = _srgb_to_linear(rgb[:, 1])
    f = _srgb_to_linear(rgb[:, 2])
    xr = (u * 0.4124564 + d * 0.3575761 + f * 0.1804375) / 0.95047
    yr = u * 0.2126729 + d * 0.7151522 + f * 0.0721750
    zr = (u * 0.0193339 + d * 0.1191920 + f * 0.9503041) / 1.08883

    def f_(t):
        return np.where(t > 0.008856451679, np.cbrt(t),
                        (903.296296296 * t + 16) / 116.0)

    fx, fy, fz = f_(xr), f_(yr), f_(zr)
    return np.stack([116.0 * fy - 16.0,
                     500.0 * (fx - fy),
                     200.0 * (fy - fz)], axis=1)


def lab_dist2(a, b):
    """Lab 空间欧氏距离平方。对应 vl()。"""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def hex2rgb(s: str):
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def rgb2hex(c) -> str:
    return "#%02x%02x%02x" % (int(c[0]), int(c[1]), int(c[2]))
