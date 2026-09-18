"""gateway/openai_compat.py — OpenAI 兼容层 (/v1/chat/completions, /v1/models)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from lib.sse import _mkid, sse_done, sse_error, sse_stop, sse_usage

from ._deps import _srv, require_auth

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    require_auth(request)
    srv = _srv()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # [P46 测试模式] ?__test_empty=1 → 强制走空响应路径验 sse_error 触发
    if request.query_params.get("__test_empty") == "1":
        async def _empty_stream():
            yield sse_error("LLM 返回空 (可能上游超时/限流), 请重试", retry_hint=True)
            yield sse_usage(0, 0, 1, 0.1, body.get("model", srv.MODEL_ID))
            yield sse_stop(body.get("model", srv.MODEL_ID))
            yield sse_done()
            try:
                _ep = Path("/tmp/litecode_workspace/telemetry/empty_response.jsonl")
                _ep.parent.mkdir(parents=True, exist_ok=True)
                with open(_ep, "a") as _f:
                    _f.write(json.dumps({
                        "ts": time.time(), "sid": body.get("user", "?"),
                        "model": body.get("model", srv.MODEL_ID),
                        "duration": 0.1, "reason": "test_empty mode",
                    }) + "\n")
            except Exception:
                pass
        return StreamingResponse(_empty_stream(), media_type="text/event-stream")

    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="No messages")

    stream     = body.get("stream", True)
    session_id = body.get("user") or None
    model      = body.get("model", srv.MODEL_ID)
    # [2026-09-03] 权谋/多方博弈推演模式 chip (前端 strategist_mode / 兼容旧名 scenario_mode)
    strategist = bool(body.get("strategist_mode") or body.get("scenario_mode"))

    # [v1.4] 保留原 content (multimodal list) 同时抽纯文本给下游 skill/trace/memory.
    # 之前只抽文本扔掉 image_url 导致多模态失效 — wechat 发图一直 OCR 走歪.
    user_message = ""
    user_content_raw: Any = None
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if c is None:
                continue
            user_content_raw = c
            if isinstance(c, list):
                user_message = " ".join(
                    p.get("text", "") for p in c
                    if isinstance(p, dict) and p.get("type") == "text"
                ).strip()
            elif isinstance(c, str):
                user_message = c.strip()
            else:
                user_message = str(c).strip()
            break

    if not user_message and user_content_raw is None:
        raise HTTPException(status_code=400, detail="No user message content")
    # 纯图 + 空文本 → 给 skill/trace 一个占位
    if not user_message and isinstance(user_content_raw, list):
        user_message = "(图片输入)"

    gen = srv.agent_stream(user_message, session_id, model,
                           user_content=user_content_raw, strategist=strategist)

    if stream:
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
    else:
        full = ""
        async for chunk in gen:
            if chunk.startswith("data: ") and chunk[6:].strip() not in ("[DONE]", ""):
                try:
                    d  = json.loads(chunk[6:])
                    ct = d["choices"][0]["delta"].get("content") or ""
                    if ct:
                        full += ct
                except Exception:
                    pass
        return JSONResponse({
            "id":      f"cmpl-{_mkid()}",
            "object":  "chat.completion",
            "model":   model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": full},
                         "finish_reason": "stop"}]
        })


@router.get("/v1/models")
async def list_models(request: Request):
    require_auth(request)
    srv = _srv()
    return {"object": "list",
            "data": [{"id": srv.MODEL_ID, "object": "model", "owned_by": "vllm"}]}
