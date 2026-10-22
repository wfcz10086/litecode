"""gateway/subagents.py — 后台子代理面板的 server 侧接口 (/v1/sessions/{sid}/subagents*)。

子代理登记表 (lib/agent/subagent_registry) 活在 litecode_server 进程里, web_ui 进程看不到,
所以 Web 面板经 routers/subagents_router.py 鉴权 + 校验会话归属后代理到这里。
按路径里的 sid 隔离: 一律走 registry.records(sid) / get(sid, id), 别的会话的 id 当作不存在 (404)。
stop / send 直接复用 agent_control_tool (长度上限、已结束/已请求停止等判断与主代理完全一致)。
events (?after=seq 增量) / diff / merge / discard 见文件末尾 (过程记录 + 分支操作)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from lib.config import log
from lib.agent import subagent_registry as reg
from lib.agent import subagent_events as _sev
from lib.agent import subagent_branch as _branch

from ._deps import require_auth

router = APIRouter()
UI_STOP_REASON = "用户在界面子代理面板点了停止"


def _rec(sid: str, rid: str):
    r = reg.get(sid, rid)
    if r is None:
        raise HTTPException(status_code=404, detail="subagent not found")
    return r


@router.get("/v1/sessions/{sid}/subagents")
async def list_subagents(sid: str, request: Request):
    require_auth(request)
    items = [reg.public_view(r) for r in reg.records(sid)]
    running = sum(1 for i in items if i["status"] == "running")
    return {"items": items, "running": running, "max_background": reg.max_background(),
            "send_max": reg.SEND_MAX}


@router.get("/v1/sessions/{sid}/subagents/{rid}")
async def get_subagent(sid: str, rid: str, request: Request):
    require_auth(request)
    return reg.public_view(_rec(sid, rid), with_result=True)


async def _control(sid: str, rid: str, args: dict) -> dict:
    out = await reg.agent_control_tool(sid, {**args, "id": rid})
    ok = not out.startswith("ERROR")
    return {"ok": ok, "message" if ok else "error": out,
            "item": reg.public_view(_rec(sid, rid))}


@router.post("/v1/sessions/{sid}/subagents/{rid}/stop")
async def stop_subagent(sid: str, rid: str, request: Request):
    require_auth(request)
    r = _rec(sid, rid)
    if r.status == "running" and not r.stop_requested:
        r.stop_reason = UI_STOP_REASON
    log.info(f"[subagent-panel] sid={sid[:12]} 用户请求停止 {rid}")
    return await _control(sid, rid, {"action": "stop"})


@router.post("/v1/sessions/{sid}/subagents/{rid}/send")
async def send_subagent(sid: str, rid: str, request: Request):
    require_auth(request)
    _rec(sid, rid)
    try:
        body = await request.json()
    except Exception:
        body = {}
    msg = str((body or {}).get("message") or "")
    log.info(f"[subagent-panel] sid={sid[:12]} 用户给 {rid} 发补充指令 ({len(msg)} 字)")
    return await _control(sid, rid, {"action": "send", "message": msg})


# ── [subagent-view 2026-10-22] 过程记录 + 分支操作 (lib/agent/subagent_events / subagent_branch) ──

@router.get("/v1/sessions/{sid}/subagents/{rid}/events")
async def subagent_events(sid: str, rid: str, request: Request, after: int = 0, limit: int = 300):
    """增量拉取: 返回 seq > after 的事件 (最多 limit 条); 带当前状态, 前端据此决定是否继续轮询。"""
    require_auth(request)
    r = _rec(sid, rid)
    d = _sev.since(r, max(0, int(after)), max(1, min(int(limit), _sev.EVENT_CAP)))
    d.update(status=r.status, done=r.status != "running", item=reg.public_view(r))
    return d


@router.get("/v1/sessions/{sid}/subagents/{rid}/diff")
async def subagent_diff(sid: str, rid: str, request: Request):
    require_auth(request)
    return await _branch.diff(_rec(sid, rid))


@router.post("/v1/sessions/{sid}/subagents/{rid}/merge")
async def subagent_merge(sid: str, rid: str, request: Request):
    require_auth(request)
    r = _rec(sid, rid)
    log.info(f"[subagent-panel] sid={sid[:12]} 用户合并 {rid} 的工作树 {r.worktree_id or '-'}")
    out = await _branch.merge(r)
    out["item"] = reg.public_view(r)
    return out


@router.post("/v1/sessions/{sid}/subagents/{rid}/discard")
async def subagent_discard(sid: str, rid: str, request: Request):
    require_auth(request)
    r = _rec(sid, rid)
    log.info(f"[subagent-panel] sid={sid[:12]} 用户丢弃 {rid} 的工作树 {r.worktree_id or '-'}")
    out = await _branch.discard(r)
    out["item"] = reg.public_view(r)
    return out
