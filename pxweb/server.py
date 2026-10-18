# -*- coding: utf-8 -*-
"""pxkit 本地服务 —— 仿 rika-ai.com 前端的像素素材工具。

纯标准库（http.server），零依赖。算法全部来自已验证的 pxkit 包。

启动：
    python server.py            # 默认 127.0.0.1:8765，自动开浏览器
    python server.py --port 9000 --no-open

接口：
    GET  /                       前端页面
    GET  /static/*               静态资源
    GET  /work/<id>.png          处理结果
    POST /api/open               导入图片（dataURL）→ {key, w, h}
    POST /api/op                 执行操作 → {key, w, h, info, palette?, tiles?}
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
WORK = ROOT / "work"

# pxkit 在隔壁目录
sys.path.insert(0, str(ROOT.parent / "pxkit"))
import numpy as np                                          # noqa: E402
from PIL import Image                                       # noqa: E402

from pxkit import background, grid, imgio, palette, sheet   # noqa: E402

WORK.mkdir(parents=True, exist_ok=True)
STATIC.mkdir(parents=True, exist_ok=True)

# key -> (H,W,4) uint8
_STORE: dict[str, np.ndarray] = {}


# ---------------------------------------------------------------- 存取

def _put(rgba: np.ndarray) -> str:
    """存进内存 + 落盘，返回 key。"""
    key = uuid.uuid4().hex[:16]
    _STORE[key] = rgba
    if len(_STORE) > 60:                                   # 简单淘汰
        for k in list(_STORE)[:20]:
            _STORE.pop(k, None)
    imgio.save_rgba(rgba, WORK / f"{key}.png")
    return key


def _get(key: str) -> np.ndarray:
    if key in _STORE:
        return _STORE[key]
    p = WORK / f"{key}.png"
    if not p.is_file():
        raise KeyError(f"找不到图片 {key}")
    rgba = imgio.load_rgba(p)
    _STORE[key] = rgba
    return rgba


def _decode_dataurl(data: str) -> np.ndarray:
    raw = data.split(",", 1)[-1]
    buf = io.BytesIO(base64.b64decode(raw))
    im = Image.open(buf).convert("RGBA")
    return np.array(im, dtype=np.uint8)


def _meta(key: str, rgba: np.ndarray) -> dict:
    return {"key": key, "w": int(rgba.shape[1]), "h": int(rgba.shape[0])}


# ---------------------------------------------------------------- 操作

def op_detect(rgba, p):
    block, score, cands = grid.auto_block_size(rgba)
    return rgba, {"block": int(block), "score": round(float(score), 4),
                  "candidates": {int(k): round(float(v), 4)
                                 for k, v in sorted(cands.items())}}


def op_depixelate(rgba, p):
    block = int(p.get("block") or 0)
    out, info = grid.depixelate(rgba, block=block)
    clean = {k: (v if isinstance(v, (int, float, str, bool, list, dict, type(None)))
                 else str(v)) for k, v in info.items()}
    return out, clean


def op_palette(rgba, p):
    """只抽取调色板，不改图。"""
    want = int(p.get("colors") or 16)
    pal = palette.extract_palette(rgba, want)
    return rgba, {"palette": palette.palette_report(pal), "count": len(pal)}


def op_quantize(rgba, p):
    want = int(p.get("colors") or 16)
    bg = p.get("bg")
    bg_rgb = tuple(bg[:3]) if bg else None
    out, pal = palette.quantize(rgba, want, bg_rgb=bg_rgb)
    return out, {"palette": palette.palette_report(pal), "count": len(pal)}


def op_removebg(rgba, p):
    bg = p.get("bg")
    bg_rgb = tuple(int(v) for v in bg[:3]) if bg else None
    tol = float(p.get("tol") or 12.0)
    out = background.remove_background(rgba, bg=bg_rgb, tol=tol)
    corner = background.corner_background(rgba)
    return out, {"bg_corner": list(corner[:3]),
                 "tol": tol,
                 "transparent_ratio": round(
                     float((out[..., 3] < 128).mean()), 4)}


def op_slice(rgba, p):
    rows = max(1, int(p.get("rows") or 4))
    cols = max(1, int(p.get("cols") or 4))
    tiles = sheet.slice_uniform(rgba, rows, cols)
    out = []
    for name, tile in tiles:
        out.append({"name": name, "key": _put(tile),
                    "w": int(tile.shape[1]), "h": int(tile.shape[0])})
    return rgba, {"tiles": out, "rows": rows, "cols": cols}


def op_pack(rgba, p):
    keys = p.get("keys") or []
    cols = max(1, int(p.get("cols") or 4))
    gap = int(p.get("gap") or 0)
    tiles = [(k, _get(k)) for k in keys]
    if not tiles:
        raise ValueError("没有可拼的图元")
    packed, info = sheet.pack_sheet(tiles, cols=cols, gap=gap)
    return packed, info


def op_pad(rgba, p):
    px = int(p.get("px") or 4)
    out = sheet.trim_rgba(rgba, padding=px, square=bool(p.get("square")))
    return out, {"pad": px, "square": bool(p.get("square"))}


def op_scale(rgba, p):
    s = max(1, int(p.get("scale") or 1))
    return sheet.resize_nearest(rgba, s), {"scale": s}


def op_clean_bg(rgba, p):
    out = background.despill_edges(rgba, iterations=int(p.get("iter") or 1))
    return out, {"iterations": int(p.get("iter") or 1)}


def op_probe(rgba, p):
    """只读探测：四角背景色 + 颜色数 + 透明占比，不改图。"""
    corner = background.corner_background(rgba)
    flat = rgba.reshape(-1, 4)
    opaque = flat[flat[:, 3] >= 128][:, :3]
    uniq = int(len({(int(r), int(g), int(b)) for r, g, b in opaque}))
    return rgba, {
        "bg_corner": [int(v) for v in corner[:3]],
        "bg_alpha": int(corner[3]),
        "color_count": uniq,
        "transparent_ratio": round(float((rgba[..., 3] < 128).mean()), 4),
        "w": int(rgba.shape[1]),
        "h": int(rgba.shape[0]),
    }


OPS = {
    "detect": op_detect,
    "probe": op_probe,
    "depixelate": op_depixelate,
    "palette": op_palette,
    "quantize": op_quantize,
    "removebg": op_removebg,
    "slice": op_slice,
    "pack": op_pack,
    "pad": op_pad,
    "scale": op_scale,
    "clean_bg": op_clean_bg,
}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "pxkit/1.0"
    protocol_version = "HTTP/1.0"

    # ---- 工具 ----
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _file(self, path: Path):
        if not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".woff2": "font/woff2",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".json": "application/json; charset=utf-8",
        }.get(path.suffix.lower(), "application/octet-stream")
        self._send(200, path.read_bytes(), ctype)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def log_message(self, fmt, *args):                      # 静音
        pass

    # ---- 路由 ----
    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p in ("/", "/index.html"):
            self._file(STATIC / "index.html")
        elif p.startswith("/static/"):
            rel = p[len("/static/"):]
            target = (STATIC / rel).resolve()
            if not str(target).startswith(str(STATIC.resolve())):
                self._json({"error": "bad path"}, 400)
                return
            self._file(target)
        elif p.startswith("/work/"):
            name = p[len("/work/"):]
            if "/" in name or "\\" in name or not name.endswith(".png"):
                self._json({"error": "bad path"}, 400)
                return
            self._file(WORK / name)
        elif p == "/api/ops":
            self._json({"ops": sorted(OPS)})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        try:
            body = self._body()
            if p == "/api/open":
                rgba = _decode_dataurl(body.get("data") or "")
                key = _put(rgba)
                block, score, _ = grid.auto_block_size(rgba)
                self._json({**_meta(key, rgba), "block": int(block),
                            "score": round(float(score), 4)})
                return
            if p == "/api/op":
                op = body.get("op") or ""
                if op not in OPS:
                    self._json({"error": f"未知操作 {op}"}, 400)
                    return
                src = _get(body.get("key") or "")
                out, info = OPS[op](src, body.get("params") or {})
                same = out is src
                key = body.get("key") if same else _put(out)
                self._json({**_meta(key, out), "info": info, "same": same})
                return
            self._json({"error": "not found"}, 404)
        except KeyError as e:
            self._json({"error": f"图片不存在：{e}"}, 404)
        except Exception as e:                              # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    url = f"http://{a.host}:{a.port}/"
    print(f"pxkit 服务已启动  {url}")
    print("Ctrl+C 停止")
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
