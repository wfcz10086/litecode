# plugins/tools/imagegen.py — S1-I (#29)
# 生图工具. 读 config.json imagegen 段, 分派到 4 个 provider:
#   comfyui / openai_dalle / sd_webui / mock (测试用)
#
# config.json 里 imagegen.enabled=false 时, image_gen 工具仍注册, 但调用返回清晰
# "disabled + 启用指引" 错误, 不静默失败.
#
# 输出走 config.imagegen.output.save_dir + emit_artifact (registry 注入),
# 未注入时 fallback 只返回文件路径.
#
# 单文件自成一体, 不 delegate 到 core.tool_dispatch (imagegen 没有 core handler).

from __future__ import annotations
import base64
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("plugins.imagegen")

CFG_PATH = Path("/opt/litecode/config.json")
DEFAULT_SAVE_DIR = "/tmp/litecode_workspace/imagegen"


def _load_cfg() -> dict:
    """读取 config.json imagegen 段. 失败返回 disabled 骨架."""
    try:
        with CFG_PATH.open("r", encoding="utf-8") as f:
            root = json.load(f)
    except Exception as e:
        log.warning("imagegen config read failed: %s", e)
        return {"enabled": False, "providers": {}, "output": {}}
    return root.get("imagegen") or {"enabled": False, "providers": {}, "output": {}}


def _save_dir() -> Path:
    cfg = _load_cfg()
    p = Path((cfg.get("output") or {}).get("save_dir") or DEFAULT_SAVE_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _fname(prompt: str, ext: str = "png") -> str:
    stub = "".join(c if c.isalnum() else "_" for c in prompt[:40]).strip("_") or "image"
    return f"{int(time.time())}_{stub}_{uuid.uuid4().hex[:6]}.{ext}"


# ── mock provider (test-only, ~90 byte 透明 PNG) ────────────
_MOCK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8"
    "AAAAASUVORK5CYII="
)


async def _run_mock(prompt: str, cfg: dict) -> tuple[bytes, str]:
    _ = (prompt, cfg)  # mock ignores both
    return base64.b64decode(_MOCK_PNG_B64), "png"


# ── openai dalle3 ────────────────────────────────────────────
async def _run_dalle(prompt: str, cfg: dict) -> tuple[bytes, str]:
    key = os.environ.get(cfg.get("api_key_env") or "OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError(f"OPENAI_API_KEY (env {cfg.get('api_key_env')}) missing")
    endpoint = cfg.get("endpoint") or "https://api.openai.com/v1/images/generations"
    body = {
        "model": cfg.get("model") or "dall-e-3",
        "prompt": prompt,
        "size": cfg.get("default_size") or "1024x1024",
        "quality": cfg.get("default_quality") or "standard",
        "n": 1,
        "response_format": "b64_json",
    }
    timeout = float(cfg.get("timeout_sec") or 60)
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(endpoint,
                         headers={"Authorization": f"Bearer {key}"},
                         json=body)
        r.raise_for_status()
        d = r.json()
    img_b64 = d["data"][0]["b64_json"]
    return base64.b64decode(img_b64), "png"


# ── stable diffusion webui (AUTOMATIC1111) ───────────────────
async def _run_sd(prompt: str, cfg: dict, defaults: dict) -> tuple[bytes, str]:
    endpoint = cfg.get("endpoint") or "http://127.0.0.1:7860/sdapi/v1/txt2img"
    w, _, h = (defaults.get("size") or "1024x1024").partition("x")
    body = {
        "prompt": prompt,
        "negative_prompt": defaults.get("negative_prompt") or "",
        "width": int(w or 1024),
        "height": int(h or 1024),
        "steps": int(defaults.get("steps") or 20),
        "cfg_scale": float(defaults.get("cfg_scale") or 7),
        "seed": int(defaults.get("seed") or -1),
    }
    timeout = float(cfg.get("timeout_sec") or 120)
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(endpoint, json=body)
        r.raise_for_status()
        d = r.json()
    img_b64 = d["images"][0]
    return base64.b64decode(img_b64), "png"


# ── comfyui (bare-bone: POST /prompt → poll /history) ────────
async def _run_comfy(prompt: str, cfg: dict, defaults: dict) -> tuple[bytes, str]:
    endpoint = (cfg.get("endpoint") or "").rstrip("/")
    tpl_name = cfg.get("workflow_template") or ""
    if not endpoint or not tpl_name:
        raise RuntimeError("comfyui provider needs endpoint + workflow_template in config")
    tpl_path = Path("/opt/litecode/skills/imagegen/workflows") / tpl_name
    if not tpl_path.is_file():
        raise RuntimeError(f"comfyui workflow template not found: {tpl_path}")
    wf = json.loads(tpl_path.read_text())
    # 极简变量替换: 只支持 {{PROMPT}} 占位符
    wf_str = json.dumps(wf).replace("{{PROMPT}}", prompt.replace('"', '\\"'))
    wf = json.loads(wf_str)
    _ = defaults  # 默认参数已在 workflow 里定义
    timeout = float(cfg.get("timeout_sec") or 180)
    poll = float(cfg.get("poll_interval_sec") or 2)
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{endpoint}/prompt", json={"prompt": wf})
        r.raise_for_status()
        pid = r.json().get("prompt_id")
        if not pid:
            raise RuntimeError("comfyui: no prompt_id in POST /prompt response")
        # poll history
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = await c.get(f"{endpoint}/history/{pid}")
            if h.status_code == 200:
                hist = h.json().get(pid) or {}
                outputs = hist.get("outputs") or {}
                for _, node_out in outputs.items():
                    imgs = node_out.get("images") or []
                    if imgs:
                        info = imgs[0]
                        params = {"filename": info["filename"],
                                  "subfolder": info.get("subfolder", ""),
                                  "type": info.get("type", "output")}
                        img_r = await c.get(f"{endpoint}/view", params=params)
                        img_r.raise_for_status()
                        return img_r.content, "png"
            import asyncio
            await asyncio.sleep(poll)
    raise TimeoutError(f"comfyui prompt {pid} timeout")


_DISPATCH = {
    "mock": _run_mock,
    "openai_dalle": _run_dalle,
    "sd_webui": _run_sd,
    "comfyui": _run_comfy,
}


async def _image_gen(args: dict, ctx: dict) -> dict:
    """image_gen tool 入口."""
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt required"}

    cfg = _load_cfg()
    if not cfg.get("enabled"):
        return {"ok": False,
                "error": "imagegen disabled — 在 config.json 设 imagegen.enabled=true 并配置 default_provider"}

    prov_name = (args.get("provider") or cfg.get("default_provider") or "").strip()
    providers = cfg.get("providers") or {}
    prov_cfg = providers.get(prov_name)
    if not prov_cfg:
        return {"ok": False,
                "error": f"provider '{prov_name}' 未在 config.imagegen.providers 中配置; "
                         f"可选: {list(providers.keys())}"}

    prov_type = prov_cfg.get("type") or prov_name
    runner = _DISPATCH.get(prov_type)
    if not runner:
        return {"ok": False, "error": f"unknown provider type: {prov_type}"}

    defaults = dict(cfg.get("defaults") or {})
    # 用户参数覆盖 defaults
    for k in ("size", "steps", "cfg_scale", "seed", "negative_prompt"):
        if k in args:
            defaults[k] = args[k]

    try:
        if prov_type == "mock" or prov_type == "openai_dalle":
            img_bytes, ext = await runner(prompt, prov_cfg)
        else:
            img_bytes, ext = await runner(prompt, prov_cfg, defaults)
    except Exception as e:
        log.exception("imagegen run failed")
        return {"ok": False, "error": f"{prov_type} failed: {e!r}"}

    fname = _fname(prompt, ext)
    save_path = _save_dir() / fname
    save_path.write_bytes(img_bytes)

    out: dict[str, Any] = {
        "ok": True,
        "provider": prov_type,
        "prompt": prompt,
        "path": str(save_path),
        "bytes": len(img_bytes),
    }

    # 落 artifact (若 registry 注入了 emit_artifact)
    emit = (ctx or {}).get("emit_artifact")
    if callable(emit):
        try:
            aid = emit(kind=f"image/{ext}", data=img_bytes, name=fname,
                       meta={"prompt": prompt, "provider": prov_type})
            out["artifact_id"] = aid
        except Exception as e:
            log.warning("emit_artifact failed: %s", e)

    return out


TOOLS = [
    {
        "name": "image_gen",
        "description": (
            "文本生图. 从 config.json imagegen 段读 provider 配置 (comfyui/dalle3/sd_webui/mock). "
            "config 里 enabled=false 时会返回启用指引. 返回 png 文件路径 + artifact_id."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "prompt":          {"type": "string",  "description": "英文提示词更稳"},
                "provider":        {"type": "string",  "description": "覆盖 default_provider, 可选 comfyui_remote/dalle3/sd_api/mock"},
                "size":            {"type": "string",  "description": "如 1024x1024, 覆盖 defaults.size"},
                "steps":           {"type": "integer", "description": "采样步数, 覆盖 defaults.steps"},
                "cfg_scale":       {"type": "number",  "description": "CFG scale, 覆盖 defaults.cfg_scale"},
                "seed":            {"type": "integer", "description": "-1 随机"},
                "negative_prompt": {"type": "string",  "description": "负面提示词"},
            },
            "required": ["prompt"],
        },
        "capabilities": {"safe": True, "long_running": True, "produces_artifact": True, "cost": "heavy"},
        "run": lambda args, ctx: _image_gen(args, ctx),
    },
]

VERSION = "0.1.0"
