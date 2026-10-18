# -*- coding: utf-8 -*-
"""rika 官方后端客户端。

原则：**直接采用原项目的后端**（`https://api.rika-ai.com/v1/api`），
本地不重训、不模拟、不降级，只做两件事：

  1. 读写 `config.json`（base_url / api_key / model / timeout）
  2. 用 `X-API-Key` 把请求转发给原后端

端点（均来自原站公开文档）：
    GET  /credits              余额
    POST /generate             V1 动画（4 积分）
    POST /generate-v2          V2 动画（6 积分，推荐）
    POST /generate-character   角色（1 积分）
    POST /generate-item        道具（1 积分）
    POST /generate-map         地图（2 积分）
    GET  /jobs/{gen_id}        任务状态与结果

零依赖：只用标准库 urllib。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

DEFAULT_BASE_URL = "https://api.rika-ai.com/v1/api"

# 写进 config.json 的字段及其默认值。顺序即配置页展示顺序。
DEFAULTS = {
    "base_url": DEFAULT_BASE_URL,
    "api_key": "",
    "model": "",          # 自定义模型名覆盖；留空 = 用后端默认
    "timeout": 120,       # 单次 HTTP 超时（秒）
    "poll_interval": 4,   # 轮询 /jobs 的间隔（秒）
    "poll_timeout": 900,  # 轮询总上限（秒）
}

# kind -> (path, 积分)。顺序即配置页/后端的端点清单顺序。
ENDPOINTS = {
    "v1": ("/generate", 4),
    "v2": ("/generate-v2", 6),
    "character": ("/generate-character", 1),
    "item": ("/generate-item", 1),
    "map": ("/generate-map", 2),
}

# 各端点的必填参数（用于提交前校验，避免白扣积分）
REQUIRED = {
    "v1": ("image_base64", "prompt"),
    "v2": ("image_base64", "prompt", "pixel_size"),
    "character": (),
    "item": (),
    "map": ("image_base64", "prompt"),
}


class RikaError(RuntimeError):
    """后端返回的错误。message 一律是给用户看的中文。"""

    def __init__(self, message: str, status: int = 0, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


# ------------------------------------------------------------------ 配置读写

def load() -> dict:
    """读 config.json，缺失的键补默认值。文件损坏时不抛错，回落到默认值。"""
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text("utf-8"))
            if isinstance(raw, dict):
                for k in DEFAULTS:
                    if k in raw and raw[k] is not None:
                        cfg[k] = raw[k]
        except (json.JSONDecodeError, OSError):
            pass
    cfg["base_url"] = str(cfg["base_url"] or "").strip().rstrip("/") or DEFAULT_BASE_URL
    cfg["api_key"] = str(cfg["api_key"] or "").strip()
    cfg["model"] = str(cfg["model"] or "").strip()
    for k in ("timeout", "poll_interval", "poll_timeout"):
        try:
            cfg[k] = max(1, int(cfg[k]))
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS[k]
    return cfg


def save(patch: dict) -> dict:
    """把 patch 合进现有配置后落盘，返回新配置。

    约定：值为 None 或空串的 api_key **不覆盖**已有值（避免前端空输入框把 key 清掉）。
    """
    cfg = load()
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if k == "api_key" and (v is None or str(v).strip() == ""):
            continue
        cfg[k] = v
    # 复用 load() 做的规范化
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    return load()


def mask(key: str) -> str:
    """给前端看的脱敏 key。"""
    key = key or ""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "•" * min(len(key) - 8, 12) + key[-4:]


def public_view(cfg: dict | None = None) -> dict:
    """可以安全发给浏览器的配置视图（不含明文 key）。"""
    cfg = cfg or load()
    return {
        "base_url": cfg["base_url"],
        "api_key_masked": mask(cfg["api_key"]),
        "has_key": bool(cfg["api_key"]),
        "model": cfg["model"],
        "timeout": cfg["timeout"],
        "poll_interval": cfg["poll_interval"],
        "poll_timeout": cfg["poll_timeout"],
        "default_base_url": DEFAULT_BASE_URL,
        "endpoints": [
            {"kind": k, "path": p, "credits": c} for k, (p, c) in ENDPOINTS.items()
        ],
    }


# ------------------------------------------------------------------ HTTP 转发

def _request(cfg: dict, method: str, path: str, payload: dict | None = None) -> dict:
    """发一次请求，返回解析后的 JSON。所有失败都转成 RikaError。"""
    if not cfg.get("api_key"):
        raise RikaError("还没有配置 API Key —— 打开「设置」填入后保存。", 401)

    url = cfg["base_url"].rstrip("/") + path
    data = None
    headers = {
        "X-API-Key": cfg["api_key"],
        "Accept": "application/json",
        "User-Agent": "pxkit-web/1.0",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        status = e.code
    except urllib.error.URLError as e:
        raise RikaError(f"连不上后端：{e.reason}", 0) from e
    except TimeoutError as e:
        raise RikaError(f"请求超时（>{cfg['timeout']}s）", 0) from e

    try:
        obj = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError:
        raise RikaError(
            f"后端返回的不是 JSON（HTTP {status}）", status, body[:400]
        ) from None

    if status >= 400:
        msg = ""
        if isinstance(obj, dict):
            msg = str(obj.get("error") or obj.get("message") or obj.get("detail") or "")
        if status in (401, 403):
            raise RikaError(
                f"API Key 无效或无权限（HTTP {status}）" + (f"：{msg}" if msg else ""),
                status,
                body[:400],
            )
        if status == 402:
            raise RikaError("积分不足，充值后再试（HTTP 402）" + (f"：{msg}" if msg else ""), status)
        if status == 429:
            raise RikaError("请求太频繁，稍后再试（HTTP 429）", status)
        raise RikaError(
            f"后端错误 HTTP {status}" + (f"：{msg}" if msg else ""), status, body[:400]
        )

    if not isinstance(obj, dict):
        raise RikaError("后端返回结构异常", status, body[:400])
    if obj.get("error"):
        raise RikaError(str(obj["error"]), status)
    return obj


def credits(cfg: dict | None = None) -> int:
    """GET /credits → 当前积分。"""
    cfg = cfg or load()
    obj = _request(cfg, "GET", "/credits")
    try:
        return int(obj.get("credits") or 0)
    except (TypeError, ValueError):
        raise RikaError("余额响应异常", 0, json.dumps(obj)[:400]) from None


def submit(kind: str, images: list[str], params: dict, cfg: dict | None = None) -> str:
    """POST /generate* → gen_id。

    images: base64 字符串列表（不带 data: 前缀）
    params: 该端点的其余参数（prompt / pixel_size / …）
    """
    cfg = cfg or load()
    # 先查 key，再查参数 —— 没配 key 时「去设置里填」比「缺参数」更有用
    if not cfg.get("api_key"):
        raise RikaError("还没有配置 API Key —— 打开「设置」填入后保存。", 401)
    kind = (kind or "v2").strip()
    if kind not in ENDPOINTS:
        raise RikaError(f"未知的生成类型：{kind}")

    payload = dict(params or {})
    if images:
        payload["image_base64"] = list(images)
    if cfg["model"]:
        # 自定义模型名覆盖 —— 后端不认就忽略，认了就用。
        payload.setdefault("model", cfg["model"])

    missing = [k for k in REQUIRED[kind] if not payload.get(k)]
    if missing:
        raise RikaError("缺少必填参数：" + "、".join(missing))

    path, _ = ENDPOINTS[kind]
    obj = _request(cfg, "POST", path, payload)
    gen_id = obj.get("gen_id")
    if not gen_id:
        raise RikaError("后端没有返回 gen_id", 0, json.dumps(obj)[:400])
    return str(gen_id)


def job(gen_id: str, cfg: dict | None = None) -> dict:
    """GET /jobs/{gen_id} → 任务对象。"""
    cfg = cfg or load()
    if not gen_id:
        raise RikaError("缺少 gen_id")
    return _request(cfg, "GET", "/jobs/" + urllib.parse.quote(str(gen_id), safe=""))


def wait(gen_id: str, cfg: dict | None = None, on_tick=None) -> dict:
    """轮询到 succeeded / failed / 超时。on_tick(job) 用于流式回传进度。"""
    cfg = cfg or load()
    deadline = time.time() + cfg["poll_timeout"]
    last = {}
    while True:
        last = job(gen_id, cfg)
        status = str(last.get("status") or "")
        if on_tick:
            try:
                on_tick(last)
            except Exception:
                pass
        if status in ("succeeded", "failed"):
            return last
        if time.time() >= deadline:
            raise RikaError(
                f"等待超时（>{cfg['poll_timeout']}s），任务仍在 {status or 'unknown'}；"
                f"可稍后用 gen_id={gen_id} 再查",
                0,
            )
        time.sleep(cfg["poll_interval"])


def test_connection(patch: dict | None = None) -> dict:
    """用「尚未保存」的候选配置测一次 /credits。

    patch 里带 api_key（用户刚输入、还没保存）时用它，否则用已保存的。
    成功返回 {ok, credits, base_url, model}。
    """
    cfg = load()
    for k, v in (patch or {}).items():
        if k in DEFAULTS and v not in (None, ""):
            cfg[k] = v
    cfg["base_url"] = str(cfg["base_url"]).strip().rstrip("/") or DEFAULT_BASE_URL
    cfg["api_key"] = str(cfg["api_key"]).strip()
    n = credits(cfg)
    return {"ok": True, "credits": n, "base_url": cfg["base_url"], "model": cfg["model"]}
