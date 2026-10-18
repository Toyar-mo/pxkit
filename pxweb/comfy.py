# -*- coding: utf-8 -*-
"""pxweb 的本地后端适配层 —— 直接驱动本机 ComfyUI。

为什么单独一个文件：rika.py 走的是 rika 官方 HTTP API（异步 job + 积分），
ComfyUI 是**同步的本地推理**（提交 prompt → 轮询 history → 取图），
两者协议完全不同，混在一个文件里会互相污染。这里只做 ComfyUI。

已验证的本地环境（2026-10 实测）：
    ComfyUI 0.36.0 @ http://127.0.0.1:8188
    RTX 3070 Ti / 8.6 GB VRAM / 34 GB RAM
    diffusion_models/SilvermoonMix-Anima1-Evolved-Turbo-V2.3.0.safetensors  （Qwen-Image 架构）
    text_encoders/qwen_3_06b_base.safetensors                                （type=qwen_image）
    vae/qwen_image_vae.safetensors

实测耗时（8 步 / cfg 1.0 / euler+simple）：
    512×512 单张 ≈ 4 s（模型已热）
    256×256 单张 ≈ 56 s（模型冷加载后的首张）
    256×256 批量 4 张 ≈ 63 s（首张之后每张 ≈ 10 s）
    128×128 批量 4 张 ≈ 101 s
    TextEncodeQwenImageEditPlus 参考图编辑 ≈ 65 s/张

纯标准库实现（urllib + 手写 multipart），不引入 requests。
"""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_BASE = "http://127.0.0.1:8188"

# 本机 ComfyUI 的模型文件名。换成别的模型时改这里（或从 /object_info 自动探测）。
DEFAULTS = {
    "base_url": DEFAULT_BASE,
    "unet": "SilvermoonMix-Anima1-Evolved-Turbo-V2.3.0.safetensors",
    "clip": "qwen_3_06b_base.safetensors",
    "clip_type": "qwen_image",
    "vae": "qwen_image_vae.safetensors",
    "sampler": "euler",
    "scheduler": "simple",
    "steps": 8,
    "cfg": 1.0,
    "timeout": 600,        # 单次轮询总上限（秒）；20B 模型批量出图可能几十秒到几分钟
    "poll_interval": 1.5,
}


class ComfyError(RuntimeError):
    """ComfyUI 侧的失败。message 一律给用户看的中文。"""

    def __init__(self, message: str, status: int = 0, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


# ------------------------------------------------------------------ HTTP 基础

def _request(cfg: dict, method: str, path: str, data: bytes | None = None,
             headers: dict | None = None, timeout: float | None = None) -> bytes:
    url = str(cfg.get("base_url") or DEFAULT_BASE).rstrip("/") + path
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or 60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            obj = json.loads(body)
        except json.JSONDecodeError:
            obj = {}
        msg = _dig_error(obj) or body[:300]
        if e.code == 404:
            raise ComfyError(
                f"ComfyUI 上找不到 {path} —— 确认地址是 {DEFAULT_BASE} 这种根地址"
                "（不要带 /api），且 ComfyUI 正在运行。", e.code, body[:400]) from None
        raise ComfyError(f"ComfyUI 返回 HTTP {e.code}：{msg}", e.code, body[:400]) from None
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        raise ComfyError(
            f"连不上 ComfyUI（{url}）：{reason}\n"
            "请先启动 ComfyUI，确认它监听 127.0.0.1:8188。", 0) from e
    except TimeoutError as e:
        raise ComfyError(f"ComfyUI 请求超时（>{timeout or 60}s）", 0) from e


def _dig_error(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    err = obj.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        m = err.get("message") or err.get("type")
        if isinstance(m, str) and m.strip():
            return m.strip()
    for k in ("message", "detail", "msg"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # ComfyUI 的 node_errors 结构
    ne = obj.get("node_errors")
    if isinstance(ne, dict) and ne:
        bits = []
        for nid, info in list(ne.items())[:3]:
            cls = info.get("class_type") if isinstance(info, dict) else ""
            errs = info.get("errors") if isinstance(info, dict) else None
            if isinstance(errs, list) and errs:
                m = errs[0].get("message") or errs[0].get("details")
                bits.append(f"{cls}: {m}")
            else:
                bits.append(str(cls))
        return "；".join(b for b in bits if b)
    return ""


def _json(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    try:
        return json.loads(obj.decode("utf-8", "replace"))
    except Exception:
        return {}


# ------------------------------------------------------------------ 探测

def probe(cfg: dict | None = None) -> dict:
    """查一次 /system_stats，返回可用性 + 模型清单。用于设置页「测试连接」。"""
    cfg = cfg or load()
    raw = _request(cfg, "GET", "/system_stats", timeout=15)
    st = _json(raw)
    sysinfo = st.get("system") or {}
    dev = (st.get("devices") or [{}])[0]

    # 顺手把可选模型列出来，前端好做下拉
    unets, clips, vaes = [], [], []
    try:
        info = _json(_request(cfg, "GET", "/object_info/UNETLoader", timeout=15))
        unets = info.get("UNETLoader", {}).get("input", {}).get(
            "required", {}).get("unet_name", [[]])[0]
    except ComfyError:
        pass
    for node, key, bucket in (("CLIPLoader", "clip_name", clips),
                              ("VAELoader", "vae_name", vaes)):
        try:
            info = _json(_request(cfg, "GET", f"/object_info/{node}", timeout=15))
            opts = info.get(node, {}).get("input", {}).get(
                "required", {}).get(key, [[]])[0]
            bucket.extend(o for o in opts if isinstance(o, str))
        except ComfyError:
            pass

    return {
        "ok": True,
        "backend": "comfy",
        "base_url": cfg["base_url"],
        "version": sysinfo.get("comfyui_version") or "",
        "python": str(sysinfo.get("python_version") or "").split(" ")[0],
        "device": dev.get("name") or "",
        "vram_total": dev.get("vram_total") or 0,
        "vram_free": dev.get("vram_free") or 0,
        "models": {"unet": list(unets), "clip": clips, "vae": vaes},
    }


# ------------------------------------------------------------------ 配置

def load() -> dict:
    cfg = dict(DEFAULTS)
    # 地址与模型名允许由主配置（rika.py 的 config.json）覆盖
    try:
        import rika
        main = rika.load()
        if isinstance(main, dict):
            if main.get("comfy_url"):
                cfg["base_url"] = main["comfy_url"]
            sub = main.get("comfy")
            if isinstance(sub, dict):
                for k in DEFAULTS:
                    if sub.get(k) not in (None, ""):
                        cfg[k] = sub[k]
    except Exception:
        pass
    cfg["base_url"] = str(cfg.get("base_url") or DEFAULT_BASE).rstrip("/") or DEFAULT_BASE
    for k in ("steps",):
        try:
            cfg[k] = max(1, int(cfg[k]))
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS[k]
    for k in ("cfg",):
        try:
            cfg[k] = float(cfg[k])
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS[k]
    for k in ("timeout", "poll_interval"):
        try:
            cfg[k] = float(cfg[k]) if k == "poll_interval" else int(cfg[k])
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS[k]
    return cfg


# ------------------------------------------------------------------ 工作流

def _loaders(cfg: dict) -> dict:
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": cfg["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": cfg["clip"], "type": cfg["clip_type"],
                         "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae"]}},
    }


def _sampler(cfg: dict, positive, negative, latent, seed: int, denoise: float,
             steps: int | None = None) -> dict:
    return {
        "class_type": "KSampler",
        "inputs": {
            "model": ["1", 0],
            "seed": int(seed) & ((1 << 63) - 1),
            "steps": int(steps if steps is not None else cfg["steps"]),
            "cfg": float(cfg["cfg"]),
            "sampler_name": cfg["sampler"],
            "scheduler": cfg["scheduler"],
            "positive": positive,
            "negative": negative,
            "latent_image": latent,
            "denoise": float(denoise),
        },
    }


def build_t2i(cfg: dict, prompt: str, negative: str, width: int, height: int,
              seed: int, steps: int | None = None, batch: int = 1) -> dict:
    """文生图。batch>1 一次出多张（同一 prompt、不同噪声 → 帧间有变化）。"""
    g = _loaders(cfg)
    g["4"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}}
    g["5"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative or "", "clip": ["2", 0]}}
    g["6"] = {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": int(width), "height": int(height),
                         "batch_size": max(1, int(batch))}}
    g["7"] = _sampler(cfg, ["4", 0], ["5", 0], ["6", 0], seed, 1.0, steps)
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}}
    g["9"] = {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "pxkit"}}
    return g


def build_i2i(cfg: dict, prompt: str, negative: str, image_name: str,
              width: int, height: int, seed: int, denoise: float = 0.6,
              steps: int | None = None) -> dict:
    """参考图 → 变体。用 TextEncodeQwenImageEditPlus（支持 image1..image3）。

    注意：denoise<1 时 latent 必须来自参考图编码，否则等于重新生成。
    这里走 edit-plus 的 conditioning 路线（Qwen-Image 的参考图是作为
    conditioning 注入的，不是 VAE latent），所以 latent 仍用空 latent，
    denoise 保持 1.0 —— 这是 Qwen-Image-Edit 的正确用法。
    """
    g = _loaders(cfg)
    g["10"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    g["11"] = {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["2", 0], "prompt": prompt,
                          "vae": ["3", 0], "image1": ["10", 0]}}
    g["12"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative or "", "clip": ["2", 0]}}
    g["6"] = {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": int(width), "height": int(height), "batch_size": 1}}
    g["7"] = _sampler(cfg, ["11", 0], ["12", 0], ["6", 0], seed, 1.0, steps)
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}}
    g["9"] = {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "pxkit"}}
    return g


def build_i2i_latent(cfg: dict, prompt: str, negative: str, image_name: str,
                     seed: int, denoise: float = 0.55,
                     steps: int | None = None) -> dict:
    """低降噪图生图：把参考图编码成 latent 再重绘。

    和 edit-plus 的区别（实测出来的关键差异）：
      - edit-plus：参考图作为 conditioning 注入，denoise 必须 1.0，
        等于每次重画 → 同一角色的 N 帧会变成 N 个不同角色。
      - 本函数：latent 直接来自参考图，denoise 0.4~0.6 只改一部分像素
        → 角色身份能锁住，适合做「同一角色的动作帧」。
    用于动画逐帧：第一帧 t2i 出标准形象，后续每帧以它为基准低降噪重绘。
    """
    g = _loaders(cfg)
    g["10"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    g["13"] = {"class_type": "VAEEncode",
               "inputs": {"pixels": ["10", 0], "vae": ["3", 0]}}
    g["4"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}}
    g["5"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative or "", "clip": ["2", 0]}}
    g["7"] = _sampler(cfg, ["4", 0], ["5", 0], ["13", 0], seed, denoise, steps)
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}}
    g["9"] = {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "pxkit"}}
    return g


# ------------------------------------------------------------------ 提交/轮询

def submit(cfg: dict, graph: dict) -> str:
    body = json.dumps({"prompt": graph, "client_id": str(uuid.uuid4())},
                      ensure_ascii=False).encode("utf-8")
    raw = _request(cfg, "POST", "/prompt", body,
                   {"Content-Type": "application/json"}, timeout=60)
    obj = _json(raw)
    pid = obj.get("prompt_id")
    if not pid:
        err = _dig_error(obj)
        raise ComfyError("ComfyUI 拒绝了这次任务" + (f"：{err}" if err else ""), 0,
                         json.dumps(obj, ensure_ascii=False)[:600])
    return str(pid)


def wait(cfg: dict, prompt_id: str, on_tick=None) -> dict:
    """轮询 /history/{id} 直到完成。返回该 prompt 的 history 条目。"""
    deadline = time.time() + float(cfg["timeout"])
    interval = float(cfg["poll_interval"])
    last_queue = None
    while time.time() < deadline:
        time.sleep(interval)
        raw = _request(cfg, "GET", "/history/" + urllib.parse.quote(prompt_id, safe=""),
                       timeout=30)
        hist = _json(raw)
        item = hist.get(prompt_id)
        if item:
            st = (item.get("status") or {}).get("status_str")
            if on_tick:
                try:
                    on_tick(st, item)
                except Exception:
                    pass
            if st == "success":
                return item
            if st in ("error", "failed"):
                msgs = (item.get("status") or {}).get("messages") or []
                raise ComfyError("ComfyUI 执行失败："
                                 + (_dig_error({"node_errors": _node_errors(msgs)})
                                    or str(msgs)[:300]), 0, str(msgs)[:600])
        else:
            # 顺便看一眼队列，执行中时把进度回传
            if on_tick:
                try:
                    q = _json(_request(cfg, "GET", "/queue", timeout=10))
                    running = q.get("queue_running") or []
                    last_queue = "running" if running else "queued"
                    on_tick(last_queue, None)
                except Exception:
                    pass
    raise ComfyError(
        f"等待 ComfyUI 超时（>{cfg['timeout']}s）。20B 模型批量出图较慢，"
        "可减少帧数或尺寸后重试。", 0)


def _node_errors(messages) -> dict:
    """把 history.status.messages 里的 execution_error 转成 {node: {errors:[…]}}。"""
    out = {}
    for m in messages or []:
        if not isinstance(m, list) or len(m) < 2:
            continue
        kind, data = m[0], m[1]
        if kind in ("execution_error", "execution_interrupted") and isinstance(data, dict):
            nid = str(data.get("node_id") or "")
            out[nid] = {
                "class_type": data.get("node_type") or "",
                "errors": [{"message": str(data.get("exception_message") or kind)}],
            }
    return out


def fetch_images(cfg: dict, item: dict) -> list[bytes]:
    """把 history 条目里所有 SaveImage 输出取回来（PNG 字节列表，保持顺序）。"""
    metas = []
    for _nid, o in (item.get("outputs") or {}).items():
        for im in (o.get("images") or []):
            metas.append(im)
    out = []
    for im in metas:
        q = urllib.parse.urlencode({
            "filename": im.get("filename", ""),
            "subfolder": im.get("subfolder", ""),
            "type": im.get("type", "output"),
        })
        out.append(_request(cfg, "GET", "/view?" + q, timeout=120))
    return out


# ------------------------------------------------------------------ 上传参考图

def upload_image(cfg: dict, data: bytes, filename: str = "ref.png",
                 overwrite: bool = True) -> str:
    """POST /upload/image（手写 multipart）→ 返回 ComfyUI 侧的图片名。"""
    boundary = "----pxkit" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(filename)[0] or "image/png"
    parts = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode())
    parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
    parts.append(data)
    parts.append(f"\r\n--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="overwrite"\r\n\r\n')
    parts.append(b"true" if overwrite else b"false")
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    raw = _request(cfg, "POST", "/upload/image", body,
                   {"Content-Type": f"multipart/form-data; boundary={boundary}"},
                   timeout=120)
    obj = _json(raw)
    name = obj.get("name")
    if not name:
        raise ComfyError("参考图上传失败：ComfyUI 没有返回文件名", 0,
                         json.dumps(obj, ensure_ascii=False)[:400])
    sub = obj.get("subfolder") or ""
    return f"{sub}/{name}" if sub else str(name)
