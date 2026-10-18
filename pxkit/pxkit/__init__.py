# -*- coding: utf-8 -*-
"""pxkit —— 像素素材工具。

能力：
  - 还原放大倍数、降回真实分辨率（grid.depixelate）
  - Lab 空间调色板提取与限色（palette）
  - 颜色键控抠底、去色边（background）
  - 精灵表等分切格、拼表、裁边（sheet）

内部统一用 (H,W,4) uint8 RGBA 数组。
"""
from . import background, color, grid, imgio, palette, sheet

__version__ = "0.1.0"
__all__ = ["background", "color", "grid", "imgio", "palette", "sheet",
           "__version__"]
