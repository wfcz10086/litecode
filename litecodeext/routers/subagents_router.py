"""subagents_router.py — Web 后台子代理面板接口 (前端 web_assets/subagent_panel.js)。

  GET  /api/sessions/{sid}/subagents              本会话子代理列表 (+ 运行中数量)
  GET  /api/sessions/{sid}/subagents/{id}         单个子代理详情 + 结果 (只读, 不标"已取走")
  POST /api/sessions/{sid}/subagents/{id}/stop    叫停运行中的子代理
  POST /api/sessions/{sid}/subagents/{id}/send    给运行中的子代理发补充指令 {"message": "..."}
  GET  /api/sessions/{sid}/subagents/{id}/events?after=<seq>   过程记录增量拉取
  GET  /api/sessions/{sid}/subagents/{id}/diff    子代理工作树改动 (git diff, 限 200KB)
  POST /api/sessions/{sid}/subagents/{id}/merge   合并到发起时的基分支 (冲突即失败, 不留半合并)
  POST /api/sessions/{sid}/subagents/{id}/discard 删工作树与分支

子代理登记表在 litecode_server 进程, 本 router 只做 登录鉴权 (require_auth) + 会话归属校验
(require_owner 语义与 sessions_router 一致; 通过结果按 (用户, sid) 缓存 60s, 会话文件 mtime 变即失效),
然后经 S.aproxy 转发到 gateway/subagents.py。
会话隔离在 server 侧按路径 sid 做: 别的会话的子代理 id 一律 404。
"""
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request

from routers.auth_router import require_auth
from routers.state import S
from lib.owner import require_owner_cached

router = APIRouter()
ORDER = 105  # 注册顺序 (routers.discover 按此升序 include, 路径重叠时先注册者生效)

SEND_MAX = 2000   # 与 subagent_registry.SEND_MAX 一致; 真正的校验在 server 侧 agent_control, 这里只挡超大请求体


def _wui():
    """Lazy import web_ui 主模块, 避免循环 import (同 sessions_router)。"""
    import web_ui  # type: ignore
    return web_ui


def _mt(p) -> tuple:
    try:
        st = Path(p).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, -1)   # 不存在


def _stamp(sid: str) -> tuple:
    """会话存储指纹: web 缓存文件 + server 会话文件 + 墓碑文件的 mtime/size。
    改写/删除/转移 owner 都会改动其中之一 → 归属缓存失效。只 stat, 不读内容。"""
    w = _wui()
    paths = getattr(w, "CFG", {}).get("paths", {}) if hasattr(w, "CFG") else {}
    ws = Path(paths.get("workspace_base", "/tmp/litecode_workspace"))
    srv = Path(paths.get("sessions_dir", str(ws / "sessions"))) / f"{sid}.json"
    return (_mt(w._sf(sid)), _mt(srv), _mt(getattr(w, "_TOMBSTONE_FILE", "")))


def _guard(sid: str, request: Request) -> None:
    require_auth(request)
    # [owner-cache 2026-10-18] 面板运行中每 3s 轮询, 不再每次 load_session 全量读盘:
    # 通过结果按 (用户, sid) 缓存 60s, 会话文件/墓碑 mtime 变化即失效; 越权从不缓存。
    require_owner_cached(sid, request, _wui().load_session, _stamp)


def _p(sid: str, rid: str = "", tail: str = "") -> str:
    base = f"/v1/sessions/{quote(sid, safe='')}/subagents"
    return base + (f"/{quote(rid, safe='')}" if rid else "") + tail


async def _upstream(path: str, method: str = "GET", **kw) -> dict:
    d = await S.aproxy(path, method, **kw)
    if not isinstance(d, dict):
        raise HTTPException(502, "upstream: bad response")
    if "error" in d and "ok" not in d:   # aproxy 的失败形态: {"error", "status"?}
        st = d.get("status")
        raise HTTPException(st if isinstance(st, int) and 400 <= st < 600 else 502, d["error"])
    return d


@router.get("/api/sessions/{sid}/subagents")
async def api_subagents(sid: str, request: Request):
    _guard(sid, request)
    return await _upstream(_p(sid))


@router.get("/api/sessions/{sid}/subagents/{rid}")
async def api_subagent_detail(sid: str, rid: str, request: Request):
    _guard(sid, request)
    return await _upstream(_p(sid, rid))


@router.post("/api/sessions/{sid}/subagents/{rid}/stop")
async def api_subagent_stop(sid: str, rid: str, request: Request):
    _guard(sid, request)
    return await _upstream(_p(sid, rid, "/stop"), "POST")


@router.post("/api/sessions/{sid}/subagents/{rid}/send")
async def api_subagent_send(sid: str, rid: str, request: Request):
    _guard(sid, request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    msg = str((body or {}).get("message") or "") if isinstance(body, dict) else ""
    if len(msg) > SEND_MAX * 4:
        raise HTTPException(413, f"补充指令过长 ({len(msg)} 字), 上限 {SEND_MAX} 字")
    return await _upstream(_p(sid, rid, "/send"), "POST", json={"message": msg})


# ── [subagent-view 2026-10-22] 过程记录 (增量) + 分支操作 (前端 web_assets/subagent_view.js) ──

@router.get("/api/sessions/{sid}/subagents/{rid}/events")
async def api_subagent_events(sid: str, rid: str, request: Request, after: int = 0, limit: int = 300):
    _guard(sid, request)
    return await _upstream(_p(sid, rid, "/events"), params={"after": max(0, int(after)),
                                                            "limit": max(1, min(int(limit), 300))})


@router.get("/api/sessions/{sid}/subagents/{rid}/diff")
async def api_subagent_diff(sid: str, rid: str, request: Request):
    _guard(sid, request)
    return await _upstream(_p(sid, rid, "/diff"), timeout=60)


@router.post("/api/sessions/{sid}/subagents/{rid}/merge")
async def api_subagent_merge(sid: str, rid: str, request: Request):
    _guard(sid, request)
    return await _upstream(_p(sid, rid, "/merge"), "POST", timeout=180)


@router.post("/api/sessions/{sid}/subagents/{rid}/discard")
async def api_subagent_discard(sid: str, rid: str, request: Request):
    _guard(sid, request)
    return await _upstream(_p(sid, rid, "/discard"), "POST", timeout=120)
