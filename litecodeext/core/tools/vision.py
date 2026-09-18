"""core/tools/vision.py — vision_ocr multimodal helper.

Reads active model via lib.config._LIVE so post-switch OCR routes to new backend.
Falls back to lib.config.find_vision_fallback when the active model 不支持 vision.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx

from lib.config import API_KEY, BACKEND_URL, MODEL_ID, WORKSPACE, log


# [wire-name 2026-08-28] config 的 `id` 是显示名, 上游认的可能是别的名字
# (自建 vLLM 的 --served-model-name)。本模块直接打上游 /chat/completions,
# 不经过 lib.transport, 所以要自己翻译 —— 否则会 404
# ("The model `xxx-local` does not exist", 实测撞过)。
def _wire(mid: str) -> str:
    try:
        from lib.config import wire_model_name
        return wire_model_name(mid)
    except Exception:
        return mid

_VISION_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_VISION_MIME = {
    ".png":  "image/png",  ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",  ".bmp":  "image/bmp",
}


async def _do_vision_ocr(path: str, prompt: str, max_tokens: int = 4096) -> str:
    """
    把本地图片送到当前激活模型的 /v1/chat/completions, 走 vision content parts.
    用 lib.config._LIVE 现读 backend_url / model_id / api_key, 模型切换后立即生效。

    返回: 识别结果字符串 (纯文本). 错误以 "ERROR: ..." 开头。
    """
    import base64 as _b64
    from lib.config import _LIVE as _cfg_live

    if not path:
        return "ERROR: vision_ocr 需要 path 参数"
    p = Path(path)
    if not p.is_absolute():
        p = (WORKSPACE / path).resolve()
    if not p.exists():
        return f"ERROR: 文件不存在: {p}"
    ext = p.suffix.lower()
    if ext not in _VISION_EXTS:
        return (f"ERROR: 不支持的图片格式 {ext}. "
                f"支持: {', '.join(sorted(_VISION_EXTS))}. "
                f"PDF 请先用 pdf-ocr-pipeline skill 分页导出 PNG。")

    # [v1.4] 尺寸保护: > 2MB 的图就下采样 + 重编码.
    sz = p.stat().st_size
    img_bytes = p.read_bytes()
    if sz > 2 * 1024 * 1024:
        try:
            from PIL import Image as _Im
            import io as _io
            try:
                RESAMPLE = _Im.Resampling.LANCZOS
            except AttributeError:
                RESAMPLE = getattr(_Im, "LANCZOS", 1)
            im = _Im.open(_io.BytesIO(img_bytes))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            w, h = im.size; scale = 1280 / max(w, h)
            if scale < 1.0:
                im = im.resize((int(w*scale), int(h*scale)), RESAMPLE)
            out = _io.BytesIO()
            im.save(out, format="JPEG", quality=82, optimize=True)
            img_bytes = out.getvalue()
            ext = ".jpg"
            log.info(f"  [vision_ocr] compressed {sz}→{len(img_bytes)} bytes")
        except Exception as _ce:
            return f"ERROR: 图片过大 ({sz} bytes) 且无法压缩: {_ce}"

    b64 = _b64.b64encode(img_bytes).decode("ascii")
    mime = _VISION_MIME.get(ext, "image/png")
    data_url = f"data:{mime};base64,{b64}"

    backend_url = _cfg_live.get("backend_url", BACKEND_URL).rstrip("/")
    model_id    = _cfg_live.get("model_id", MODEL_ID)
    api_key     = _cfg_live.get("api_key", API_KEY)

    # [vision-route 2026-05-29] _do_vision_ocr 不走 transport 层, 自己判 active model
    # 是否支持 vision, 不支持就用 vision_fallback_id 临时路由 (本次调用一次性)
    if not bool(_cfg_live.get("supports_vision", False)):
        from lib.config import find_vision_fallback as _find_fb
        fb = _find_fb()
        if fb and fb.get("backend_url") and fb.get("id"):
            log.info(f"  [vision_ocr] 主模型 {model_id} 不支持 vision, "
                     f"路由 → {fb['id']} @ {fb['backend_url']}")
            model_id    = fb["id"]
            backend_url = fb["backend_url"].rstrip("/")
            api_key     = fb.get("api_key", "")

    payload = {
        "model": _wire(model_id),
        "stream": False,
        "max_tokens": max_tokens,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text",      "text": prompt},
            ],
        }],
    }

    headers = {"Content-Type": "application/json"}
    if api_key and api_key not in ("EMPTY", "", "sk-your-api-key-here"):
        headers["Authorization"] = f"Bearer {api_key}"

    url = f"{backend_url}/chat/completions"

    # [v1.1.1] 重试: 3 次指数退避, 429/5xx 可重试 (与 transport.py 一致)
    _max_retries = 3
    _retryable = {429, 500, 502, 503, 504}
    r = None
    last_err = None
    for attempt in range(_max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(url, json=payload, headers=headers)
            if r.status_code in _retryable and attempt < _max_retries:
                wait = 1.5 ** attempt
                log.warning(f"  [vision_ocr] {r.status_code} attempt {attempt+1}/{_max_retries+1}, retry in {wait:.1f}s")
                await asyncio.sleep(wait)
                continue
            break
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ReadError,
                httpx.WriteTimeout, httpx.WriteError,
                httpx.PoolTimeout, httpx.ConnectTimeout) as e:
            last_err = e
            if attempt >= _max_retries:
                return f"ERROR: vision_ocr 请求失败 (重试 {_max_retries} 次): {type(e).__name__}: {e}"
            wait = 1.5 ** attempt
            log.warning(f"  [vision_ocr] {type(e).__name__} attempt {attempt+1}/{_max_retries+1}, retry in {wait:.1f}s")
            await asyncio.sleep(wait)
        except httpx.HTTPError as e:
            return f"ERROR: vision_ocr 请求失败: {type(e).__name__}: {e}"

    if r is None:
        return f"ERROR: vision_ocr 请求失败: {last_err}"
    if r.status_code != 200:
        return f"ERROR: vision_ocr HTTP {r.status_code}: {r.text[:200]}"

    try:
        data = r.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        # 剥 <think>
        if "<think>" in content or "</think>" in content:
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            if "</think>" in content:
                content = content.split("</think>", 1)[-1].strip()
        if not content:
            rc = msg.get("reasoning_content") or ""
            if rc:
                content = rc.strip()
        return content or "ERROR: vision_ocr 返回空内容"
    except (KeyError, IndexError, ValueError) as e:
        return f"ERROR: vision_ocr 响应解析失败: {e} body={r.text[:200]}"
