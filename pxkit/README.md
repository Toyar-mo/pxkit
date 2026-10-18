# pxkit — 像素素材工具

把任意像素画整理成能直接进引擎的规格：识别放大倍数、还原真实分辨率、
收敛颜色、抠掉底色、切格、拼表。

**本地运行，不联网，不依赖任何在线服务。**

## 安装

需要 Python 3.9+ 与 numpy、Pillow：

```powershell
python -m pip install numpy pillow
```

无需安装本包，在 `pxkit\` 目录下直接 `python -m pxkit <命令>` 即可。

## 命令

| 命令 | 作用 |
|---|---|
| `info` | 看图：尺寸、颜色数、不透明占比、四角底色 |
| `detect` | 推断放大倍数与真实分辨率 |
| `pixelate` | 还原到真实分辨率（降采样），可再放大预览 |
| `quantize` | 限色，导出调色板 |
| `clean` | 一站式：抠底 → 去色边 → 降分辨率 → 限色 |
| `slice` | 精灵表等分切格 |
| `pack` | 多张图拼成一张精灵表 + 索引 JSON |
| `selftest` | 内置自检，不依赖外部文件 |

全部命令支持 `--json`（结构化输出）与 `--force`（允许覆盖）。

## 常用流程

**把一张 AI 生成的图变成可用素材：**

```powershell
python -m pxkit detect hero.png                  # 先看推断对不对
python -m pxkit clean hero.png -o hero_px.png -c 16 --trim
```

**拆一张精灵表再重新排布：**

```powershell
python -m pxkit slice walk.png -o tiles --rows 4 --cols 4 --trim --square
python -m pxkit pack tiles -o walk_sheet.png --cols 4 --fit bottom
```

**统一多张图的颜色风格：**

```powershell
python -m pxkit quantize a.png -c 24 --palette-out pal.txt
# 之后每张都按同一套调色板收敛（P1 功能，当前版本调色板文件为只写）
```

## 原理要点

三个决定效果的关键设计，改代码时别动：

1. **距离算在 CIELAB，不在 RGB。** RGB 欧氏距离会把深蓝和亮紫判为接近，
   跨色相过渡会糊成一团；Lab 是感知均匀空间，不会。

2. **调色板权重用 `count^0.7`，不是线性 `count`。**
   线性权重下大面积纯色背景会独占调色板，细节色一个都留不下。

3. **质心回映射到原图真实存在的颜色。**
   不产生图里没有的新颜色，替换后不会出现脏色。这是和普通 k-means 量化的本质差别。

4. **像素图只能用最近邻缩放。** 任何插值（双线性/双三次/lanczos）都会把
   硬边糊掉，像素感直接毁掉。代码里所有缩放都是 `resize_nearest`。

## 已知局限

- **非整数倍放大的图**：倍数检测会失败，此时工具报错而不是静默输出坏图。
  这是有意为之——坏图比报错更难排查。
- **抠底是颜色键控，不是 AI 抠图**。纯色底能抠干净，复杂背景无能为力。
- **限色会损失细节**，默认开启 16 色，可用 `-c 0` 关闭。

## 目录

```
pxkit/pxkit/
  color.py      颜色打包 / sRGB→Lab / Lab 距离
  grid.py       亮度剖面 / 块大小检测 / 降采样
  palette.py    调色板提取 / 量化
  sheet.py      切格 / 拼表 / 裁边 / 最近邻放大
  background.py 四角底色检测 / 键控抠底 / 去色边
  imgio.py      读写与统一 (H,W,4) RGBA 表示
  cli.py        命令行
```

内部数据一律 `(H, W, 4)` uint8 RGBA 数组，算法层不碰文件 IO。
