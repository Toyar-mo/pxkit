# pxkit · 像素素材工具

把 AI 生成的「伪像素图」还原成可用的真像素素材：检测放大倍数、还原真实分辨率、限色到调色板、抠底、切分、拼表。

纯本地运行，不联网、不上传图片。算法从 [rika-ai.com](https://rika-ai.com) 前端逆向复刻并逐项实测验证，详见 [`像素素材工具-方案.md`](像素素材工具-方案.md)。

## 两个入口

| 入口 | 命令 | 适合 |
|---|---|---|
| **命令行** `pxkit` | `python -m pxkit ...` | 批量处理、挂进构建脚本 |
| **Web 界面** `pxweb` | `python pxweb/server.py` | 单张精修、肉眼对比 |

## 环境

- Python 3.10+
- `numpy`、`Pillow`

```bash
pip install numpy pillow
```

## 快速开始 · 命令行

在 `pxkit/` 目录下执行：

```bash
# 看一张图的尺寸、像素倍数、颜色数
python -m pxkit info 图片.png

# 自检（10 项）
python -m pxkit selftest

# 一步到位：检测倍数 → 还原分辨率 → 抠底 → 限色
python -m pxkit clean 图片.png -o out/ --colors 16

# 只检测倍数
python -m pxkit detect 图片.png --json

# 还原真实分辨率（自动判定倍数）
python -m pxkit pixelate 图片.png -o out/px.png

# 限色到指定调色板
python -m pxkit quantize 图片.png -o out/q.png --colors 16

# 抠掉纯色背景
python -m pxkit clean 图片.png -o out/ --colors 0 --bg none

# 精灵表切分 → 拼回去
python -m pxkit slice sheet.png -o out/tiles/ --rows 4 --cols 4
python -m pxkit pack out/tiles/ -o out/repacked.png --cols 4
```

## 快速开始 · Web 界面

```bash
cd pxweb
python server.py            # 自动打开 http://127.0.0.1:8765
python server.py --port 9000 --no-open
```

界面仿 rika-ai.com 的视觉语言（深紫底 + 金色描边 + 像素字体），操作顺序：上传 → 检测 → 还原分辨率 → 限色 → 抠底 → 切分 / 拼表 → 导出 PNG。

## 核心能力

| 能力 | 说明 |
|---|---|
| **倍数检测** | 用亮度+透明度的边缘剖面找周期，得出原图被放大几倍 |
| **还原分辨率** | 按检测到的块大小取样，回到原始像素网格 |
| **调色板提取** | Lab 空间 k-means++，权重 `count^0.7`，质心回映射到真实存在的源色 |
| **限色** | 逐像素映射到最近调色板色，Lab 距离 |
| **抠底** | 四角取色 + 容差判断 + 边缘去溢色 + 小连通块清理 |
| **切分 / 拼表** | 均匀网格切分，按列数重排拼回 |

## 目录

```
pxkit/          命令行库
  pxkit/
    grid.py       边缘剖面、倍数检测、去像素化
    palette.py    调色板提取与限色
    background.py 抠底、去溢色、孤立块清理
    sheet.py      切分、包围盒、拼表、最近邻缩放
    color.py      sRGB → Lab、色距
    imgio.py      读写与目录遍历
    cli.py        子命令
    gui.py        Tkinter 桌面版（可选）
  README.md
pxweb/          Web 界面
  server.py       纯标准库 HTTP 服务 + JSON API
  static/         页面、样式、脚本、字体
像素素材工具-方案.md   定位、能力清单、选型对比、里程碑
```

## 已知局限

- 只处理「整数倍最近邻放大」的伪像素图。真正的抗锯齿插值图还原效果有限。
- 抠底假设背景是单一纯色。渐变背景、复杂背景需要先手工处理。
- 切分是**均匀等分**，不检测内容格线；留白不均的精灵表需要手动调行列数或先加留白。
- 限色后的调色板是确定性的（k-means++ 初始化取最高频色，之后按 `argmax(count·√weight)` 选种子，无随机数），但**颜色顺序按 Lab 亮度排序**，不同图片之间不共享同一套调色板。

## 许可

未指定。使用前请与作者确认。
