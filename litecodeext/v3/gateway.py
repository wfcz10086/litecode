"""V3 API Gateway — FastAPI router.

Exposed endpoints (mountable at any prefix; default nothing):
  GET  /v3/providers       list registered LLM providers + configured models
  GET  /v3/skills          list all skills (name/description/enabled)
  PUT  /v3/skills/{name}   {"enabled": bool} -> set flag
  GET  /v3/nodes           list all workflow node types
  POST /v3/chat            {model, messages, thinking?, stream?} -> SSE UnifiedChunk stream
  POST /v3/workflow/run    {workflow: WorkflowSpec dict, inputs: dict, stream?: bool}
  POST /v1/chat/completions OpenAI-compatible passthrough to /v3/chat

All endpoints share a single bearer-token check matching config.json server.token.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config_bridge import build_provider, list_models, load_raw_config
from .providers.base import ThinkingSpec, UnifiedChunk, UnifiedMessage, UnifiedRequest
from .providers.registry import llm_registry
from .skill_registry import skill_registry
from .workflow import WorkflowEngine, WorkflowSpec, node_registry

# Eager side-effect imports — populates llm_registry / vision_registry / node_registry
from .providers import llm as _llm_pkg  # noqa: F401
from .providers import vision as _vision_pkg  # noqa: F401
from .workflow import nodes as _nodes_pkg  # noqa: F401

router = APIRouter()


# ---------- Auth ----------

def _expected_token() -> str:
    cfg = load_raw_config()
    return (cfg.get("server") or {}).get("token", "")


async def require_token(request: Request) -> str:
    exp = _expected_token()
    if not exp:
        return ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        got = auth[7:].strip()
    else:
        got = request.headers.get("x-api-key", "").strip()
    if got != exp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return got


# ---------- Schemas ----------

class ChatMessageIn(BaseModel):
    role: str
    content: Any = ""
    name: str | None = None
    tool_call_id: str | None = None


class ThinkingIn(BaseModel):
    effort: str = "off"
    budget_tokens: int | None = None


class ChatIn(BaseModel):
    model: str
    messages: list[ChatMessageIn]
    thinking: ThinkingIn | None = None
    stream: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None


class WorkflowRunIn(BaseModel):
    workflow: dict
    inputs: dict = Field(default_factory=dict)
    stream: bool = True


class SkillFlagIn(BaseModel):
    enabled: bool


# ---------- Info endpoints ----------

@router.get("/v3/providers", dependencies=[Depends(require_token)])
async def list_providers() -> dict:
    return {
        "providers": llm_registry.list(),
        "models": [{"id": m.get("id"), "vision": m.get("supports_vision"), "thinking": m.get("enable_thinking"), "context": m.get("context_window")} for m in list_models()],
    }


@router.get("/v3/skills", dependencies=[Depends(require_token)])
async def list_skills() -> dict:
    if not skill_registry.list():
        skill_registry.scan()
    return {"skills": skill_registry.as_json()}


@router.put("/v3/skills/{name}", dependencies=[Depends(require_token)])
async def set_skill(name: str, body: SkillFlagIn) -> dict:
    if not skill_registry.list():
        skill_registry.scan()
    try:
        skill_registry.set_enabled(name, body.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"skill {name!r} not found")
    return {"name": name, "enabled": body.enabled}


@router.get("/v3/nodes", dependencies=[Depends(require_token)])
async def list_nodes() -> dict:
    return {"nodes": node_registry.all_meta()}


# ---------- SSE helpers ----------

def _chunk_to_sse(chunk: UnifiedChunk) -> str:
    payload = {"kind": chunk.kind, "delta": chunk.delta, "payload": chunk.payload, "round": chunk.round}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_chat(req: UnifiedRequest) -> AsyncIterator[str]:
    provider = build_provider(req.model)
    try:
        async for chunk in provider.stream(req):
            yield _chunk_to_sse(chunk)
            if chunk.kind == "done":
                return
    except Exception as e:
        yield _chunk_to_sse(UnifiedChunk.error(f"{type(e).__name__}: {e}"))
        yield _chunk_to_sse(UnifiedChunk.done({"reason": "exception"}))


# ---------- /v3/chat ----------

def _to_unified(body: ChatIn) -> UnifiedRequest:
    msgs = [UnifiedMessage(role=m.role, content=m.content, name=m.name, tool_call_id=m.tool_call_id) for m in body.messages]
    thinking = None
    if body.thinking:
        thinking = ThinkingSpec(effort=body.thinking.effort, budget_tokens=body.thinking.budget_tokens)  # type: ignore[arg-type]
    return UnifiedRequest(
        model=body.model,
        messages=msgs,
        thinking=thinking,
        stream=body.stream,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        stop=body.stop,
    )


@router.post("/v3/chat", dependencies=[Depends(require_token)])
async def v3_chat(body: ChatIn):
    req = _to_unified(body)
    if body.stream:
        return StreamingResponse(_stream_chat(req), media_type="text/event-stream")
    # non-streaming: accumulate
    provider = build_provider(body.model)
    text = ""; thinking = ""; usage: dict | None = None
    async for chunk in provider.stream(req):
        if chunk.kind == "content" and chunk.delta: text += chunk.delta
        elif chunk.kind == "thinking" and chunk.delta: thinking += chunk.delta
        elif chunk.kind == "usage": usage = chunk.payload
        elif chunk.kind == "error": raise HTTPException(500, detail=chunk.delta)
        elif chunk.kind == "done": break
    return {"model": body.model, "content": text, "thinking": thinking, "usage": usage}


# ---------- /v3/workflow/run ----------

@router.post("/v3/workflow/run", dependencies=[Depends(require_token)])
async def workflow_run(body: WorkflowRunIn):
    wf = WorkflowSpec.from_dict(body.workflow)
    engine = WorkflowEngine()
    if body.stream:
        async def gen() -> AsyncIterator[str]:
            async for chunk in engine.run(wf, body.inputs):
                yield _chunk_to_sse(chunk)
        return StreamingResponse(gen(), media_type="text/event-stream")
    outputs: dict = {}
    async for chunk in engine.run(wf, body.inputs):
        if chunk.kind == "done" and chunk.payload:
            outputs = chunk.payload.get("outputs", {})
        elif chunk.kind == "error":
            raise HTTPException(500, detail=chunk.delta)
    return {"workflow": wf.id, "outputs": outputs}


# ---------- OpenAI-compatible passthrough ----------

@router.post("/v1/chat/completions", dependencies=[Depends(require_token)])
async def openai_compat(request: Request):
    body = await request.json()
    stream = bool(body.get("stream", False))
    model = body.get("model") or (list_models()[0].get("id") if list_models() else "")
    if not model:
        raise HTTPException(400, "no model")
    msgs = [ChatMessageIn(role=m.get("role", "user"), content=m.get("content", "")) for m in body.get("messages", [])]
    ci = ChatIn(
        model=model, messages=msgs, stream=stream,
        temperature=body.get("temperature"), max_tokens=body.get("max_tokens"),
        stop=body.get("stop"),
    )
    req = _to_unified(ci)
    resp_id = f"chatcmpl-v3-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if stream:
        async def _stream_openai() -> AsyncIterator[str]:
            provider = build_provider(model)
            try:
                async for chunk in provider.stream(req):
                    if chunk.kind in ("content", "thinking") and chunk.delta:
                        delta_field = "reasoning_content" if chunk.kind == "thinking" else "content"
                        evt = {
                            "id": resp_id, "object": "chat.completion.chunk",
                            "created": created, "model": model,
                            "choices": [{"index": 0, "delta": {delta_field: chunk.delta}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                    elif chunk.kind == "done":
                        evt = {
                            "id": resp_id, "object": "chat.completion.chunk",
                            "created": created, "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        }
                        yield f"data: {json.dumps(evt)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    elif chunk.kind == "error":
                        evt = {"error": {"message": chunk.delta}}
                        yield f"data: {json.dumps(evt)}\n\n"
                        return
            except Exception as e:
                yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n"

        return StreamingResponse(_stream_openai(), media_type="text/event-stream")

    provider = build_provider(model)
    text = ""; thinking = ""
    async for chunk in provider.stream(req):
        if chunk.kind == "content" and chunk.delta: text += chunk.delta
        elif chunk.kind == "thinking" and chunk.delta: thinking += chunk.delta
        elif chunk.kind == "done": break
        elif chunk.kind == "error":
            return JSONResponse({"error": {"message": chunk.delta}}, status_code=500)
    return {
        "id": resp_id, "object": "chat.completion", "created": created, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text, "reasoning_content": thinking}, "finish_reason": "stop"}],
    }


def create_app():
    """Standalone FastAPI app (`uvicorn litecodeext.v3.gateway:create_app --factory`)."""
    from fastapi import FastAPI
    app = FastAPI(title="LiteCode v3 Gateway", version="0.1")
    app.include_router(router)
    # eager scan skills so endpoints are populated
    try:
        skill_registry.scan()
    except Exception:
        pass
    return app
