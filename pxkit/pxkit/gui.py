# -*- coding: utf-8 -*-
"""pxkit GUI —— 像素素材工具图形界面。

启动：
    python -m pxkit.gui
    python -m pxkit gui

设计要点：
  - 左「原图」右「结果」，参数一改自动重算（250ms 防抖）
  - 「应用」把结果变成新的工作图，可以链式反复处理；「还原」回到最初
  - 所有耗时操作走后台线程 + 代次号（generation）丢弃过期结果，界面不卡
  - 预览统一棋盘格底 + 最近邻放大，透明区和像素边界都看得清
"""
from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

import numpy as np
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from PIL import Image, ImageTk

from . import background as bgm
from . import grid, imgio, palette, sheet
from .color import rgb2hex

# ---------------------------------------------------------------- 外观常量
UI = ("Microsoft YaHei UI", 9)
UI_B = ("Microsoft YaHei UI", 9, "bold")
UI_S = ("Microsoft YaHei UI", 8)
MONO = ("Consolas", 9)

C_BG = "#1e1e1e"
C_PANEL = "#252526"
C_LINE = "#3a3a3c"
C_FG = "#d4d4d4"
C_DIM = "#8a8a8a"
C_ACCENT = "#34bcb5"
C_WARN = "#e0a030"
C_ERR = "#e05a5a"

IMG_TYPES = [
    ("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tga"),
    ("PNG", "*.png"),
    ("所有文件", "*.*"),
]

FIT_CHOICES = ["居中 center", "左上 topleft", "贴底 bottom"]


# ---------------------------------------------------------------- 绘图工具
def _checker(w: int, h: int, size: int = 8,
             c1=(56, 56, 56), c2=(72, 72, 72)) -> np.ndarray:
    """棋盘格底纹，用来显形透明区域。"""
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // size) + (yy // size)) % 2 == 0
    out = np.empty((h, w, 3), np.uint8)
    out[m] = c1
    out[~m] = c2
    return out


def _render(rgba: np.ndarray, cw: int, ch: int, zoom,
            checker: bool = True) -> Image.Image:
    """把 RGBA 数组按缩放铺到 cw×ch 的画布上（最近邻，保持像素硬边）。"""
    h, w = rgba.shape[:2]
    if zoom is None:
        s = min(cw / w, ch / h)
    else:
        s = float(zoom)
    s = max(s, 1e-3)
    nw = max(1, int(round(w * s)))
    nh = max(1, int(round(h * s)))

    im = Image.fromarray(rgba, "RGBA")
    if (nw, nh) != (w, h):
        im = im.resize((nw, nh), Image.NEAREST)
    arr = np.asarray(im)

    base = (_checker(nw, nh) if checker
            else np.zeros((nh, nw, 3), np.uint8))
    a = arr[..., 3:4].astype(np.float32) / 255.0
    rgb = arr[..., :3].astype(np.float32)
    out = (rgb * a + base.astype(np.float32) * (1.0 - a))
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def _count_colors(rgba: np.ndarray) -> int:
    """不透明像素里有多少种颜色。"""
    flat = rgba.reshape(-1, 4)
    flat = flat[flat[:, 3] >= 128]
    if flat.size == 0:
        return 0
    return int(np.unique(flat[:, :3], axis=0).shape[0])


def _is_empty(rgba) -> bool:
    return rgba is None or getattr(rgba, "size", 0) == 0


# ---------------------------------------------------------------- 单图视图
class ImageView(ttk.Frame):
    """一块画布：标题 + 棋盘格预览 + 可选网格线覆盖。"""

    def __init__(self, master, title: str = "", **kw):
        super().__init__(master, **kw)
        self._rgba = None
        self._photo = None
        self._scale = 1.0
        self.zoom = None            # None = 适应窗口
        self.show_checker = True
        self.overlay_block = 0      # >0 时画块网格线

        self.header = tk.Label(self, text=title, font=UI_S, fg=C_DIM,
                               bg=C_PANEL, anchor="w", padx=6, pady=2)
        self.header.pack(fill="x")
        self.canvas = tk.Canvas(self, bg=C_BG, highlightthickness=0,
                                bd=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Motion>", self._on_motion)

    # ---- 数据
    def set_image(self, rgba):
        self._rgba = rgba
        self.redraw()

    def set_caption(self, text: str):
        self.header.configure(text=text)

    def image(self):
        return self._rgba

    # ---- 绘制
    def redraw(self):
        c = self.canvas
        c.delete("all")
        cw, ch = max(c.winfo_width(), 2), max(c.winfo_height(), 2)
        if _is_empty(self._rgba):
            c.create_text(cw // 2, ch // 2, text="（空）", fill=C_DIM, font=UI)
            return
        h, w = self._rgba.shape[:2]
        try:
            img = _render(self._rgba, cw, ch, self.zoom, self.show_checker)
        except Exception:                                  # noqa: BLE001
            return
        self._photo = ImageTk.PhotoImage(img)
        pw, ph = img.size
        ox, oy = (cw - pw) // 2, (ch - ph) // 2
        c.create_image(ox, oy, image=self._photo, anchor="nw")
        self._scale = pw / w if w else 1.0

        if self.overlay_block > 1 and self._scale > 0.5:
            step = self.overlay_block * self._scale
            n = 0
            x = step
            while x < pw - 0.5:
                c.create_line(ox + x, oy, ox + x, oy + ph,
                              fill=C_ACCENT, stipple="gray50")
                x += step
                n += 1
                if n > 400:
                    break
            n = 0
            y = step
            while y < ph - 0.5:
                c.create_line(ox, oy + y, ox + pw, oy + y,
                              fill=C_ACCENT, stipple="gray50")
                y += step
                n += 1
                if n > 400:
                    break

    def _on_motion(self, e):
        if _is_empty(self._rgba):
            return
        c = self.canvas
        cw, ch = c.winfo_width(), c.winfo_height()
        h, w = self._rgba.shape[:2]
        pw, ph = int(round(w * self._scale)), int(round(h * self._scale))
        ox, oy = (cw - pw) // 2, (ch - ph) // 2
        px = int((e.x - ox) / self._scale) if self._scale else -1
        py = int((e.y - oy) / self._scale) if self._scale else -1
        if 0 <= px < w and 0 <= py < h:
            r, g, b, a = self._rgba[py, px]
            self.header.configure(
                text=f"{self._title_txt}  ({px},{py}) #{r:02x}{g:02x}{b:02x} a={a}")

    _title_txt = ""

    def title_text(self, t: str):
        self._title_txt = t
        self.header.configure(text=t)


# ---------------------------------------------------------------- 参数小工具
def _row(parent, label: str, widget, hint: str = ""):
    f = ttk.Frame(parent)
    f.pack(fill="x", pady=2)
    ttk.Label(f, text=label, font=UI, width=9, anchor="w").pack(side="left")
    widget.pack(side="left", fill="x", expand=True)
    if hint:
        ttk.Label(f, text=hint, font=UI_S, foreground=C_DIM).pack(side="left")
    return f


class SliderRow(ttk.Frame):
    """整数滑杆 + 实时数值显示。"""

    def __init__(self, master, label: str, lo: int, hi: int, value: int,
                 on_change=None, width: int = 9):
        super().__init__(master)
        self.pack(fill="x", pady=2)
        self._cb = on_change
        self.var = tk.IntVar(value=value)
        ttk.Label(self, text=label, font=UI, width=width,
                  anchor="w").pack(side="left")
        self._val = ttk.Label(self, text=str(value), font=MONO,
                              width=5, anchor="e")
        self._val.pack(side="right")
        self.scale = ttk.Scale(self, from_=lo, to=hi, orient="horizontal",
                               command=self._on)
        self.scale.set(value)
        self.scale.pack(side="left", fill="x", expand=True, padx=3)

    def _on(self, _=None):
        v = int(round(float(self.scale.get())))
        if v != self.var.get():
            self.var.set(v)
            self._val.configure(text=str(v))
            if self._cb:
                self._cb()

    def get(self) -> int:
        return int(self.var.get())

    def set(self, v: int):
        self.scale.set(v)
        self.var.set(int(v))
        self._val.configure(text=str(int(v)))


# ---------------------------------------------------------------- 主程序
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("pxkit · 像素素材工具")
        self.root.geometry("1440x900")
        self.root.minsize(1120, 720)
        self.root.configure(bg=C_BG)

        # ---- 状态
        self.path: Path | None = None
        self.folder: Path | None = None
        self.original = None         # 从文件读进来的原始 RGBA
        self.current = None          # 当前工作图（可被「应用」更新）
        self.result = None           # 右侧预览结果
        self.tiles: list = []        # 切分产出
        self.bg_color = None         # 手动指定的背景色 (r,g,b)
        self._gen = 0                # 代次号，丢弃过期后台结果
        self._busy = False
        self._debounce = None
        self._batch_cancel = False

        self._build_style()
        self._build_toolbar()
        self._build_log()       # 先占住底部，再把主体填中间
        self._build_body()

        self.root.bind("<Control-o>", lambda e: self.open_file())
        self.root.bind("<Control-O>", lambda e: self.open_file())
        self.root.bind("<Control-s>", lambda e: self.save_result())
        self.root.bind("<Control-d>", lambda e: self.open_folder())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.log("pxkit GUI 就绪。Ctrl+O 打开图片，Ctrl+D 打开文件夹，Ctrl+S 保存结果。")
        self._sync_bg_swatch()

    # ------------------------------------------------------------ 样式
    def _build_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=UI, background=C_PANEL, foreground=C_FG,
                     fieldbackground="#2d2d30", bordercolor=C_LINE)
        st.configure("TFrame", background=C_PANEL)
        st.configure("TLabel", background=C_PANEL, foreground=C_FG)
        st.configure("Dim.TLabel", foreground=C_DIM, font=UI_S)
        st.configure("TButton", padding=(8, 4))
        st.configure("Accent.TButton", foreground="#0f1a1a",
                     background=C_ACCENT, borderwidth=0)
        st.map("Accent.TButton",
               background=[("active", "#4bd6ce"), ("disabled", "#3c5c5a")])
        st.configure("TNotebook", background=C_PANEL, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(12, 5))
        st.map("TNotebook.Tab",
               background=[("selected", "#2d2d30"), ("!selected", C_PANEL)])
        st.configure("TLabelframe", background=C_PANEL, bordercolor=C_LINE)
        st.configure("TLabelframe.Label", background=C_PANEL,
                     foreground=C_DIM, font=UI_S)
        st.configure("TCheckbutton", background=C_PANEL, foreground=C_FG)
        st.configure("TCombobox", fieldbackground="#2d2d30")
        st.configure("TEntry", fieldbackground="#2d2d30")
        st.configure("Sash", sashthickness=6)

    # ------------------------------------------------------------ 工具栏
    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=C_PANEL, height=42)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        def btn(text, cmd, accent=False, side="left"):
            b = ttk.Button(bar, text=text, command=cmd,
                           style="Accent.TButton" if accent else "TButton")
            b.pack(side=side, padx=3, pady=7)
            return b

        # 右侧组先 pack，保证窗口变窄时优先保住它们
        btn("保存结果", self.save_result, side="right")
        btn("应用结果", self.apply_result, accent=True, side="right")
        btn("还原", self.reset_current, side="right")
        tk.Frame(bar, bg=C_LINE, width=1).pack(side="right", fill="y",
                                               padx=6, pady=8)

        btn("打开图片", self.open_file, accent=True)
        btn("打开文件夹", self.open_folder)
        tk.Frame(bar, bg=C_LINE, width=1).pack(side="left", fill="y",
                                               padx=6, pady=8)
        btn("适应", lambda: self.set_zoom(None))
        btn("1:1", lambda: self.set_zoom(1.0))
        btn("＋", lambda: self.set_zoom(self._next_zoom(1)))
        btn("－", lambda: self.set_zoom(self._next_zoom(-1)))

        self.chk_checker = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="棋盘格", variable=self.chk_checker,
                        command=self._toggle_checker).pack(side="left", padx=6)

    def _next_zoom(self, d: int):
        cur = self.views[0].zoom if self.views else None
        steps = [0.25, 0.33, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 16]
        if cur is None:
            cur = 1.0
        idx = min(range(len(steps)), key=lambda i: abs(steps[i] - cur))
        idx = max(0, min(len(steps) - 1, idx + d))
        return steps[idx]

    def set_zoom(self, z):
        for v in self.views:
            v.zoom = z
            v.redraw()

    def _toggle_checker(self):
        on = self.chk_checker.get()
        for v in self.views:
            v.show_checker = on
            v.redraw()

    # ------------------------------------------------------------ 主体
    def _build_body(self):
        body = tk.Frame(self.root, bg=C_BG)
        body.pack(side="top", fill="both", expand=True)

        # ---------------- 左：文件列表
        left = tk.Frame(body, bg=C_PANEL, width=196)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)        tk.Label(left, text="文件", font=UI_S, fg=C_DIM, bg=C_PANEL,
                 anchor="w", padx=8, pady=4).pack(fill="x")
        wrap = tk.Frame(left, bg=C_PANEL)
        wrap.pack(fill="both", expand=True, padx=4, pady=(0, 6))
        self.listbox = tk.Listbox(
            wrap, bg="#2d2d30", fg=C_FG, font=UI_S, selectmode="browse",
            highlightthickness=0, bd=0, activestyle="none",
            selectbackground=C_ACCENT, selectforeground="#0f1a1a")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_pick)

        # ---------------- 右：参数面板（先 pack，窄窗口时优先保住它）
        right = tk.Frame(body, bg=C_PANEL, width=330)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        # ---------------- 中：双画布
        mid = tk.Frame(body, bg=C_BG)
        mid.pack(side="left", fill="both", expand=True)

        panes = tk.Frame(mid, bg=C_BG)
        panes.pack(fill="both", expand=True, padx=4, pady=4)
        panes.columnconfigure(0, weight=1, uniform="v")
        panes.columnconfigure(1, weight=1, uniform="v")
        panes.rowconfigure(0, weight=1)

        self.view_l = ImageView(panes, "原图")
        self.view_r = ImageView(panes, "结果")
        self.view_l.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        self.view_r.grid(row=0, column=1, sticky="nsew", padx=(2, 0))
        self.views = [self.view_l, self.view_r]

        self.status = tk.Label(mid, text="未载入图片", font=UI_S, fg=C_DIM,
                               bg=C_BG, anchor="w", padx=8, pady=3)
        self.status.pack(fill="x", side="bottom")

        # ---------------- 右：参数面板内容
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True, padx=4, pady=4)
        self._build_tab_detect(nb)
        self._build_tab_clean(nb)
        self._build_tab_slice(nb)
        self._build_tab_pack(nb)
        self._build_tab_bg(nb)

    # ------------------------------------------------------------ Tab 检测
    def _build_tab_detect(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="检测")

        ttk.Label(f, text="认出这张图的真实分辨率", font=UI_B).pack(anchor="w")
        ttk.Label(f, text="像素画常被整数倍放大保存；这一步反推放大倍数。",
                  font=UI_S, foreground=C_DIM, wraplength=290,
                  justify="left").pack(anchor="w", pady=(2, 8))

        ttk.Button(f, text="检测放大倍数", command=self.op_detect,
                   style="Accent.TButton").pack(fill="x", pady=2)

        box = ttk.LabelFrame(f, text="结果", padding=8)
        box.pack(fill="x", pady=8)
        self.lbl_detect = tk.Label(box, text="—", font=MONO, fg=C_FG,
                                   bg=C_PANEL, anchor="w", justify="left")
        self.lbl_detect.pack(fill="x")

        self.chk_overlay = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="在画布上叠显示网格线",
                        variable=self.chk_overlay,
                        command=self._apply_overlay).pack(anchor="w", pady=(4, 0))

        ttk.Separator(f).pack(fill="x", pady=10)
        ttk.Label(f, text="提示", font=UI_B).pack(anchor="w")
        for t in ["· 检测只给建议，最终以你眼睛看到的为准",
                  "· 倍数 = 1 表示已经是原始像素尺寸",
                  "· 检测不出（置信度低）时手填块大小即可"]:
            ttk.Label(f, text=t, font=UI_S, foreground=C_DIM,
                      wraplength=290, justify="left").pack(anchor="w")

    # ------------------------------------------------------------ Tab 清理
    def _build_tab_clean(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="清理")

        ttk.Label(f, text="一步得到干净的像素画", font=UI_B).pack(anchor="w")

        self.chk_auto_block = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="自动检测放大倍数",
                        variable=self.chk_auto_block,
                        command=self._schedule).pack(anchor="w", pady=(8, 0))
        self.sl_block = SliderRow(f, "块大小", 2, 64, 8,
                                  on_change=self._on_block_slider)
        self.sl_block.scale.configure(state="disabled")

        self.chk_quant = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="限制颜色数", variable=self.chk_quant,
                        command=self._schedule).pack(anchor="w", pady=(8, 0))
        self.sl_colors = SliderRow(f, "颜色数", 2, 128, 16, on_change=self._schedule)

        ttk.Separator(f).pack(fill="x", pady=8)
        ttk.Label(f, text="抠底", font=UI_B).pack(anchor="w")
        self.cmb_bg = ttk.Combobox(f, state="readonly", font=UI,
                                   values=["自动取四角", "不抠底", "指定颜色"])
        self.cmb_bg.current(0)
        self.cmb_bg.pack(fill="x", pady=(4, 2))
        self.cmb_bg.bind("<<ComboboxSelected>>", lambda e: self._on_bg_mode())

        sw = ttk.Frame(f)
        sw.pack(fill="x", pady=2)
        self.swatch = tk.Label(sw, text="  ", bg="#743451", relief="flat",
                               width=3, bd=0)
        self.swatch.pack(side="left", padx=(44, 4))
        self.btn_pick = ttk.Button(sw, text="取色…", width=7,
                                   command=self.pick_bg_color, state="disabled")
        self.btn_pick.pack(side="left")
        self.lbl_bghex = ttk.Label(sw, text="自动", font=MONO,
                                   foreground=C_DIM)
        self.lbl_bghex.pack(side="left", padx=6)

        self.sl_tol = SliderRow(f, "抠底容差", 0, 80, 14, on_change=self._schedule)

        self.chk_spill = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="去色边（半透明过渡像素洗色）",
                        variable=self.chk_spill,
                        command=self._schedule).pack(anchor="w", pady=(4, 0))

        self.chk_trim = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="裁到内容边界", variable=self.chk_trim,
                        command=self._schedule).pack(anchor="w")

        ttk.Separator(f).pack(fill="x", pady=8)
        ttk.Button(f, text="批量处理整个文件夹…",
                   command=self.batch_clean).pack(fill="x")

    # ------------------------------------------------------------ Tab 切分
    def _build_tab_slice(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="切分")

        ttk.Label(f, text="把精灵表等分成小格", font=UI_B).pack(anchor="w")
        ttk.Label(f, text="纯均匀切分，不做边缘检测——行列数由你定。",
                  font=UI_S, foreground=C_DIM, wraplength=290,
                  justify="left").pack(anchor="w", pady=(2, 8))

        self.sl_rows = SliderRow(f, "行数", 1, 32, 4, on_change=None)
        self.sl_cols = SliderRow(f, "列数", 1, 32, 4, on_change=None)

        r = ttk.Frame(f)
        r.pack(fill="x", pady=4)
        ttk.Label(r, text="顺序", font=UI, width=9, anchor="w").pack(side="left")
        self.cmb_order = ttk.Combobox(r, state="readonly", font=UI, width=12,
                                      values=["按行 row", "按列 col"])
        self.cmb_order.current(0)
        self.cmb_order.pack(side="left")

        self.chk_tile_trim = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="每格裁到内容（尺寸会不一致）",
                        variable=self.chk_tile_trim).pack(anchor="w", pady=(6, 0))

        ttk.Button(f, text="切分", command=self.op_slice,
                   style="Accent.TButton").pack(fill="x", pady=(10, 2))

        box = ttk.LabelFrame(f, text="产出", padding=8)
        box.pack(fill="x", pady=8)
        self.lbl_slice = tk.Label(box, text="—", font=MONO, fg=C_FG,
                                  bg=C_PANEL, anchor="w", justify="left")
        self.lbl_slice.pack(fill="x")

        ttk.Button(f, text="把所有格子另存为 PNG…",
                   command=self.save_tiles).pack(fill="x")
        ttk.Label(f, text="切出来的格子会带到「拼表」页去重排。",
                  font=UI_S, foreground=C_DIM, wraplength=290,
                  justify="left").pack(anchor="w", pady=(8, 0))

    # ------------------------------------------------------------ Tab 拼表
    def _build_tab_pack(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="拼表")

        ttk.Label(f, text="把散图重排成一张表", font=UI_B).pack(anchor="w")
        ttk.Label(f, text="先「打开文件夹」或先做「切分」，再来这里。",
                  font=UI_S, foreground=C_DIM, wraplength=290,
                  justify="left").pack(anchor="w", pady=(2, 8))

        self.sl_pack_cols = SliderRow(f, "列数", 1, 16, 4, on_change=self._schedule)
        self.sl_pack_gap = SliderRow(f, "间隙", 0, 32, 0, on_change=self._schedule)

        r = ttk.Frame(f)
        r.pack(fill="x", pady=4)
        ttk.Label(r, text="摆放", font=UI, width=9, anchor="w").pack(side="left")
        self.cmb_fit = ttk.Combobox(r, state="readonly", font=UI, width=12,
                                    values=FIT_CHOICES)
        self.cmb_fit.current(0)
        self.cmb_fit.pack(side="left")
        self.cmb_fit.bind("<<ComboboxSelected>>", lambda e: self._schedule())

        self.chk_auto_cell = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="格大小自动（取最大图元）",
                        variable=self.chk_auto_cell,
                        command=self._schedule).pack(anchor="w", pady=(6, 0))
        self.sl_cell = SliderRow(f, "格边长", 8, 512, 64, on_change=self._schedule)
        self.sl_cell.scale.configure(state="disabled")

        ttk.Button(f, text="拼表", command=self.op_pack,
                   style="Accent.TButton").pack(fill="x", pady=(10, 2))
        self.lbl_pack = tk.Label(f, text="—", font=MONO, fg=C_FG,
                                 bg=C_PANEL, anchor="w", justify="left")
        self.lbl_pack.pack(fill="x", pady=6)

    # ------------------------------------------------------------ Tab 抠底
    def _build_tab_bg(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="抠底")

        ttk.Label(f, text="只做背景透明化", font=UI_B).pack(anchor="w")
        ttk.Label(f, text="适合抠掉纯色/近纯色底，保留原图像素与颜色。",
                  font=UI_S, foreground=C_DIM, wraplength=290,
                  justify="left").pack(anchor="w", pady=(2, 8))

        r = ttk.Frame(f)
        r.pack(fill="x", pady=4)
        ttk.Label(r, text="背景色", font=UI, width=9, anchor="w").pack(side="left")
        self.cmb_bg2 = ttk.Combobox(r, state="readonly", font=UI, width=12,
                                    values=["自动取四角", "指定颜色"])
        self.cmb_bg2.current(0)
        self.cmb_bg2.pack(side="left")
        self.cmb_bg2.bind("<<ComboboxSelected>>", lambda e: self._on_bg_mode())

        self.sl_tol2 = SliderRow(f, "硬容差", 0, 80, 14, on_change=self._schedule)
        self.sl_soft2 = SliderRow(f, "软过渡", 0, 80, 24, on_change=self._schedule)
        self.sl_island = SliderRow(f, "去杂点", 0, 400, 0, on_change=self._schedule)

        self.chk_spill2 = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="去色边", variable=self.chk_spill2,
                        command=self._schedule).pack(anchor="w", pady=(4, 0))

        ttk.Button(f, text="执行抠底", command=self.op_bg,
                   style="Accent.TButton").pack(fill="x", pady=(12, 2))

    # ------------------------------------------------------------ 日志
    def _build_log(self):
        wrap = tk.Frame(self.root, bg=C_PANEL, height=112)
        wrap.pack(side="bottom", fill="x")
        wrap.pack_propagate(False)
        tk.Frame(wrap, bg=C_LINE, height=1).pack(fill="x")
        head = tk.Frame(wrap, bg=C_PANEL)
        head.pack(fill="x")
        tk.Label(head, text="日志", font=UI_S, fg=C_DIM, bg=C_PANEL,
                 anchor="w", padx=8).pack(side="left")
        ttk.Button(head, text="清空", width=5,
                   command=lambda: self.txt_log.delete("1.0", "end")).pack(
            side="right", padx=4, pady=2)

        self.txt_log = tk.Text(wrap, height=5, bg="#1b1b1c", fg=C_FG,
                               font=MONO, bd=0, highlightthickness=0,
                               wrap="word", padx=8, pady=3)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt_log.pack(side="left", fill="both", expand=True)
        self.txt_log.tag_configure("ok", foreground=C_ACCENT)
        self.txt_log.tag_configure("warn", foreground=C_WARN)
        self.txt_log.tag_configure("err", foreground=C_ERR)
        self.txt_log.tag_configure("dim", foreground=C_DIM)
        self.txt_log.configure(state="disabled")

    def log(self, msg: str, tag: str = ""):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", msg + "\n", tag)
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    # ------------------------------------------------------------ 打开
    def open_file(self):
        p = filedialog.askopenfilename(title="打开图片", filetypes=IMG_TYPES)
        if not p:
            return
        self.load_path(Path(p))

    def open_folder(self):
        d = filedialog.askdirectory(title="打开文件夹")
        if not d:
            return
        self.folder = Path(d)
        files = imgio.list_images(self.folder, recursive=False)
        self.listbox.delete(0, "end")
        for f in files:
            self.listbox.insert("end", Path(f).name)
        self._files = [Path(f) for f in files]
        self.log(f"打开文件夹：{self.folder}（{len(self._files)} 张）", "ok")
        if self._files:
            self.listbox.selection_set(0)
            self.load_path(self._files[0])

    def _on_pick(self, _evt):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(getattr(self, "_files", [])):
            self.load_path(self._files[idx])

    def load_path(self, p: Path):
        try:
            rgba = imgio.load_rgba(p)
        except Exception as e:                             # noqa: BLE001
            messagebox.showerror("打开失败", f"{p.name}\n\n{e}")
            self.log(f"打开失败：{p.name} —— {e}", "err")
            return
        self.path = p
        self.original = rgba
        self.current = rgba.copy()
        self.result = None
        self.tiles = []
        h, w = rgba.shape[:2]
        nc = _count_colors(rgba)
        alpha_vals = np.unique(rgba[..., 3])
        opaque = "不透明" if alpha_vals.size == 1 and alpha_vals[0] == 255 else "含透明"

        self.view_l.set_image(self.current)
        self.view_l.title_text(f"原图 {w}×{h}")
        self.view_r.set_image(None)
        self.view_r.title_text("结果")
        self.view_l.overlay_block = 0
        self.lbl_slice.configure(text="—")
        self.lbl_pack.configure(text="—")
        self.lbl_detect.configure(text="—")

        self.status.configure(
            text=f"{p.name}   {w}×{h}   {nc} 色   {opaque}   "
                 f"{p.stat().st_size / 1024:.1f} KB")
        self.log(f"载入 {p.name}  {w}×{h}  {nc} 色  {opaque}")

        # 若来自文件夹列表，同步选中
        if getattr(self, "_files", None) and p in self._files:
            i = self._files.index(p)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(i)

        self.root.title(f"pxkit · {p.name}")

    # ------------------------------------------------------------ 缩放/应用
    def apply_result(self):
        if _is_empty(self.result):
            self.log("还没有结果可以应用。", "warn")
            return
        self.current = self.result.copy()
        self.view_l.set_image(self.current)
        h, w = self.current.shape[:2]
        self.view_l.title_text(f"工作图 {w}×{h}")
        self.log(f"应用结果 → 工作图 {w}×{h}  {_count_colors(self.current)} 色", "ok")
        self._schedule()

    def reset_current(self):
        if _is_empty(self.original):
            return
        self.current = self.original.copy()
        self.view_l.set_image(self.current)
        h, w = self.current.shape[:2]
        self.view_l.title_text(f"原图 {w}×{h}")
        self.log("已还原到最初载入的图。")
        self._schedule()

    def save_result(self):
        if _is_empty(self.result):
            self.log("还没有结果可以保存。", "warn")
            return
        init = (self.path.stem + "_out.png") if self.path else "out.png"
        p = filedialog.asksaveasfilename(
            title="保存结果", defaultextension=".png",
            initialfile=init, filetypes=[("PNG", "*.png")])
        if not p:
            return
        try:
            imgio.save_rgba(self.result, p)
        except Exception as e:                             # noqa: BLE001
            messagebox.showerror("保存失败", str(e))
            self.log(f"保存失败：{e}", "err")
            return
        self.log(f"已保存 → {p}", "ok")

    # ------------------------------------------------------------ 参数联动
    def _on_block_slider(self):
        if self.chk_auto_block.get():
            self.chk_auto_block.set(False)
        self._schedule()

    def _on_bg_mode(self):
        mode = self.cmb_bg.get()
        manual = mode == "指定颜色"
        self.btn_pick.configure(state="normal" if manual else "disabled")
        if not manual and mode == "自动取四角":
            self.lbl_bghex.configure(text="自动")
        self._schedule()

    def _sync_bg_swatch(self):
        if self.bg_color:
            self.swatch.configure(bg=rgb2hex(self.bg_color))
            self.lbl_bghex.configure(text=rgb2hex(self.bg_color))
        else:
            self.swatch.configure(bg="#743451")

    def pick_bg_color(self):
        cur = rgb2hex(self.bg_color) if self.bg_color else "#743451"
        rgb, _ = colorchooser.askcolor(color=cur, title="选择背景色")
        if rgb:
            self.bg_color = tuple(int(v) for v in rgb)
            self._sync_bg_swatch()
            self._schedule()

    def _bg_arg(self):
        """按两个下拉框合成 remove_background 的 bg 参数。"""
        mode = self.cmb_bg.get()
        if mode == "不抠底":
            return False
        if mode == "指定颜色":
            return self.bg_color if self.bg_color else None
        return None

    def _bg_arg2(self):
        mode = self.cmb_bg2.get()
        if mode == "指定颜色":
            return self.bg_color if self.bg_color else None
        return None

    def _apply_overlay(self):
        b = 0
        if self.chk_overlay.get() and self._last_block > 1:
            b = self._last_block
        self.view_l.overlay_block = b
        self.view_r.overlay_block = b
        self.view_l.redraw()
        self.view_r.redraw()

    _last_block = 0

    # ------------------------------------------------------------ 调度
    def _schedule(self, _evt=None):
        """参数变化 → 250ms 后重算预览。"""
        if self._debounce is not None:
            try:
                self.root.after_cancel(self._debounce)
            except Exception:                              # noqa: BLE001
                pass
        self._debounce = self.root.after(250, self.refresh)

    def _run_async(self, fn, on_done):
        """后台跑 fn()，结果按代次号回主线程；过期结果直接丢弃。"""
        self._gen += 1
        gen = self._gen
        src = None if _is_empty(self.current) else self.current.copy()

        def work():
            try:
                res = fn(src)
                err = None
            except Exception as e:                         # noqa: BLE001
                res, err = None, e
                tb = traceback.format_exc()

            def deliver():
                if gen != self._gen:
                    return
                if err is not None:
                    self.log(f"运算失败：{err}", "err")
                    self._last_traceback = tb
                else:
                    on_done(res)

            try:
                self.root.after(0, deliver)
            except Exception:                              # noqa: BLE001
                pass

        threading.Thread(target=work, daemon=True).start()

    _last_traceback = ""

    # ------------------------------------------------------------ 预览计算
    def refresh(self):
        if _is_empty(self.current):
            return
        mode = self._current_tab()
        if mode == "clean":
            self._preview_clean()
        elif mode == "bg":
            self._preview_bg()
        elif mode == "pack":
            self._preview_pack()

    def _current_tab(self) -> str:
        nb = None
        for w in self.root.winfo_children():
            nb = self._find_notebook(w)
            if nb:
                break
        if not nb:
            return "clean"
        try:
            idx = nb.index(nb.select())
        except Exception:                                  # noqa: BLE001
            return "clean"
        return {0: "detect", 1: "clean", 2: "slice", 3: "pack", 4: "bg"}.get(
            idx, "clean")

    def _find_notebook(self, w):
        if isinstance(w, ttk.Notebook):
            return w
        for c in w.winfo_children():
            r = self._find_notebook(c)
            if r:
                return r
        return None

    # ---- 清理
    def _preview_clean(self):
        block = 0 if self.chk_auto_block.get() else self.sl_block.get()
        colors = self.sl_colors.get() if self.chk_quant.get() else 0
        bgarg = self._bg_arg()
        tol = float(self.sl_tol.get())
        spill = self.chk_spill.get()
        trim = self.chk_trim.get()

        def job(src):
            out = src
            if block != 1:
                out = grid.depixelate(out, block=block)
            if bgarg is not False:
                out = bgm.remove_background(out, bg=bgarg, tol=tol)
                if spill:
                    out = bgm.despill_edges(out)
            if colors > 0:
                out, _pal = palette.quantize(out, colors)
            if trim:
                out = sheet.trim_rgba(out, padding=0, square=False)
            return out

        self._run_async(job, self._show_clean)

    def _show_clean(self, out):
        self.result = out
        h, w = out.shape[:2]
        nc = _count_colors(out)
        self.view_r.set_image(out)
        self.view_r.title_text(f"结果 {w}×{h} · {nc} 色")
        self.status.configure(text=f"{self.path.name if self.path else ''}   "
                                   f"{w}×{h}   {nc} 色")

    # ---- 抠底
    def _preview_bg(self):
        bgarg = self._bg_arg2()
        tol = float(self.sl_tol2.get())
        soft = float(self.sl_soft2.get())
        island = self.sl_island.get()
        spill = self.chk_spill2.get()

        def job(src):
            out = bgm.remove_background(src, bg=bgarg, tol=tol, soft=soft,
                                        keep_larger_than=island)
            if spill:
                out = bgm.despill_edges(out)
            return out

        self._run_async(job, self._show_bg)

    def _show_bg(self, out):
        self.result = out
        h, w = out.shape[:2]
        opaque = float((out[..., 3] > 0).mean()) * 100
        self.view_r.set_image(out)
        self.view_r.title_text(f"结果 {w}×{h} · 不透明 {opaque:.1f}%")

    # ---- 拼表
    def _tiles_for_pack(self):
        if self.tiles:
            return self.tiles
        if getattr(self, "_files", None):
            out = []
            for p in self._files:
                try:
                    out.append(imgio.load_rgba(p))
                except Exception:                          # noqa: BLE001
                    pass
            return out
        return []

    def _preview_pack(self):
        tiles = self._tiles_for_pack()
        if not tiles:
            self.lbl_pack.configure(text="没有可拼的图元。\n先切分，或打开一个文件夹。")
            self.view_r.set_image(None)
            return
        cols = self.sl_pack_cols.get()
        gap = self.sl_pack_gap.get()
        fit = self.cmb_fit.get().split()[1]
        cell = None if self.chk_auto_cell.get() else self.sl_cell.get()

        def job(_src):
            return sheet.pack_sheet(tiles, cols=cols, cell=cell,
                                    fit=fit, gap=gap)

        self._run_async(job, self._show_pack)

    def _show_pack(self, out):
        self.result = out
        h, w = out.shape[:2]
        self.view_r.set_image(out)
        self.view_r.title_text(f"结果 {w}×{h}")
        self.lbl_pack.configure(
            text=f"{len(self._tiles_for_pack())} 个图元 → {w}×{h}")

    # ------------------------------------------------------------ 显式操作
    def op_detect(self):
        if _is_empty(self.current):
            self.log("先打开一张图。", "warn")
            return
        try:
            r = grid.auto_block_size(self.current)
            if isinstance(r, tuple):
                block, score = int(r[0]), float(r[1])
                cands = r[2] if len(r) > 2 else None
            else:
                block, score, cands = int(r), float("nan"), None
        except Exception as e:                             # noqa: BLE001
            self.log(f"检测失败：{e}", "err")
            return
        h, w = self.current.shape[:2]
        self._last_block = int(block)
        txt = (f"放大倍数  {block}×\n"
               f"真实尺寸  {w // max(block, 1)} × {h // max(block, 1)}\n"
               f"置信度    {score:.3f}")
        if cands:
            try:
                top4 = sorted(cands, key=lambda c: -c[1])[:4]
                top = ", ".join(f"{c[0]}× {c[1]:.2f}" for c in top4)
                txt += f"\n高分候选  {top}"
            except Exception:                              # noqa: BLE001
                pass
        self.lbl_detect.configure(text=txt)
        self.log(f"检测：{block}× → 真实 {w // max(block, 1)}×{h // max(block, 1)}  "
                 f"置信度 {score:.3f}", "ok")
        if block > 1:
            self.sl_block.set(int(block))
        self._apply_overlay()

    def op_slice(self):
        if _is_empty(self.current):
            self.log("先打开一张图。", "warn")
            return
        rows, cols = self.sl_rows.get(), self.sl_cols.get()
        order = "row" if self.cmb_order.get().startswith("按行") else "col"
        try:
            raw = sheet.slice_uniform(self.current, rows, cols, order=order)
            # slice_uniform 返回 [((r,c), tile), ...]，拆出图元本身
            tiles = [t for _rc, t in raw]
        except Exception as e:                             # noqa: BLE001
            self.log(f"切分失败：{e}", "err")
            return
        if self.chk_tile_trim.get():
            tiles = [sheet.trim_rgba(t) for t in tiles]
        self.tiles = tiles
        th, tw = tiles[0].shape[:2] if tiles else (0, 0)
        self.lbl_slice.configure(
            text=f"{rows}×{cols} = {len(tiles)} 格\n单格  {tw}×{th}")
        self.log(f"切分 {rows}×{cols} → {len(tiles)} 格（单格 {tw}×{th}）", "ok")
        self._schedule()

    def save_tiles(self):
        if not self.tiles:
            self.log("还没有切出格子。", "warn")
            return
        d = filedialog.askdirectory(title="选择保存格子的文件夹")
        if not d:
            return
        base = Path(d) / (self.path.stem if self.path else "tiles")
        imgio.ensure_dir(base)
        for i, t in enumerate(self.tiles):
            imgio.save_rgba(t, base / f"tile_{i:03d}.png")
        self.log(f"已保存 {len(self.tiles)} 张格子 → {base}", "ok")

    def op_pack(self):
        if not self._tiles_for_pack():
            self.log("没有可拼的图元。先切分，或打开一个文件夹。", "warn")
            return
        self._preview_pack()

    def op_bg(self):
        if _is_empty(self.current):
            self.log("先打开一张图。", "warn")
            return
        self._preview_bg()

    # ------------------------------------------------------------ 批量
    def batch_clean(self):
        if not getattr(self, "_files", None):
            self.log("批量处理需要先「打开文件夹」。", "warn")
            return
        out_dir = filedialog.askdirectory(title="选择输出文件夹")
        if not out_dir:
            return
        out = Path(out_dir)

        block0 = 0 if self.chk_auto_block.get() else self.sl_block.get()
        colors = self.sl_colors.get() if self.chk_quant.get() else 0
        bgarg = self._bg_arg()
        tol = float(self.sl_tol.get())
        spill = self.chk_spill.get()

        files = list(self._files)
        self._batch_cancel = False
        self.log(f"批量开始：{len(files)} 张 → {out}", "ok")

        def job(_src):
            ok = fail = 0
            for p in files:
                if self._batch_cancel:
                    break
                try:
                    img = imgio.load_rgba(p)
                    if block0 != 1:
                        img = grid.depixelate(img, block=block0)
                    if bgarg is not False:
                        img = bgm.remove_background(img, bg=bgarg, tol=tol)
                        if spill:
                            img = bgm.despill_edges(img)
                    if colors > 0:
                        img, _pal = palette.quantize(img, colors)
                    imgio.save_rgba(img, out / (p.stem + ".png"))
                    ok += 1
                    self.root.after(0, lambda n=p.name:
                                    self.log(f"  ✓ {n}", "dim"))
                except Exception as e:                     # noqa: BLE001
                    fail += 1
                    self.root.after(0, lambda n=p.name, err=e:
                                    self.log(f"  ✗ {n} —— {err}", "err"))
            return ok, fail

        self._run_async(job, lambda r: self.log(
            f"批量完成：成功 {r[0]}，失败 {r[1]}。", "ok"))

    # ------------------------------------------------------------ 收尾
    def _on_close(self):
        self._batch_cancel = True
        self.root.destroy()


# ---------------------------------------------------------------- 入口
def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    root = tk.Tk()
    app = App(root)
    # 允许 `python -m pxkit.gui <图片>` 直接打开
    if argv:
        p = Path(argv[0])
        if p.is_file():
            app.load_path(p)
        elif p.is_dir():
            app.folder = p
            app._files = [Path(f) for f in imgio.list_images(p, recursive=False)]
            for f in app._files:
                app.listbox.insert("end", f.name)
            if app._files:
                app.load_path(app._files[0])
    root.mainloop()
    return 0


def run(argv=None):
    return main(argv)


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
