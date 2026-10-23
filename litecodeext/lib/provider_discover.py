"""lib/provider_discover.py — 「能力接入」自动发现: 已经能用的能力显示成"已接好"的只读卡片。

  SearXNG   环境变量 SEARXNG_URL > config.json 的 search.searxng_url > 本机 127.0.0.1:18888 (TCP 能连上才算)
  ComfyUI   comfyui 技能记住的地址: $COMFY_ENDPOINTS_FILE 或 <WORKSPACE>/comfyui_endpoints.json
            (与 skills/comfyui-image/scripts/comfy.py 的 store_path() 同一规则; 另查 config 里的 workspace)
  内置配音  text_to_speech 工具 (本地引擎装了就用本地, 否则 edge-tts 在线免费音色)
本地 BGM 在 providers.BUILTINS 里。用户已经手动加过同一地址的卡片时不重复显示。
卡片带 auto=True / readonly=True, id 以 auto_ 开头; 「转为可编辑卡片」= 复制成普通卡片 (providers_router adopt)。
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.parse
from pathlib import Path

_CACHE: dict = {"at": 0.0, "items": []}
TTL = 30.0


def _host_port(url: str) -> tuple[str, int]:
    u = urllib.parse.urlparse(url if "://" in url else "http://" + url)
    return (u.hostname or "", u.port or (443 if u.scheme == "https" else 80))


def _tcp_ok(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _card(pid, name, kind, protocol, base_url="", note="", usage="", extra=None) -> dict:
    return {"id": pid, "name": name, "kind": kind, "protocol": protocol, "base_url": base_url, "api_key": "",
            "model": "", "extra": extra or {}, "usage": usage, "note": note, "default": False, "enabled": True,
            "readonly": True, "auto": True}


def searxng_url(cfg: dict) -> tuple[str, str]:
    """(地址, 来源说明); 没有返回 ("", "")。"""
    env = os.environ.get("SEARXNG_URL", "").strip()
    if env:
        return env.rstrip("/"), "环境变量 SEARXNG_URL"
    url = str((cfg.get("search") or {}).get("searxng_url") or "").strip()
    if url:
        return url.rstrip("/"), "设置里的 search.searxng_url"
    if _tcp_ok("127.0.0.1", 18888):
        return "http://127.0.0.1:18888", "本机 18888 端口"
    return "", ""


def comfy_store_files(cfg: dict) -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("COMFY_ENDPOINTS_FILE")
    if env:
        out.append(Path(env))
    bases = [os.environ.get("WORKSPACE") or "/tmp/litecode_workspace"]
    paths = cfg.get("paths") or {}
    agent = cfg.get("agent") or {}
    for b in (paths.get("workspace_base"), agent.get("workspace")):
        if b:
            bases.append(str(b))
    for b in bases:
        f = Path(b) / "comfyui_endpoints.json"
        if f not in out:
            out.append(f)
    return out


def comfy_endpoints(cfg: dict) -> list[dict]:
    seen, out = set(), []
    for f in comfy_store_files(cfg):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        for key, rec in d.items():
            rec = rec if isinstance(rec, dict) else {}
            url = str(rec.get("url") or key).strip().rstrip("/")
            if url and url not in seen:
                seen.add(url)
                out.append(dict(rec, url=url))
    out.sort(key=lambda r: r.get("last_used", ""), reverse=True)
    return out


def _tts_card() -> dict:
    local = False
    try:
        try:
            from core import tts as T
        except Exception:
            import tts as T   # noqa: F401  (core/ 在 sys.path 时的扁平名)
        local = bool(T._local_ok())
    except Exception:
        local = False
    how = "本地引擎 (不联网、免费)" if local else "edge-tts 在线免费音色 (本地引擎没装)"
    return _card("auto_tts", "内置配音 (" + ("本地引擎" if local else "edge-tts") + ")", "tts", "builtin",
                 note=f"LiteCode 自带的配音, 现在用的是{how}; 对话里说「把这段读出来」「配个音」就行",
                 usage="对话里让 LiteCode 配音即可 (text_to_speech 工具)。引擎按顺序自动选: 本地引擎 → edge-tts → 百炼;"
                       " 某个失败自动换下一个。text_to_speech(action='voices') 可列出全部音色。",
                 extra={"engine": "local" if local else "edge"})


def discover(cfg: dict | None = None, items: list[dict] | None = None, refresh: bool = False) -> list[dict]:
    use_cache = cfg is None and items is None
    if use_cache and not refresh and time.time() - _CACHE["at"] < TTL:
        return [dict(x) for x in _CACHE["items"]]
    if cfg is None:
        from .config import load_cfg
        cfg = load_cfg()
    if items is None:
        from .providers import load_all
        items = load_all(cfg)
    mine = {(p.get("protocol"), _host_port(p.get("base_url") or "")) for p in items if p.get("base_url")}
    out: list[dict] = []

    url, src = searxng_url(cfg)
    if url and ("searxng", _host_port(url)) not in mine:
        out.append(_card("auto_searxng", "SearXNG 搜索 (已自带)", "search", "searxng", url,
                         note=f"LiteCode 自带的开源搜索, 联网查资料默认用它 (来自{src})",
                         usage=f"SearXNG 搜索 (开源免费)。\n地址: {url}\n"
                               f"curl -s '{url}/search?q={{query}}&format=json'"))

    for rec in comfy_endpoints(cfg):
        u = rec["url"]
        hp = _host_port(u)
        if ("comfyui", hp) in mine:
            continue
        via = str(rec.get("via") or "")
        label = f"{hp[0]}:{hp[1]}"
        extra = {"via_ssh": via} if via else {}
        out.append(_card(f"auto_comfy_{hp[0].replace('.', '_')}_{hp[1]}", f"ComfyUI · {label} (已记住)", "image",
                         "comfyui", label, extra=extra,
                         note="之前用过的 ComfyUI, 让 LiteCode 画图 / 做视频时会用 comfyui 技能调用它"
                              + (f" (经跳板 {via})" if via else ""),
                         usage=f"自建 ComfyUI。\n地址: {label}\n"
                               f"curl -s http://{label}/system_stats\n"
                               "python3 skills/comfyui-image/scripts/comfy.py"
                               + (f" --via-ssh {via}" if via else "")
                               + f" run {label} -w qwen21_t2i --prompt '{{prompt}}'   # 生图; 视频见 comfyui-video 技能"))

    if not any(p.get("protocol") == "builtin" and p.get("kind") == "tts" for p in items):
        out.append(_tts_card())
    if use_cache:
        _CACHE.update(at=time.time(), items=[dict(x) for x in out])
    return out


def invalidate() -> None:
    _CACHE["at"] = 0.0
