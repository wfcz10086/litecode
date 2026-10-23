"""lib/provider_gen.py — generate_image / generate_video / generate_music 的路由与执行 (卡片见 lib/providers.py)。

路由: providers.candidates(kind, provider) —— 指定的 → 同类默认 → 其余启用的; 一张失败自动试下一张。
  openai_images  POST {base}/images/generations, 返回 url 或 b64 都落盘 (兼容 data[] / images[] 两种形状)
  comfyui        委托 skills/comfyui-image/scripts/comfy.py run (生图同步等结果; 生视频后台跑, 立即返回日志路径)
  builtin        音乐: skills/bgm-music/scripts/bgm_gen.py 本地生成
  http 等        不自动调用, 返回完整明文卡片, 让 agent 按 usage 自己 curl
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from . import providers as P

ROOT = Path(__file__).resolve().parent.parent
COMFY = ROOT / "skills" / "comfyui-image" / "scripts" / "comfy.py"
BGM = ROOT / "skills" / "bgm-music" / "scripts" / "bgm_gen.py"


class Manual(Exception):
    """这张卡没有内置适配, 需要 agent 按明文用法自己调用。"""


def _stub(prompt: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "_", prompt or "")[:24].strip("_")
    return s or "out"


def _fname(prompt: str, ext: str) -> str:
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{_stub(prompt)}_{uuid.uuid4().hex[:4]}.{ext}"


def _ext_of(ctype: str, data: bytes, default: str = "png") -> str:
    ctype = (ctype or "").lower()
    for k, e in (("png", "png"), ("jpeg", "jpg"), ("jpg", "jpg"), ("webp", "webp"), ("gif", "gif"),
                 ("mp4", "mp4"), ("mpeg", "mp3"), ("wav", "wav")):
        if k in ctype:
            return e
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return default


def manual_hint(p: dict) -> str:
    return ("这张接入没有内置适配, 请按下面的明文用法自己调用 (execute_shell 跑 curl, 产物存到工作目录后把路径写进回复):\n"
            + P.card_text(p))


# ── 生图 ──────────────────────────────────────────────────────

def _pick_images(d: dict) -> list[dict]:
    """兼容 OpenAI data[{url|b64_json}] 与 硅基流动 images[{url}] 等形状。"""
    out = []
    for key in ("data", "images"):
        for it in d.get(key) or []:
            if isinstance(it, dict):
                out.append(it)
            elif isinstance(it, str):
                out.append({"url": it} if it.startswith("http") else {"b64_json": it})
    return out


async def _openai_image(p: dict, prompt: str, width: int, height: int, out_dir: Path) -> list[str]:
    base = (p.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("没填地址 base_url")
    ex = p.get("extra") or {}
    body = {"prompt": prompt, "n": 1}
    if p.get("model"):
        body["model"] = p["model"]
    size_field = str(ex.get("size_field") or "size")
    if size_field != "none":
        body[size_field] = str(ex.get("size") or f"{width}x{height}")
    if isinstance(ex.get("body"), dict):   # 平台特有参数 (如 response_format / watermark) 合进请求体
        body.update(ex["body"])
    headers = {"Authorization": f"Bearer {p.get('api_key') or 'EMPTY'}", "Content-Type": "application/json"}
    tmo = float(ex.get("timeout") or 300)
    async with httpx.AsyncClient(timeout=tmo, follow_redirects=True) as c:
        r = await c.post(base + "/images/generations", headers=headers, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        imgs = _pick_images(r.json())
        if not imgs:
            raise RuntimeError("返回里没有图片: " + r.text[:300])
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for it in imgs:
            if it.get("b64_json"):
                data, ctype = base64.b64decode(it["b64_json"]), ""
            elif it.get("url"):
                g = await c.get(it["url"])
                g.raise_for_status()
                data, ctype = g.content, g.headers.get("content-type", "")
            else:
                continue
            f = out_dir / _fname(prompt, _ext_of(ctype, data))
            f.write_bytes(data)
            files.append(str(f))
    if not files:
        raise RuntimeError("图片下载失败")
    return files


def _comfy_args(p: dict, workflow: str, prompt: str, out_dir: Path, extra_args: list[str]) -> list[str]:
    ex = p.get("extra") or {}
    if not p.get("base_url"):
        raise RuntimeError("没填 ComfyUI 地址 (host:port)")
    args = [sys.executable, str(COMFY)]
    if ex.get("via_ssh"):
        args += ["--via-ssh", str(ex["via_ssh"])]
    args += ["run", p["base_url"], "-w", str(ex.get("workflow") or workflow), "--prompt", prompt,
             "-o", str(out_dir)] + extra_args
    if str(ex.get("wait_idle", "")).lower() in ("no", "false", "0"):
        args.append("--no-wait-idle")
    return args


def parse_comfy_output(text: str) -> tuple[list[str], dict]:
    files = [m.group(1) for m in re.finditer(r"^\[out\] (\S+)", text, re.M)]
    res = {}
    m = re.search(r"^RESULT (\{.*\})\s*$", text, re.M)
    if m:
        try:
            res = json.loads(m.group(1))
        except ValueError:
            res = {}
    return files, res


async def _comfy_image(p: dict, prompt: str, width: int, height: int, out_dir: Path) -> list[str]:
    args = _comfy_args(p, "qwen21_t2i", prompt, out_dir, ["--width", str(width), "--height", str(height)])
    tmo = float((p.get("extra") or {}).get("timeout") or 1800)
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=tmo + 60)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"ComfyUI 超过 {tmo:.0f} 秒没出图")
    text = out.decode(errors="replace")
    files, _ = parse_comfy_output(text)
    if proc.returncode != 0 or not files:
        tail = "\n".join(text.strip().splitlines()[-6:])
        raise RuntimeError(f"comfy.py 退出码 {proc.returncode}: {tail}")
    return files


async def generate_image(prompt: str, width: int = 1024, height: int = 1024, provider: str | None = None,
                         out_dir: Path | None = None, items: list | None = None) -> dict:
    """返回 {ok, files, provider, tried:[(name, err)], manual?: 卡片明文}。"""
    if not (prompt or "").strip():
        raise ValueError("prompt 不能为空")
    out_dir = Path(out_dir or "/tmp/litecode_workspace/images")
    cands = P.candidates("image", provider, items)
    tried = []
    for p in cands:
        try:
            if p["protocol"] == "openai_images":
                files = await _openai_image(p, prompt, width, height, out_dir)
            elif p["protocol"] == "comfyui":
                files = await _comfy_image(p, prompt, width, height, out_dir)
            else:
                return {"ok": False, "manual": manual_hint(p), "provider": p["name"], "tried": tried}
            return {"ok": True, "files": files, "provider": p["name"], "tried": tried}
        except Exception as e:
            tried.append((p["name"], f"{type(e).__name__}: {e}"[:400]))
    return {"ok": False, "files": [], "provider": "", "tried": tried}


# ── 生视频 (异步) ─────────────────────────────────────────────

def _spawn(args: list[str], log_file: Path, sid: str | None) -> str:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "wb") as fh:
        proc = subprocess.Popen(args, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, env={**os.environ})
    pid = str(proc.pid)
    try:   # 进会话后台进程表: list_bg 能看到, kill_bg 能停
        try:
            from tools.bg import _register_bg
        except ImportError:
            from core.tools.bg import _register_bg
        _register_bg(pid, " ".join(args[:6]), log_file, sid=sid or "_orphan")
    except Exception:
        pass
    return pid


async def generate_video(prompt: str, width: int | None = None, height: int | None = None,
                         frames: int | None = None, image: str | None = None, provider: str | None = None,
                         out_dir: Path | None = None, sid: str | None = None, items: list | None = None) -> dict:
    """comfyui: 后台跑 comfy.py, 立即返回 {ok, async: True, pid, log}; http: 返回明文卡片。"""
    if not (prompt or "").strip():
        raise ValueError("prompt 不能为空")
    out_dir = Path(out_dir or "/tmp/litecode_workspace/videos")
    tried = []
    for p in P.candidates("video", provider, items):
        try:
            if p["protocol"] == "comfyui":
                extra = []
                for k, v in (("--width", width), ("--height", height), ("--frames", frames)):
                    if v:
                        extra += [k, str(int(v))]
                wf = "h3_i2v" if image else "h3_t2v"
                if image:
                    extra += ["--input", f"first={image}"]
                args = _comfy_args(p, wf, prompt, out_dir, extra)
                log_file = out_dir / f"video_{time.strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}.log"
                pid = _spawn(args, log_file, sid)
                return {"ok": True, "async": True, "pid": pid, "log": str(log_file), "provider": p["name"],
                        "tried": tried}
            return {"ok": False, "manual": manual_hint(p), "provider": p["name"], "tried": tried}
        except Exception as e:
            tried.append((p["name"], f"{type(e).__name__}: {e}"[:400]))
    return {"ok": False, "provider": "", "tried": tried}


# ── 音乐 ──────────────────────────────────────────────────────

async def generate_music(prompt: str, duration: int = 30, mood: str | None = None, provider: str | None = None,
                         out_dir: Path | None = None, items: list | None = None) -> dict:
    out_dir = Path(out_dir or "/tmp/litecode_workspace/music")
    tried = []
    for p in P.candidates("music", provider, items):   # 末尾自带本机 bgm-music 兜底 (providers.BUILTINS)
        try:
            if p["protocol"] == "builtin":
                out_dir.mkdir(parents=True, exist_ok=True)
                f = out_dir / _fname(prompt or mood or "bgm", "mp3")
                args = [sys.executable, str(BGM), "--mood", str(mood or "upbeat"),
                        "--duration", str(max(5, min(int(duration or 30), 600))), "-o", str(f)]
                proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.STDOUT)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
                if proc.returncode != 0 or not f.exists():
                    raise RuntimeError(out.decode(errors="replace")[-400:])
                return {"ok": True, "files": [str(f)], "provider": p["name"], "tried": tried,
                        "note": "本地程序生成的合成器配乐, 无第三方版权"}
            return {"ok": False, "manual": manual_hint(p), "provider": p["name"], "tried": tried}
        except Exception as e:
            tried.append((p["name"], f"{type(e).__name__}: {e}"[:400]))
    return {"ok": False, "files": [], "provider": "", "tried": tried}
