"""model_router.py — 多模型管理."""
from fastapi import APIRouter, Request

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()


@router.get("/api/models")
async def api_model_list(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"active": S.model, "models": []}
    d = await S.aproxy("/v1/model/list")
    return d or {"active": S.model, "models": []}


@router.post("/api/models/switch")
async def api_model_switch(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    body = await request.json()
    d = await S.aproxy("/v1/model/switch", "POST", json=body)
    if d and d.get("ok"):
        # [v1.0] 同步所有模型相关字段, 不仅仅是名称.
        # 旧版只更新 MODEL 导致 backend_url/api_key 不同步 → 401 错误
        S.model = d.get("model", S.model)
        S.ctx_window = d.get("context_window", S.ctx_window)
        # SERVER_URL 不变 (它是 litecode_server 本身的地址, 不是 backend)
    return d or {"ok": False, "error": "server unavailable"}


@router.post("/api/models/test")
async def api_model_test(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    body = await request.json()
    # vLLM 等服务冷启动可能很慢, 测试超时放宽到 30 秒
    d = await S.aproxy("/v1/model/test", "POST", json=body, timeout=30)
    return d or {"ok": False, "error": "server unavailable"}


@router.post("/api/models/add")
async def api_model_add(request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    body = await request.json()
    d = await S.aproxy("/v1/model/add", "POST", json=body)
    return d or {"ok": False, "error": "server unavailable"}


@router.delete("/api/models/{model_id}")
async def api_model_delete(model_id: str, request: Request):
    require_auth(request)
    if S.aproxy is None:
        return {"ok": False, "error": "server unavailable"}
    d = await S.aproxy(f"/v1/model/{model_id}", "DELETE")
    return d or {"ok": False, "error": "server unavailable"}
