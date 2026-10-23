"""providers_router.py — 「🔌 能力接入」接口 (数据与协议适配见 lib/providers.py / lib/provider_probe.py)。

GET    /api/providers                 列表 (api_key 打码) + 类型/协议说明 + 内置只读卡片
GET    /api/providers/presets         新增时的预设模板 (lib/provider_presets.py)
GET    /api/providers/{id}            单条完整明文 (详情页 / 对话引用展开)
POST   /api/providers                 新增
PUT    /api/providers/{id}            修改 (打码占位 ••••xxxx 视为不改)
DELETE /api/providers/{id}            删除
POST   /api/providers/{id}/default    设为同类默认
POST   /api/providers/{id}/test       按协议发最小请求, 返回 {ok, ms, detail|error}
POST   /api/providers/test            测试未保存的草稿 (「测试并保存」先跑这个; 带 id 时打码字段用已存值)
POST   /api/providers/{id}/adopt      自动发现 / 内置的只读卡片 → 复制成一张可编辑的普通卡片
列表里 builtins = 内置 + 自动发现 (lib/provider_discover.py: SearXNG / 记住的 ComfyUI / 内置配音), 都只读。
新卡片只需 name + usage (明文), 其余字段由 lib/provider_infer.py 从明文识别; 测试失败带 hint (人话原因)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from lib import provider_discover, provider_presets, provider_probe
from lib import providers as P
from routers._body import json_object
from routers.auth_router import require_auth

router = APIRouter()
ORDER = 310


def _readonly_cards() -> list[dict]:
    return [dict(P.normalize(b), readonly=True) for b in P.BUILTINS] + \
        [dict(P.normalize(x), readonly=True, auto=True) for x in P._discovered()]


def _get_or_404(pid: str) -> dict:
    p = next((x for x in P.load_all() if x["id"] == pid), None) or \
        next((x for x in _readonly_cards() if x["id"] == pid), None)
    if p is None:
        raise HTTPException(404, f"接入 {pid} 不存在")
    return p


def _readonly(pid: str) -> None:
    if any(b["id"] == pid for b in P.BUILTINS) or pid.startswith("auto_"):
        raise HTTPException(400, "内置 / 自动发现的卡片只读, 可以先「转为可编辑卡片」")


@router.get("/api/providers")
async def providers_list(request: Request):
    require_auth(request)
    items = [P.masked(p) for p in P.load_all()]
    provider_discover.invalidate()
    builtins = _readonly_cards()
    return {"ok": True, "providers": items, "builtins": builtins,
            "kinds": P.KINDS, "protocols": P.PROTOCOLS}


@router.get("/api/providers/presets")
async def providers_presets(request: Request):
    require_auth(request)
    return {"ok": True, "presets": provider_presets.PRESETS}


@router.post("/api/providers/test")
async def providers_test_draft(request: Request):
    require_auth(request)
    body = await json_object(request)
    pid = str(body.get("id") or "")
    base = next((x for x in P.load_all() if x["id"] == pid), None) if pid else None
    data = {}
    for k, v in body.items():
        if k == "api_key" and base is not None:
            v = P._merge_secret(v, base.get("api_key", ""))
        elif k == "extra" and isinstance(v, dict) and base is not None:
            v = P._unmask_extra(v, base.get("extra") or {})
        data[k] = v
    draft = P.prepare_draft(data, base)
    r = await provider_probe.test(draft)
    r["recognized"] = {k: draft.get(k) for k in ("kind", "protocol") if draft.get(k)}
    return r


@router.get("/api/providers/{pid}")
async def providers_get(pid: str, request: Request):
    require_auth(request)
    p = _get_or_404(pid)
    return {"ok": True, "provider": p, "card": P.card_text(p)}


@router.post("/api/providers")
async def providers_create(request: Request):
    require_auth(request)
    body = await json_object(request)
    try:
        p = P.create(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "provider": P.masked(p)}


@router.put("/api/providers/{pid}")
async def providers_update(pid: str, request: Request):
    require_auth(request)
    _readonly(pid)
    body = await json_object(request)
    try:
        p = P.update(pid, body)
    except KeyError:
        raise HTTPException(404, f"接入 {pid} 不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "provider": P.masked(p)}


@router.delete("/api/providers/{pid}")
async def providers_delete(pid: str, request: Request):
    require_auth(request)
    _readonly(pid)
    if not P.delete(pid):
        raise HTTPException(404, f"接入 {pid} 不存在")
    return {"ok": True}


@router.post("/api/providers/{pid}/default")
async def providers_default(pid: str, request: Request):
    require_auth(request)
    _readonly(pid)
    try:
        p = P.set_default(pid)
    except KeyError:
        raise HTTPException(404, f"接入 {pid} 不存在")
    return {"ok": True, "provider": P.masked(p)}


@router.post("/api/providers/{pid}/test")
async def providers_test(pid: str, request: Request):
    require_auth(request)
    r = await provider_probe.test(_get_or_404(pid))
    P.record_test(pid, r)
    return r


@router.post("/api/providers/{pid}/adopt")
async def providers_adopt(pid: str, request: Request):
    """只读卡片 (内置 / 自动发现) → 复制成可编辑卡片; 重名时加后缀。"""
    require_auth(request)
    src = next((x for x in _readonly_cards() if x["id"] == pid), None)
    if src is None:
        raise HTTPException(404, f"接入 {pid} 不存在或不是只读卡片")
    names = {x["name"].lower() for x in P.load_all()}
    name = src["name"]
    for suf in ("", " (可编辑)", " (可编辑 2)", " (可编辑 3)"):
        if (name + suf).lower() not in names:
            name += suf
            break
    data = {k: v for k, v in src.items() if k not in ("id", "readonly", "auto", "default", "inferred")}
    data["name"] = name
    try:
        p = P.create(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    provider_discover.invalidate()
    return {"ok": True, "provider": P.masked(p)}
