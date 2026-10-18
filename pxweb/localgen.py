# -*- coding: utf-8 -*-
"""pxweb 本地生成后端 —— 把 ComfyUI 的输出整理成和 rika 一样的 job 结构。

为什么要这一层：
    ComfyUI 是**同步阻塞**推理，而且出的是 512px 照片级图；
    rika 是**异步 job + 积分**，出的直接是像素精灵表。
    前端只认 rika 的 job 形状（{gen_id, status, output_images:[{url}]}），
    所以这里做两件事：

      1. 后台线程跑 ComfyUI，立刻返回 gen_id —— 前端逻辑一行都不用改
      2. 把照片级输出做**像素化后处理**（去背 → 面积降采样 → 调色板量化 → 拼条）
         pxkit 的 background / palette / sheet 直接复用，不重写算法

本地能力边界（诚实标注，前端据此禁用）：
    image  单张     x 文生图，1 张                          ~ 6 s
    anim   动画条   x 整条精灵表一次出图，见下方说明         ~ 6 s + 45 s x 额外行

动画为什么不是逐帧重绘：
    实测（work/_probe_denoise.png）逐帧 i2i 是个死结 ——
    denoise 低则每帧动作几乎不变，denoise 高则每帧变成不同角色。
    改用「整条精灵表一次出图」后，帧间一致性由模型在同一次采样内保证，
    动作差异靠提示词逐格描述姿势（work/_probe_walkcycle-posed.png）。

实测出来的硬边界（work/__probe_16f-2048x128.png / __probe_grid4x4-1024x1024.png）：
    单行 <= 8 帧、每格 >= 256px 时能出真正有差异的动作序列；
    16 帧挤一行（2048x128）直接崩成碎片；改网格排列则退化成每格同一姿势。
    所以 > 8 帧走「多行接力」：每行单独出图，后一行以前一行为 latent 参考。
"""
from __future__ import annotations

import base64
import io
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

# pxkit 在隔壁目录（server.py 也会插，但本模块要能单独 import 起来测）
_PXKIT = Path(__file__).resolve().parent.parent / "pxkit"
if str(_PXKIT) not in sys.path:
    sys.path.insert(0, str(_PXKIT))

from pxkit import background, imgio, palette, sheet   # noqa: E402

import comfy                                          # noqa: E402

# gen_id -> job dict（与 rika 的 /jobs/{id} 同形）
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()

# 本地后端支持的能力。前端据此把不支持的选项置灰。
CAPS = {
    "backend": "comfy",
    "kinds": ["character", "item", "map", "v2", "v1"],
    "credits": 0,                     # 本地不扣积分
    "needs_ref_for_anim": False,      # 无参考图也能出（t2i 直接画精灵表）
    "max_frames": 16,                 # 单行 8 帧 × 两行接力
    "notes": {
        "v2": "本地整条精灵表出图，≤8 帧一趟，更多帧走两趟接力",
        "v1": "同上（本地不区分 V1/V2 的采样器，参数映射到同一管线）",
        "character": "文生图单张",
        "item": "文生图单张",
        "map": "文生图单张",
    },
}

# 每帧 i2i 的实测耗时，用于给用户预估
SEC_PER_FRAME = 4.5
SEC_SINGLE = 6.0

# ---------------------------------------------------------------- 精灵表出图
#
# 为什么是这些数字（全部来自实测，改之前先重跑 work/ 下的探针图）：
#   CELL=256    每格 256px 才能画出全身；再小模型会开始裁掉腿
#   MAX_ROW=8   一行最多 8 格 @2048px 宽；16 格挤一行会崩成碎片
#   DENOISE=0.85 接力时低降噪：0.7 动作几乎不变，0.55 完全不动
CELL = 256
MAX_ROW = 8
SHEET_DENOISE = 0.85

# 每个动作的姿势序列 —— 描述越具体，模型越会画真正的动作差异
# （对照：只说 "walk cycle, 4 frames" 会退化成同一姿势复制 4 份）
_POSES = {
    "walk": [
        "contact pose, front leg extended far forward, back leg straight behind",
        "passing pose, legs together, body at highest point",
        "contact pose, other leg extended far forward, back leg straight behind",
        "passing pose, legs crossing under body",
        "front leg lifted high, knee bent, arms swinging opposite",
        "mid stride, weight on back leg, front foot about to land",
    ],
    "run": [
        "both feet off ground, legs split wide, body leaning forward",
        "front foot landing hard, back leg kicked up behind",
        "crouched push-off, back leg extended, arms pumping",
        "airborne, knees tucked up, arms forward",
    ],
    "idle": [
        "standing still, weight even on both feet, arms relaxed at sides",
        "subtle sway, slight shoulder drop, chest rising with breath",
        "standing still, head turned slightly, arms relaxed",
        "subtle sway the other way, slight weight shift to one leg",
    ],
    "jump": [
        "crouched low, knees deeply bent, arms back ready to spring",
        "exploding upward, legs extending, arms swinging up",
        "airborne at peak, legs tucked, arms up",
        "falling, legs reaching down for landing",
        "landing crouch, knees absorbing impact, arms out for balance",
    ],
    "attack": [
        "winding up, weapon drawn back, body coiled",
        "mid swing, weapon arcing forward, body rotating",
        "full extension, weapon forward, weight on front leg",
        "recovery, weapon lowering, body settling",
    ],
    "hit": [
        "impact, body recoiling backward, arms flying up",
        "staggering back, one foot lifting to catch balance",
        "recovering, body straightening, guard coming up",
    ],
    "defeated": [
        "taking the hit, body folding forward",
        "knees buckling, falling downward",
        "on the ground, lying motionless",
    ],
}


def _row_count(frames: int) -> int:
    """多少行能放下 frames 帧（每行最多 MAX_ROW）。"""
    return max(1, (frames + MAX_ROW - 1) // MAX_ROW)


def _frames_per_row(frames: int, rows: int) -> list[int]:
    """把 frames 帧尽量均匀分到 rows 行。"""
    base, extra = divmod(frames, rows)
    return [base + (1 if i < extra else 0) for i in range(rows)]


def _pose_sequence(motion: str, frames: int) -> list[str]:
    """按动作生成 frames 长度的姿势描述序列（循环取用）。"""
    table = _POSES.get(motion) or _POSES["idle"]
    return [table[i % len(table)] for i in range(frames)]


def _sheet_prompt(base: str, motion: str, poses: list[str], n: int) -> str:
    """拼出「N 格横向精灵表，逐格描述姿势」的提示词。

    逐格描述是整条出图能出真动作的关键 —— 只说整体动作，
    模型会把同一姿势复制 N 份（实测 work/__probe_sheet_4frames-1024x256.png）。
    """
    desc = "; ".join(f"frame {i + 1}: {p}" for i, p in enumerate(poses))
    return (f"animation sprite sheet of {base}, {motion} animation, "
            f"{n} keyframes arranged left to right in {n} equal columns of "
            f"{CELL} pixels each. {desc}. "
            f"Each frame must show a clearly different pose. "
            f"Same character design in every frame.")


# ------------------------------------------------------------------ job 存取

def job(gen_id: str) -> dict:
    """按 rika 的形状返回 job。未知 id 返回 failed 而不是抛错。"""
    with _LOCK:
        j = _JOBS.get(gen_id)
        if j is None:
            return {"gen_id": gen_id, "status": "failed",
                    "error": "本地后端重启过，找不到这个任务（任务只存在内存里）",
                    "job_type": "", "output_images": []}
        return dict(j)


def _set(gen_id: str, **kw):
    with _LOCK:
        _JOBS.setdefault(gen_id, {}).update(kw)


def _prune(keep: int = 40):
    with _LOCK:
        if len(_JOBS) <= keep:
            return
        for k in list(_JOBS)[: len(_JOBS) - keep]:
            _JOBS.pop(k, None)


# ------------------------------------------------------------------ 像素化

def _autocrop_alpha(rgba: np.ndarray, thresh: int = 8) -> np.ndarray:
    """裁掉四周全透明的边。"""
    a = rgba[:, :, 3]
    ys, xs = np.where(a > thresh)
    if ys.size == 0:
        return rgba
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    return rgba[y0:y1, x0:x1]


def _px_resize(rgba: np.ndarray, w: int, h: int) -> np.ndarray:
    """高分辨率 → 目标像素尺寸。

    用 BOX（面积平均）而不是 NEAREST：先把细节压成色块，
    再由 palette.quantize 收成有限调色板，这才是像素画的正确路径。
    直接 NEAREST 会把噪点采样成麻子。
    """
    if rgba.shape[1] == w and rgba.shape[0] == h:
        return rgba
    im = Image.fromarray(rgba, "RGBA").resize((w, h), Image.BOX)
    return np.array(im, dtype=np.uint8)


def _to_pixel(rgba: np.ndarray, size: int, colors: int,
              drop_bg: bool = True) -> np.ndarray:
    """完整像素化：去背 → 等比缩放居中 → 量化。

    注意：去背必须在**降采样之前**做，否则白底和边缘会先被平均成灰边。
    """
    if drop_bg:
        try:
            rgba = background.remove_background(rgba, tol=18.0, soft=10.0)
        except Exception:
            pass
        rgba = _autocrop_alpha(rgba)
        if rgba.shape[0] == 0 or rgba.shape[1] == 0:
            raise RuntimeError("去背后图像为空 —— 参考图可能整张都是背景色")

    # 等比缩到 size×size 的画布中央
    h, w = rgba.shape[:2]
    scale = min(size / max(w, 1), size / max(h, 1))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    small = _px_resize(rgba, nw, nh)
    canvas = np.zeros((size, size, 4), dtype=np.uint8)
    oy, ox = (size - nh) // 2, (size - nw) // 2
    canvas[oy:oy + nh, ox:ox + nw] = small

    if colors and 2 <= colors <= 256:
        canvas, _pal = palette.quantize(canvas, int(colors), bg_rgb=None)
    return canvas


def _strip(frames: list[np.ndarray]) -> np.ndarray:
    """N 帧横向拼成精灵表（前端按 width/frames 切帧）。"""
    h = frames[0].shape[0]
    w = sum(f.shape[1] for f in frames)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    x = 0
    for f in frames:
        out[:, x:x + f.shape[1]] = f
        x += f.shape[1]
    return out


def _save_png(rgba: np.ndarray) -> str:
    """落盘到 server.py 的 WORK 目录，返回可访问的 URL 路径部分。

    这里不能 import server（循环依赖），所以直接按约定拼路径：
    server.py 的 GET /work/<name>.png 会读 WORK 目录。
    """
    root = imgio.Path(__file__).resolve().parent / "work"
    imgio.ensure_dir(root)
    name = "lg_" + uuid.uuid4().hex[:16] + ".png"
    imgio.save_rgba(rgba, root / name)
    return "/work/" + name


def _png_to_rgba(data: bytes) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(data)).convert("RGBA"), dtype=np.uint8)


def _rgba_to_dataurl(rgba: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ 提交

def submit(kind: str, images: list[str], params: dict,
           cfg: dict | None = None) -> str:
    """起后台线程跑任务，立刻返回 gen_id。images 是 data URL 列表。"""
    cfg = cfg or comfy.load()
    gen_id = str(uuid.uuid4())
    _prune()
    with _LOCK:
        _JOBS[gen_id] = {
            "gen_id": gen_id,
            "job_type": kind,
            "status": "queued",
            "processing_stage": "",
            "input_params": dict(params or {}),
            "output_images": [],
            "error": "",
            "created_at": time.time(),
            "updated_at": time.time(),
            "backend": "comfy",
        }

    t = threading.Thread(
        target=_run, args=(gen_id, kind, images or [], params or {}, cfg),
        daemon=True, name="localgen-" + gen_id[:8])
    t.start()
    return gen_id


def _run(gen_id: str, kind: str, images: list[str], params: dict, cfg: dict):
    t0 = time.time()
    try:
        _set(gen_id, status="running", processing_stage="generation_starting",
             updated_at=time.time())
        out = _generate(kind, images, params, cfg,
                        on_stage=lambda s: _set(gen_id, processing_stage=s,
                                                updated_at=time.time()),
                        gen_id=gen_id)
        _set(gen_id, status="succeeded", processing_stage="done",
             output_images=[{"url": u, "key": u.rsplit("/", 1)[-1]} for u in out],
             elapsed=round(time.time() - t0, 1), updated_at=time.time())
    except Exception as e:                                   # noqa: BLE001
        _set(gen_id, status="failed", error=f"{type(e).__name__}: {e}",
             traceback=traceback.format_exc()[-1500:], updated_at=time.time())


def _generate(kind: str, images: list[str], params: dict, cfg: dict,
              on_stage, gen_id: str = "") -> list[str]:
    prompt = str(params.get("prompt") or "").strip()
    # 像素风提示词后缀：本地模型是照片级底模，不点明就出写实图
    style = str(params.get("style_suffix") or
                "pixel art sprite, clean hard edges, limited color palette, "
                "flat shading, game asset, centered, plain white background")
    full_prompt = (prompt + ", " + style) if prompt else style
    negative = str(params.get("negative") or
                   "blurry, photo, photorealistic, 3d render, gradient, noise, "
                   "text, watermark, signature, multiple characters")

    size = int(params.get("pixel_size_out") or 128)         # 最终像素尺寸
    colors = int(params.get("colors") or 32)
    seed = int(params.get("seed") or 0) & ((1 << 63) - 1)

    # 工作分辨率：本地模型在 512 附近质量最好，512 也是 8 的倍数便于降采样
    work = max(256, min(1024, size * 4))
    work = (work // 64) * 64

    ref_name = None
    if images:
        on_stage("preparing_reference")
        raw_ref = base64.b64decode(images[0].split(",", 1)[-1])
        ref_name = comfy.upload_image(cfg, raw_ref,
                                      "pxkit_ref_" + (gen_id[:8] or "x") + ".png")

    # ---- 单张：character / item / map ----
    if kind in ("character", "item", "map"):
        on_stage("generating")
        if ref_name and kind in ("character", "item"):
            g = comfy.build_i2i(cfg, full_prompt, negative, ref_name, work, work, seed)
        else:
            g = comfy.build_t2i(cfg, full_prompt, negative, work, work, seed)
        item = comfy.wait(cfg, comfy.submit(cfg, g))
        png = comfy.fetch_images(cfg, item)[0]
        on_stage("postprocessing")
        rgba = _to_pixel(_png_to_rgba(png), size, colors)
        return [_save_png(rgba)]

    # ---- 动画：v2 / v1 ----
    #
    # 核心策略（实测得出，别改回逐帧）：
    #   一次出 8 帧一行的精灵表，让模型在同一次采样内保证帧间一致；
    #   超过 8 帧就再出一行，后一行把前一行当 latent 参考接力。
    frames = int(params.get("output_frames") or params.get("length") or 8)
    frames = max(2, min(16, frames))          # 本地后端上限 16 帧（两行）
    motion = str(params.get("motion_type") or "idle")

    rows = _row_count(frames)
    per_row = _frames_per_row(frames, rows)
    pose_seq = _pose_sequence(motion, frames)

    all_frames: list[np.ndarray] = []
    ref_for_next = ref_name                  # 用户参考图 / 上一行精灵表

    for r in range(rows):
        n = per_row[r]
        on_stage(f"generating row {r + 1}/{rows} ({n} frames)")
        poses = [pose_seq[sum(per_row[:r]) + i] for i in range(n)]
        prompt = _sheet_prompt(full_prompt, motion, poses, n)

        if r == 0 and not ref_for_next:
            g = comfy.build_t2i(cfg, prompt, negative, n * CELL, CELL,
                                (seed + r * 7919) & ((1 << 63) - 1))
        else:
            # 有前序参考图（用户上传的，或上一行精灵表）→ 低降噪重绘锁身份
            g = comfy.build_i2i_latent(cfg, prompt, negative, ref_for_next,
                                       (seed + r * 7919) & ((1 << 63) - 1),
                                       denoise=SHEET_DENOISE)
        item = comfy.wait(cfg, comfy.submit(cfg, g))
        png = comfy.fetch_images(cfg, item)[0]

        # 下一条的参考图 = 这一行原图（保持高清，不要用像素化后的）
        ref_for_next = comfy.upload_image(
            cfg, png, f"pxkit_row_{gen_id[:8] or 'x'}_{r}.png")

        # 切格 → 逐格像素化
        raw_row = _png_to_rgba(png)
        for i in range(n):
            t0 = raw_row.shape[1] * i // n
            t1 = raw_row.shape[1] * (i + 1) // n
            cell_img = raw_row[:, t0:t1]
            try:
                all_frames.append(_to_pixel(cell_img, size, colors))
            except Exception:
                all_frames.append(np.zeros((size, size, 4), dtype=np.uint8))
            on_stage("generating " + str(len(all_frames)) + "/" + str(frames))

    on_stage("postprocessing")
    return [_save_png(_strip(all_frames))]
