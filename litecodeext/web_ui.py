#!/usr/bin/env python3
"""web_ui.py v5 — LiteCode Web Interface (noVNC + upload + interrupt + semantic map)"""
import json, os, re, sys, time, uuid, hmac, hashlib, secrets, asyncio, random
from datetime import datetime
from pathlib import Path
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "core"))  # core 模块扁平导入 (timer_manager 等)
def _cfg():
    p = BASE/"config.json"
    return json.loads(p.read_text()) if p.exists() else {}

CFG = _cfg()
_srv = CFG.get("server",{})
SERVER_URL  = f"http://127.0.0.1:{_srv.get('port',18789)}"
TOKEN       = _srv.get("token","CHANGE_ME_TOKEN")
MODEL       = CFG.get("model",{}).get("id","openclaw")
CTX_WIN     = int(CFG.get("model",{}).get("context_window",65000))
WEB_PORT    = int(os.environ.get("WEB_PORT", CFG.get("web_ui",{}).get("port",18790)))

# [sys-inject-filter] LiteCode 自注入的 role=user 伪消息统一前缀家族是 "[SYSTEM...]"
# (如 [SYSTEM-TEST] / [SYSTEM-READONLY] / [SYSTEM-PLAN-GATE]) — 用正则覆盖整个家族,
# 而不是逐个硬编码前缀元组, 避免每次新加一个前缀就要记得回来加白名单 (老坑: [SYSTEM-READONLY]
# 曾经就是这么漏掉的). 只匹配消息开头 (lstrip 后), 正文中间出现 "[SYSTEM" 不受影响.
_SYS_INJECT_RE = re.compile(r'^\[SYSTEM[-\]]')

def _is_sys_inject_text(text) -> bool:
    return isinstance(text, str) and bool(_SYS_INJECT_RE.match(text.lstrip()))

_PRICE_TABLE = {
    "gpt-4o":           {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":      {"input": 0.15,  "output": 0.60},
    "gpt-5":            {"input": 5.00,  "output": 15.00},
    "claude-opus-4":    {"input": 15.00, "output": 75.00},
    "claude-sonnet-4":  {"input": 3.00,  "output": 15.00},
    "claude-haiku-4":   {"input": 0.80,  "output": 4.00},
    "Qwen3.5-35B":      {"input": 0.28,  "output": 0.56},
    "Qwen3.5-122B":     {"input": 0.56,  "output": 1.12},
    "qwen3.5:35b":      {"input": 0.28,  "output": 0.56},
    "qwen3.6":          {"input": 0.56,  "output": 1.12},
    "glm-5":            {"input": 0.50,  "output": 1.50},
    "_default":         {"input": 0.50,  "output": 1.50},
}

def _calc_cost(prompt_tok: int, completion_tok: int, model_id: str) -> float:
    p = _PRICE_TABLE.get(model_id, _PRICE_TABLE["_default"])
    return (prompt_tok / 1_000_000) * p["input"] + (completion_tok / 1_000_000) * p["output"]

SESSIONS_DIR = Path.home()/".litecode"/"web_sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
HEADERS = {"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"}

# [v1.0] 墓碑文件: 用户主动删除过的 sid, 防止 list_sessions 从 server 侧残留自动复活
_TOMBSTONE_FILE = Path.home() / ".litecode" / "deleted_sids.json"
_TOMBSTONE_FILE.parent.mkdir(parents=True, exist_ok=True)

def _load_tombstones() -> set:
    try:
        if _TOMBSTONE_FILE.exists():
            data = json.loads(_TOMBSTONE_FILE.read_text())
            if isinstance(data, list):
                return set(data)
            if isinstance(data, dict):
                return set(data.get("sids", []))
    except Exception:
        pass
    return set()

def _add_tombstone(sid: str):
    """把 sid 写进墓碑文件 (去重)"""
    tbs = _load_tombstones()
    tbs.add(sid)
    # 限制大小, 最多保留最近 5000 条 (够日常用很久)
    if len(tbs) > 5000:
        tbs = set(list(tbs)[-5000:])
    tmp = _TOMBSTONE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(tbs), ensure_ascii=False))
    tmp.replace(_TOMBSTONE_FILE)

# ── Auth ──────────────────────────────────────────────────────
# [router-2026-05] auth 已拆到 routers/auth_router.py
# 这里 re-export 给老代码 (_require_auth 在 web_ui.py 里被几十个 endpoint 用)
from routers.auth_router import (
    router as _auth_router,
    init as _init_auth_router,
    require_auth as _require_auth,
)
_init_auth_router(CFG)

def _sf(sid): return SESSIONS_DIR/f"{sid}.json"
def list_sessions():
    """列出所有会话：web_sessions + server sessions（CLI等）"""
    out=[]
    seen_ids=set()
    # [v1.0] 先加载墓碑: 用户主动删过的 sid 全局跳过, 防止复活
    _tombs = _load_tombstones()
    # 1) Web sessions (本地)
    for f in sorted(SESSIONS_DIR.glob("*.json"),key=lambda x:x.stat().st_mtime,reverse=True):
        try:
            d=json.loads(f.read_text())
            sid=d["id"]
            if sid in _tombs:
                # 墓碑里有 → 顺手把残留的本地文件也删掉
                try: f.unlink()
                except Exception: pass
                continue
            seen_ids.add(sid)
            out.append({"id":sid,"name":d.get("name",sid),"created":d.get("created",0),"last_used":d.get("last_used",0),"count":len(d.get("messages",[]))})
        except: pass
    # 2) Server sessions (CLI/API直连) — 导入未见过的
    _paths=CFG.get("paths",{})
    _ws=Path(_paths.get("workspace_base","/tmp/litecode_workspace"))
    _srv_sess=Path(_paths.get("sessions_dir",str(_ws/"sessions")))
    if _srv_sess.exists():
        for f in sorted(_srv_sess.glob("*.json"),key=lambda x:x.stat().st_mtime,reverse=True):
            try:
                sid=f.stem
                if sid in seen_ids: continue
                if sid in _tombs:
                    # [v1.0] 墓碑命中: 这是被显式删除过的 server 残留, 顺手清理, 不再 re-import
                    try: f.unlink()
                    except Exception: pass
                    try:
                        import shutil
                        sub=_srv_sess/sid
                        if sub.exists() and sub.is_dir():
                            shutil.rmtree(sub, ignore_errors=True)
                    except Exception: pass
                    continue
                d=json.loads(f.read_text())
                msgs=d.get("msgs",[])
                # 只导入有内容的 session
                user_msgs=[m for m in msgs if m.get("role")=="user" and m.get("content")]
                if not user_msgs: continue
                ts=d.get("ts",f.stat().st_mtime)
                first_q=user_msgs[0].get("content","")[:40] if user_msgs else sid
                # 自动导入到 web_sessions
                web_d=_convert_server_session(sid, d)
                save_session(web_d)
                seen_ids.add(sid)
                out.append({"id":sid,"name":web_d["name"],"created":web_d.get("created",ts),"last_used":ts,"count":len(web_d.get("messages",[]))})
            except: pass
    out.sort(key=lambda x:x.get("last_used",0),reverse=True)
    return out

def _convert_server_session(sid:str, server_data:dict) -> dict:
    """将 litecode_server 格式转换为 web_session 格式（保留 tool 信息）
    [v1.0] 关键修复:
    1. 旧版丢弃所有 content 为空的 assistant 消息 (模型纯工具调用) → 整个工具链看不见
    2. 旧版丢弃所有 role=tool 消息 → 工具结果也看不见
    现在: assistant 只要有 content 或 tool_calls 就保留; 紧跟的 tool 消息合并到该 assistant
    """
    msgs=server_data.get("msgs",[])
    ts=server_data.get("ts",time.time())
    source="cli" if sid.startswith("cli-") else "api"
    web_msgs=[]
    # tool_call_id -> (tool_name, tool_args_preview) 映射, 方便 tool 结果找回所属工具
    _tc_map: dict = {}
    # [sys-inject-filter 2026-07] LiteCode 自注入的 role=user 伪消息 (前缀家族 "[SYSTEM...]") —
    # 这些是内部给 LLM 的强制指令 (强推工具/写测试/推理约束/plan-gate), 不该在 UI 显示为"用户"消息.
    # 命中 → 跳过, 不入 web_msgs. 保留在 server session 里给 LLM 用即可.
    def _is_sys_injected(_c) -> bool:
        _txt = _c if isinstance(_c, str) else ""
        if isinstance(_c, list):
            for _p in _c:
                if isinstance(_p, dict) and _p.get("type") == "text":
                    _txt = _p.get("text", "") or ""
                    break
        return _is_sys_inject_text(_txt)
    for i, m in enumerate(msgs):
        role=m.get("role","")
        content=(m.get("content") or "")
        _msg_id = hashlib.sha1(f"{role}{str(content)[:80]}{i}".encode()).hexdigest()[:12]
        if role == "user" and content:
            if _is_sys_injected(content):
                continue  # 内部注入的强制指令, 不在 UI 露出
            web_msgs.append({"id":_msg_id,"role":"user","content":content,"ts":ts,"source":source})
        elif role == "assistant":
            tc_list = m.get("tool_calls") or []
            # 无 content 且无 tool_calls → 真的空, 跳过
            if not content and not tc_list:
                continue
            tools_desc = []
            for t in tc_list:
                fn = t.get("function", {}) or {}
                name = fn.get("name", "?")
                args_raw = fn.get("arguments", "") or ""
                # arguments 可能是 str, 也可能是 dict
                if isinstance(args_raw, dict):
                    try:
                        args_str = json.dumps(args_raw, ensure_ascii=False)
                    except Exception:
                        args_str = str(args_raw)
                else:
                    args_str = str(args_raw)
                args_preview = args_str[:120]
                tools_desc.append(f"{name}: {args_preview}")
                _tc_map[t.get("id","")] = (name, args_preview)
            web_msgs.append({
                "id": _msg_id,
                "role": "assistant",
                "content": content,
                "ts": ts,
                "tools": tools_desc,
                "source": source,
                # [FIX 2026-09-04] server 用 reasoning_content, 前端/缓存用 reasoning —
                # 旧 converter 不映射这一步 → 重导时把思考内容丢了 (刷新后推理步骤消失).
                "reasoning": m.get("reasoning_content") or m.get("reasoning") or "",
                "_tool_results": [],  # 后续 tool 消息会填进来
            })
        elif role == "tool" and web_msgs and web_msgs[-1].get("role") == "assistant":
            # 把工具结果合并到最近的 assistant 消息里 (预览用, 前端可选择渲染)
            tc_id = m.get("tool_call_id", "")
            tool_name, _args = _tc_map.get(tc_id, ("?", ""))
            # [2026-06-22] 原 300 字符截断把 execute_shell 输出里的图片路径切掉
            # → 前端 _scanAndPreview 扫不到路径 → 刷新后图没了.
            # 实测 server 端 tool 消息最长 ~5KB, 这里 20KB 留足余量, 上限防失控.
            result = content[:20000] if content else ""
            web_msgs[-1].setdefault("_tool_results", []).append({
                "name": tool_name, "result": result, "id": tc_id,
            })
        # 其他 (system / orphan tool) 跳过
    # 命名
    first_user=[m for m in web_msgs if m["role"]=="user"]
    prefix="🖥️ " if sid.startswith("cli-") else "🔌 "
    first_q = first_user[0]["content"] if first_user else sid
    name = prefix + (first_q[:35] + "…" if len(first_q) > 35 else first_q)
    return {"id":sid,"name":name,"created":ts,"last_used":ts,"messages":web_msgs}

def _merge_cached_reasoning(cache_path, web_d):
    """[FIX 2026-09-04] 重导 web 会话时, 把老缓存里已有的 reasoning 按内容前缀补回 web_d。

    症状: 刷新后前几条 AI 的推理步骤消失。根因是重导用无 reasoning 的重建覆盖了缓存,
    而 server session 常不落 terminal 答案的 reasoning (只落工具调用那条) —— 那段思考
    本来只在 web 缓存里 (_save_assistant_msg 从 SSE 流存的). 按 content[:80] 匹配 (比
    content-hash id 稳, 因为缓存和 converter 两条路径的 id 方案可能不同)。
    """
    try:
        if not cache_path.exists():
            return
        old = json.loads(cache_path.read_text())
    except Exception:
        return
    old_rz = {}
    for m in old.get("messages", []):
        if m.get("role") == "assistant" and (m.get("reasoning") or "").strip():
            key = (m.get("content") or "")[:80]
            if key:  # 空 content (纯工具调用) 不作 key, 那类 reasoning 由 converter 直接带
                old_rz[key] = m["reasoning"]
    if not old_rz:
        return
    for m in web_d.get("messages", []):
        if m.get("role") == "assistant" and not (m.get("reasoning") or "").strip():
            r = old_rz.get((m.get("content") or "")[:80])
            if r:
                m["reasoning"] = r

def load_session(sid):
    # [v1.0] 墓碑命中直接返回空, 不触发 server session 自动导入
    if sid in _load_tombstones():
        return {"id":sid,"name":"(deleted)","created":time.time(),"last_used":time.time(),"messages":[]}
    _paths = CFG.get("paths", {})
    _ws = Path(_paths.get("workspace_base", "/tmp/litecode_workspace"))
    _srv = Path(_paths.get("sessions_dir", str(_ws / "sessions"))) / f"{sid}.json"
    f = _sf(sid)
    # [v1.0] server session 比 web cache 新 → 强制重新导入
    # 避免老 web_sessions 文件是旧版 converter 的产物, 永远显示不全。
    server_newer = (
        _srv.exists() and f.exists()
        and _srv.stat().st_mtime > f.stat().st_mtime + 1.0
    )
    if f.exists() and not server_newer:
        try:
            cached = json.loads(f.read_text())
            # 若缓存缺少 _tool_results 字段 (老 converter 产物), 也强制刷新
            if _srv.exists() and cached.get("messages"):
                has_tool_meta = any(
                    m.get("tools") or m.get("_tool_results")
                    for m in cached["messages"] if m.get("role") == "assistant"
                )
                if not has_tool_meta:
                    server_newer = True
                # [sys-inject-filter 2026-07] 老缓存里可能残留 "[SYSTEM...]" 伪 user 消息,
                # 新版 converter 会过滤掉 → 命中则强刷.
                _has_sys_inject = any(
                    m.get("role") == "user" and _is_sys_inject_text(m.get("content"))
                    for m in cached["messages"]
                )
                if _has_sys_inject:
                    server_newer = True
            if not server_newer:
                return cached
        except Exception:
            pass
    # 从 server session 导入 (新数据或强制刷新)
    if _srv.exists():
        try:
            d = json.loads(_srv.read_text())
            web_d = _convert_server_session(sid, d)
            _merge_cached_reasoning(f, web_d)   # 重导别抹掉老缓存里已有的思考内容
            save_session(web_d)
            return web_d
        except Exception:
            pass
    return {"id":sid,"name":"New Chat","created":time.time(),"last_used":time.time(),"messages":[]}
def save_session(d):
    # [P0-#6] fsync 原子落盘 (tmp→fsync→replace→fsync-dir)
    from lib.atomic_io import atomic_write_json
    atomic_write_json(_sf(d["id"]), d)
def create_session(name="New Chat", owner="anon"):
    sid=f"web-{uuid.uuid4().hex[:8]}"
    d={"id":sid,"name":name,"created":time.time(),"last_used":time.time(),
       "messages":[],"owner":owner}
    save_session(d); return d

async def _aproxy(path, method="GET", **kw):
    """真正的异步代理：httpx.AsyncClient，不阻塞事件循环"""
    try:
        t = kw.pop("timeout", 5)
        async with httpx.AsyncClient(timeout=t) as client:
            r = await client.request(method, f"{SERVER_URL}{path}", headers=HEADERS, **kw)
            if r.status_code < 400:
                return r.json()
            return {"error": f"HTTP {r.status_code}", "status": r.status_code}
    except httpx.TimeoutException:
        return {"error": "server busy (timeout)"}
    except httpx.ConnectError:
        return {"error": "server unavailable"}
    except Exception as e:
        return {"error": str(e)[:100]}

from contextlib import asynccontextmanager as _acm

def _scrub_agent_prefix(text: str) -> str:
    """剥离 LiteCode agent SOUL.md 里强制的 '💭 思路:/📋 计划:/✓ ...' 编排前缀.
    企微 auto-reply 场景只要用户可读的最终答案, 不要 agent 内部计划."""
    if not text:
        return text
    import re as _re
    # 去掉 <think>...</think> 兜底 (litecode_server 应该剥了, 但双保险)
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
    lines = text.splitlines()
    out = []
    skip_plan = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("💭 思路") or s.startswith("💭思路"):
            continue
        if s.startswith("📋 计划") or s.startswith("📋计划"):
            skip_plan = True
            continue
        if skip_plan:
            # 计划列表 (1. 2. 3.) 或 "开始执行 ↓" 都吞掉
            if _re.match(r"^\d+[\.、)]\s", s) or s in ("开始执行 ↓", "开始执行↓", "开始执行"):
                continue
            # 遇到非计划行 → 计划段结束
            skip_plan = False
        # 去掉行首 "✓ " 结果打勾 (SOUL 里用来标 "第 N 步完成")
        if s.startswith("✓ ") or s.startswith("✅ "):
            ln = ln.replace("✓ ", "", 1).replace("✅ ", "", 1)
        out.append(ln)
    # 折叠多余空行
    cleaned = "\n".join(out)
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or text.strip()


@_acm
async def _lifespan(application):
    # startup
    if _WX_AVAILABLE:
        mgr = _wx_manager()
        await mgr.startup()
    if _WC_AVAILABLE:
        try:
            _wcmgr = _wc_manager()
            await _wcmgr.startup()
            _wcmgr.on_message = make_wc_on_msg(_wc_manager, CFG)
        except Exception as _e:
            print(f"[lifespan] wecom startup failed: {_e}")
    if _TIMER_AVAILABLE:
        tmgr = _timer_manager()
        # [v1.3] 让 timer action_type='dag' 能直接拉一个 DAG, 不绕 HTTP/auth
        try:
            tmgr.set_dag_runner(_start_dag_internal)
        except Exception as _e:
            print(f"[lifespan] set_dag_runner failed: {_e}")
        # [2026-05-19] timer action_type='wechat_msg' 注入 wx sender
        # target 格式: "<bot_id>:<user_id>", 调 mgr.bots[bot_id]._bot.send(user_id, text)
        if _WX_AVAILABLE:
            try:
                async def _wx_send(bot_id: str, user_id: str, text: str):
                    mgr = _wx_manager()
                    bot = mgr.bots.get(bot_id)
                    if not bot:
                        raise RuntimeError(f"bot {bot_id} 不存在")
                    inner = getattr(bot, "_bot", None)
                    if inner is None:
                        raise RuntimeError(f"bot {bot_id} 未初始化")
                    await inner.send(user_id, text)
                tmgr.set_wechat_sender(_wx_send)
            except Exception as _e:
                print(f"[lifespan] set_wechat_sender failed: {_e}")
        if _WC_AVAILABLE and hasattr(tmgr, "set_wecom_sender"):
            try:
                tmgr.set_wecom_sender(make_wc_sender(_wc_manager))
            except Exception as _e:
                print(f"[lifespan] set_wecom_sender failed: {_e}")
        await tmgr.start()
    yield
    # [P0-#11] shutdown: flush in-memory DAG state, cancel bg tasks, 关 tmgr / wx bots.
    try:
        # DAG jobs: 取消未完成的 _task, 强制落盘一次
        _running = [(jid, j) for jid, j in list(_DAG_JOBS.items())
                    if j.get("status") == "running"]
        for jid, job in _running:
            t = job.get("_task")
            if t and not t.done():
                t.cancel()
            # 落盘时留 status=running + 最新 heartbeat, 下次启动 load 时按 5min 陈旧判
            try:
                _dag_job_save(jid)
            except Exception as _e:
                print(f"[lifespan/shutdown] save {jid} fail: {_e}")
        if _running:
            print(f"[lifespan/shutdown] cancel + flush {len(_running)} running DAG job(s)")
    except Exception as _e:
        print(f"[lifespan/shutdown] DAG flush fail: {_e}")

    if _TIMER_AVAILABLE:
        try:
            await _timer_manager().stop()
        except Exception as _e:
            print(f"[lifespan/shutdown] timer stop fail: {_e}")

    if _WX_AVAILABLE:
        try:
            await _wx_manager().shutdown_all()
        except Exception as _e:
            print(f"[lifespan/shutdown] wx shutdown fail: {_e}")

    if _WC_AVAILABLE:
        try:
            await _wc_manager().shutdown_all()
        except Exception as _e:
            print(f"[lifespan/shutdown] wc shutdown fail: {_e}")

app = FastAPI(title="LiteCode Web UI v4", lifespan=_lifespan)

# [P0-#3] CORS 白名单. 允许列表从 config.json:web_ui.cors_origins 读, 缺省仅本机.
# 单人自用 → 默认不放公网, 避免 CSRF/token 泄露. 想开公网需显式列出.
from fastapi.middleware.cors import CORSMiddleware as _CORSMiddleware
_cors_cfg = CFG.get("web_ui", {}).get("cors_origins")
if _cors_cfg is None:
    _cors_cfg = [
        "http://127.0.0.1", "http://localhost",
        f"http://127.0.0.1:{WEB_PORT}", f"http://localhost:{WEB_PORT}",
    ]
app.add_middleware(
    _CORSMiddleware,
    allow_origins=_cors_cfg,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=600,
)

# [router-2026-05] login / logout / auth.status 已搬到 routers/auth_router.py
app.include_router(_auth_router)

# [router-2026-05] 注入共享 state 给所有 router (aproxy / cfg / model / paths)
from routers.state import init as _init_state
_init_state(
    cfg=CFG,
    server_url=SERVER_URL,
    token=TOKEN,
    headers=HEADERS,
    base_dir=BASE,
    workspace=Path(CFG.get("paths", {}).get("workspace_base", "/tmp/litecode_workspace")),
    sessions_dir=SESSIONS_DIR,
    cfg_path=BASE / "config.json",
    model=MODEL,
    ctx_window=CTX_WIN,
    web_port=WEB_PORT,
)
# aproxy 函数会在 web_ui.py 内定义后注入 (见下方 _aproxy 定义之后的 state.S.aproxy = _aproxy)

# 拆出来的 router (每批拆完即 include, 老 endpoint 同步删):
# phase 1: auth + health + model + config + timer
# phase 2: memory + workspace + wechat + projects
from routers.health_router    import router as _health_router
from routers.model_router     import router as _model_router
from routers.config_router    import router as _config_router
from routers.timer_router     import router as _timer_router
from routers.memory_router    import router as _memory_router
from routers.workspace_router import router as _workspace_router
from routers.wechat_router    import router as _wechat_router
from routers.wecom_router     import router as _wecom_router
from routers.projects_router  import router as _projects_router
# phase 3: static + sessions + dag (chat 因 SSE 复杂保留在 web_ui.py)
from routers.static_router    import router as _static_router
from routers.sessions_router  import router as _sessions_router
from routers.dag_router       import router as _dag_router
# S1-N: 通用 artifact 仓库 (owner-scoped, 与老 /api/artifacts/{sid} 独立命名空间)
from routers.artifacts_v2_router import router as _artifacts_v2_router
# #44: Docker 管理 (ps/logs/stats/restart/stop/start/inspect)
from routers.docker_router      import router as _docker_router
app.include_router(_health_router)
app.include_router(_model_router)
app.include_router(_config_router)
app.include_router(_timer_router)
app.include_router(_memory_router)
app.include_router(_workspace_router)
app.include_router(_wechat_router)
app.include_router(_wecom_router)
app.include_router(_projects_router)
app.include_router(_sessions_router)
app.include_router(_dag_router)
app.include_router(_artifacts_v2_router)
app.include_router(_docker_router)
app.include_router(_static_router)

# 把 web_ui 里的 _aproxy 函数注入给 router 共享 (各 router 通过 S.aproxy 调上游 server)
from routers.state import S as _shared_state
_shared_state.aproxy = _aproxy
# static_router 用 (主页 HTML 注入 VNC/Web 端口) — 函数在 web_ui.py 下方定义, 启动时再注入

# [router-2026-05] /api/health 已搬到 routers/health_router.py

# [router-2026-05] sessions/tombstones/memory/files/export 全部 13 endpoint 已搬到 routers/sessions_router.py
# helper 函数 list_sessions/save_session/load_session/_sf/_load_tombstones/_add_tombstone 保留在本模块
# router 通过 lazy import web_ui 拿到这些 helper

# [router-2026-05] /api/artifacts /api/memory/{sid}/{compress,clear,l1} /api/bgprocs 已搬到 routers/{memory,health}_router.py



# ── 首条消息随机打招呼 ────────────────────────────────────────
_GREETINGS = [
    "你好！有什么我可以帮你的？",
    "嗨，你好！请问有什么需要帮忙的吗？",
    "你好，很高兴见到你！请说吧，我在听。",
    "你好！随时告诉我你需要什么。",
    "嗨！今天有什么想聊的？",
    "你好！我随时准备为你效劳。",
    "你好，欢迎！有什么问题直接问我。",
    "嗯，你好！今天有什么需要我帮忙处理的？",
]

def _random_greeting() -> str:
    return random.choice(_GREETINGS)

def _save_assistant_msg(sid:str, collected:dict):
    """后台保存 assistant 消息，保证客户端断连后依然能写入。"""
    if not (collected["text"] or collected["tools"] or collected["diffs"] or collected.get("reasoning")):
        return
    try:
        s2 = load_session(sid)
        # 去重：如果最后一条已经是这条 assistant 消息就不重复写
        msgs = s2.get("messages", [])
        if msgs and msgs[-1].get("role") == "assistant" and msgs[-1].get("content","") == collected["text"]:
            return
        s2["messages"].append({
            "role": "assistant",
            "content": collected["text"],
            "ts": time.time(),
            "tools": collected["tools"],
            "diffs": collected.get("diffs", []),
            # [v1.0] 持久化推理 (方案 B): 仅前端展示用,
            # web_ui.py 发请求时不读它, litecode_server 自己管历史, 不会回流到 LLM 上下文
            "reasoning": collected.get("reasoning", ""),
            "usage": collected.get("usage")
        })
        s2["last_used"] = time.time()
        save_session(s2)
    except Exception:
        pass

# [router-2026-05] /api/sessions/{sid}/sync 已搬到 routers/sessions_router.py

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_FILE_REF_RE = __import__("re").compile(r"\[file:\s*(/[^\]]+?)\]", __import__("re").I)

def _build_multimodal_content(message: str):
    """[v1.4] 解析消息里的 [file: /path/xxx.jpg] 引用, 若主模型多模态
    且文件是图片, 把图片作为 OpenAI content array 直接嵌入 (base64).

    [multi-upload 保护]
    - 最多 _MM_MAX_IMAGES 张 (默认 6, 对齐 wechat_bridge), 超出的走字面量路径
      让 agent 用 read_file / vision_ocr 自己挑
    - 累计 base64 超 _MM_MAX_PAYLOAD_MB 时停止追加, 剩下的也退化成字面量
    - 单图 > 2MB 会 resize 到 1280px + JPEG q82 (原逻辑)
    非图片文件不入 base64, 路径原样保留在文本里, agent 用工具访问。
    """
    cfg = _cfg()
    main_supports_vision = bool(cfg.get("model", {}).get("supports_vision", False))
    if not main_supports_vision:
        return message, []
    matches = _FILE_REF_RE.findall(message)
    if not matches:
        return message, []
    # [multi-upload] 从 config 读 cap, 默认对齐 wechat (6 张 / 12MB 载荷)
    _ui_cfg = cfg.get("web_ui", {}) or {}
    _MM_MAX_IMAGES   = int(_ui_cfg.get("multimodal_max_images", 6))
    _MM_MAX_PAYLOAD  = int(_ui_cfg.get("multimodal_max_payload_mb", 12)) * 1024 * 1024
    import base64, io
    parts = []
    imgs_ok = []
    imgs_skipped: list = []
    total_raw = 0
    for m in matches:
        p = Path(m.strip())
        if not p.exists() or p.suffix.lower() not in _IMG_EXTS:
            continue
        if len(imgs_ok) >= _MM_MAX_IMAGES:
            imgs_skipped.append(f"{p.name}(over-count)")
            continue
        raw = p.read_bytes()
        mime = {".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",
                ".gif":"image/gif",".webp":"image/webp",".bmp":"image/bmp"}.get(
                    p.suffix.lower(), "image/jpeg")
        # 大图压缩 (复用与 wechat_bridge 同样策略)
        if len(raw) > 2 * 1024 * 1024:
            try:
                from PIL import Image
                try: RESAMPLE = Image.Resampling.LANCZOS
                except AttributeError: RESAMPLE = getattr(Image, "LANCZOS", 1)
                img = Image.open(io.BytesIO(raw))
                if img.mode not in ("RGB","L"):
                    img = img.convert("RGB")
                w,h = img.size; scale = 1280 / max(w,h)
                if scale < 1.0:
                    img = img.resize((int(w*scale), int(h*scale)), RESAMPLE)
                out = io.BytesIO(); img.save(out, format="JPEG", quality=82, optimize=True)
                raw = out.getvalue(); mime = "image/jpeg"
            except Exception:
                pass
        if total_raw + len(raw) > _MM_MAX_PAYLOAD:
            imgs_skipped.append(f"{p.name}(over-payload {len(raw)//1024}KB)")
            continue
        total_raw += len(raw)
        b64 = base64.b64encode(raw).decode()
        parts.append({"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}})
        imgs_ok.append(str(p))
    if imgs_skipped:
        print(f"[multimodal] 跳过 {len(imgs_skipped)} 张: {imgs_skipped[:3]}"
              f" (max_images={_MM_MAX_IMAGES}, max_payload={_MM_MAX_PAYLOAD//1048576}MB)"
              f" 仍以 [file:] 字面量保留, agent 可用 vision_ocr 处理")
    if not parts:
        return message, []
    parts.append({"type":"text","text":message})
    return parts, imgs_ok


@app.post("/api/chat/{sid}")
async def api_chat(sid:str,request:Request):
    _require_auth(request)
    # [P0-#4] chat 节流: 30 次 / 60s / IP
    from lib.rate_limit import rate_check as _rate_check
    _rate_check(request, "chat", 30, 60)
    body=await request.json()
    message=body.get("message","").strip()
    if not message: raise HTTPException(400,"empty")
    session=load_session(sid)
    ts=time.time()
    session["messages"].append({"role":"user","content":message,"ts":ts})
    session["last_used"]=ts
    is_first = len(session["messages"])==1
    if is_first:
        session["name"]=message[:40]+("…" if len(message)>40 else "")
    save_session(session)
    # [v1.4] 如果消息里引用了本地图片 + 主模型支持视觉 → 组装多模态 content
    user_payload_content, mm_imgs = _build_multimodal_content(message)
    if mm_imgs:
        print(f"[api_chat:{sid}] 多模态直传 {len(mm_imgs)} 张图")
    collected={"text":"","reasoning":"","tools":[],"diffs":[],"usage":None}
    # [v1.9 P38-b] SSE 背压保护
    SSE_HIGH_VOLUME_WARN = 5000   # chunks 超阈值发警告 telemetry
    SSE_HARD_LIMIT       = 50000  # chunks 硬上限（防止 LLM 失控刷屏）
    # [V7.e] 前端 chip 参数: 模型 / 思考强度 / DeepSearch (老 UI 不传 → 走全局 MODEL 兼容)
    _req_model = (body.get("model") or MODEL)
    _req_effort = (body.get("thinking_effort") or "").strip().lower()
    _req_deep = bool(body.get("deep_search"))
    _req_strategist = bool(body.get("strategist_mode") or body.get("scenario_mode"))
    async def gen():
        nonlocal collected
        _stream_t0 = time.time()
        _chunk_count = 0
        _disconnected = False
        _aborted_reason = ""
        payload={"model":_req_model,"stream":True,
                 "messages":[{"role":"user","content":user_payload_content}],
                 "user":sid}
        if _req_effort == "off":
            # 显式关闭推理: 各后端格式不同, 一并注入
            payload["enable_thinking"] = False
            payload["thinking_budget"] = 0
        elif _req_effort:
            payload["reasoning_effort"] = _req_effort
        if _req_deep:
            payload["deep_search"] = True
        if _req_strategist:
            payload["strategist_mode"] = True
        # [P46 测试模式] message 含 __TRIGGER_EMPTY_RESPONSE__ → 走 server 空响应路径
        _server_url_with_test = f"{SERVER_URL}/v1/chat/completions"
        if "__TRIGGER_EMPTY_RESPONSE__" in message:
            _server_url_with_test += "?__test_empty=1"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
                async with client.stream("POST", _server_url_with_test,
                                         headers=HEADERS, json=payload) as resp:
                    if resp.status_code!=200:
                        yield f'data: {{"error":"HTTP {resp.status_code}"}}\n\n'; return
                    # [audit-p0 #10/#18] 用 pending task 包 aiter, 15s 无上游数据即发心跳注释帧;
                    # 顺便探断连. httpx aiter_lines() 本身对超长空闲不保底.
                    aiter = resp.aiter_lines()
                    _pending = None
                    _SSE_IDLE_PING = 15.0
                    while True:
                        if _pending is None:
                            _pending = asyncio.ensure_future(aiter.__anext__())
                        done, _pen = await asyncio.wait([_pending], timeout=_SSE_IDLE_PING)
                        if not done:
                            # 上游 15s 无字节: 发心跳; 顺带查前端是否断了
                            if await request.is_disconnected():
                                _disconnected = True
                                _aborted_reason = "client_disconnected"
                                _pending.cancel()
                                break
                            yield ": keep-alive\n\n"
                            continue
                        task = _pending
                        _pending = None
                        try:
                            raw = task.result()
                        except StopAsyncIteration:
                            break
                        if not raw: continue
                        if await request.is_disconnected():
                            _disconnected = True
                            _aborted_reason = "client_disconnected"
                            break
                        if _chunk_count >= SSE_HARD_LIMIT:
                            _aborted_reason = f"hard_limit_{SSE_HARD_LIMIT}"
                            yield f'data: {{"error":"SSE chunk limit {SSE_HARD_LIMIT} reached, aborted"}}\n\n'
                            break
                        line = raw
                        yield line+"\n\n"
                        _chunk_count += 1
                        if line.startswith("data: ") and line[6:] not in ("[DONE]",""):
                            try:
                                chunk=json.loads(line[6:])
                                delta=chunk["choices"][0]["delta"]
                                ct=delta.get("content","")
                                if ct: collected["text"]+=ct
                                # [v1.0] 收集推理流, 用于持久化展示 (方案 B: 不回流到 LLM 上下文)
                                rz=delta.get("reasoning","")
                                if rz: collected["reasoning"]+=rz
                                if "usage" in delta:
                                    collected["usage"]=delta["usage"]
                                for key in("task_exec","data_collect","task_analysis"):
                                    if key in delta and delta[key].get("status")=="executing":
                                        det=delta[key].get("detail","")
                                        if det: collected["tools"].append(det)
                                if "diff_view" in delta:
                                    dv=delta["diff_view"]
                                    if dv.get("diff"): collected["diffs"].append({"filepath":dv.get("filepath",""),"diff":dv["diff"]})
                            except: pass
        except Exception as e:
            yield f"data: {json.dumps({'error':str(e)})}\n\n"
        finally:
            # [v1.9 P38-b] SSE 流统计埋点
            try:
                from core.telemetry import emit as _emit
                _stream_elapsed_ms = int((time.time() - _stream_t0) * 1000)
                _event = "sse_stream_high_volume" if _chunk_count >= SSE_HIGH_VOLUME_WARN \
                         else ("sse_stream_aborted" if _aborted_reason else "sse_stream_done")
                _emit(event=_event,
                      fields={
                          "sid": sid[:32],
                          "chunk_count": _chunk_count,
                          "disconnected": _disconnected,
                          "aborted_reason": _aborted_reason,
                          "high_volume": _chunk_count >= SSE_HIGH_VOLUME_WARN,
                      },
                      jsonl="sse.jsonl",
                      sid=sid,
                      latency_ms=_stream_elapsed_ms)
            except Exception:
                pass
            # [BUG-FIX 2026-05-21] 原版 run_in_executor 不 await → cache 写 assistant 滞后 5+s
            # 用户在这 5 秒点重试就 "未在 session 定位" (cache 还没 assistant).
            # 改 await 同步等完才结束 SSE 流, 前端拿到 done 时 cache 已就绪.
            try:
                await asyncio.get_event_loop().run_in_executor(None, _save_assistant_msg, sid, dict(collected))
            except Exception:
                pass
    return StreamingResponse(gen(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ── 文件上传 ─────────────────────────────────────────────────
_WORKSPACE = Path(CFG.get("paths",{}).get("workspace_base","/tmp/litecode_workspace"))

# [multi-upload] 从 config.web_ui.max_upload_mb 读, 默认 20MB
_MAX_UPLOAD_SIZE = int(CFG.get("web_ui", {}).get("max_upload_mb", 20)) * 1024 * 1024

@app.post("/api/upload/{sid}")
async def api_upload(sid: str, request: Request, file: UploadFile = File(...)):
    _require_auth(request)
    # [P0-#4] upload 节流: 10 次 / 60s / IP (大附件重, 数值给紧)
    from lib.rate_limit import rate_check as _rate_check
    _rate_check(request, "upload", 10, 60)
    # [multi-upload] 流式读: 大文件分块落盘, 不再一次性吃进内存
    # 兼容老协议 — 依旧返回 {ok,path,name,size}
    dest = _WORKSPACE / "uploads"
    dest.mkdir(parents=True, exist_ok=True)
    # 防止同名覆盖: 冲突时加时间戳后缀
    safe_name = (file.filename or "unnamed").replace("/", "_")
    fp = dest / safe_name
    if fp.exists():
        stem = fp.stem; suf = fp.suffix
        fp = dest / f"{stem}-{int(time.time())}{suf}"
    # 分块写 + 边写边量 size; 超限立即删掉半成品并 413
    size = 0
    CHUNK = 1024 * 1024
    with open(fp, "wb") as out:
        while True:
            chunk = await file.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_UPLOAD_SIZE:
                out.close()
                try: fp.unlink()
                except Exception: pass
                raise HTTPException(
                    413,
                    f"文件过大 (>{size//1048576}MB)，限制 {_MAX_UPLOAD_SIZE//1048576}MB "
                    f"— 请改 config.web_ui.max_upload_mb 或先用 scp 放入 workspace/"
                )
            out.write(chunk)
    return {"ok": True, "path": str(fp), "name": fp.name, "size": size}

# [router-2026-05] workspace/files, workspace/download, media, preview, workspace/preview,
# workspace/preview_office 已搬到 routers/workspace_router.py

# ── 任务中断 ─────────────────────────────────────────────────
@app.post("/api/interrupt/{sid}")
async def api_interrupt(sid: str, request: Request):
    _require_auth(request)
    # 通知 litecode_server 中断
    d = await _aproxy(f"/v1/sessions/{sid}/interrupt", "POST")
    if d:
        return d
    # fallback: 写中断标记文件，agent 循环检查
    flag = _WORKSPACE / "sessions" / sid / ".interrupt"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(str(time.time()))
    return {"ok": True, "method": "flag_file"}

# [router-2026-05] /api/questions /api/map_status /api/map_force_update 已搬到 routers/health_router.py
# /api/stats 留在这 — 依赖 _calc_cost + _PRICE_TABLE
@app.get("/api/stats")
async def api_stats(request: Request):
    _require_auth(request)
    d = await _aproxy("/v1/stats")
    if isinstance(d, dict):
        _mid = MODEL or "_default"
        d["estimated_cost_usd"] = round(
            _calc_cost(d.get("total_prompt_tokens", 0), d.get("total_completion_tokens", 0), _mid), 4
        )
        d["price_model"] = _mid
        d["price_per_mtok"] = _PRICE_TABLE.get(_mid, _PRICE_TABLE["_default"])
    return d or {"error": "server unavailable"}

# [router-2026-05] /api/models* /api/config* 已搬到 routers/{model,config}_router.py

import pathlib as _pl
import asyncio as _asyncio

# ── WeChat Bridge ─────────────────────────────────────────────
try:
    from wechat_bridge import get_manager as _wx_manager
    _WX_AVAILABLE = True
except ImportError:
    _WX_AVAILABLE = False

# ── WeCom (企微智能机器人) Bridge ──────────────────────────────
try:
    from wecom_bridge import get_manager as _wc_manager
    from wecom_chat_bridge import make_wc_on_msg, make_wc_sender
    _WC_AVAILABLE = True
except ImportError:
    _WC_AVAILABLE = False

# ── Timer Manager ─────────────────────────────────────────────
# [router-2026-05] timer 模块及 7 个 endpoint 都搬到 routers/timer_router.py
# 这里只保留 _wx_manager / _timer_manager 给 lifespan 用 (set_dag_runner / startup)
try:
    from timer_manager import get_timer_manager as _timer_manager
    _TIMER_AVAILABLE = True
except ImportError:
    _TIMER_AVAILABLE = False

# [router-2026-05] WeChat 9 个 endpoint 已搬到 routers/wechat_router.py
# [router-2026-05] Projects 13 个 endpoint 已搬到 routers/projects_router.py

_HTML_FILE = _pl.Path(__file__).parent / "web_ui.html"
_ASSETS_DIR = _pl.Path(__file__).parent / "web_assets"
_HTML_CACHE: str = ""
_HTML_MTIME: float = 0.0

# [v1.0] 静态资源 mtime 缓存, 开发时热更新 + 生产时强缓存
_ASSET_MIME = {
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".woff2":"font/woff2",
    ".woff": "font/woff",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
}


# ── DAG 编排 API (P5-d) — 状态 + 启动器都在 dag_state.py ────────
# [2026-07-27] 从 web_ui.py 抽出 231 行, dag_router 里 _wui()._DAG_JOBS /
# _wui()._start_dag_internal 通过下面的 re-export 继续可用 (向后兼容).
from dag_state import (  # noqa: F401  re-export 给 routers 用
    _DAG_JOBS, _DAG_JOBS_LOCK, _DAG_JOBS_DIR,
    _dag_job_save, _dag_jobs_load, _start_dag_internal,
)



# [router-2026-05] POST /api/dags/{name}/run 也搬到 routers/dag_router.py


# [router-2026-05] /assets/* 已搬到 routers/static_router.py

def _get_html() -> str:
    """每次请求检查文件修改时间，热重载 web_ui.html（开发友好）"""
    global _HTML_CACHE, _HTML_MTIME
    try:
        # [cache-bust 2026-09-03] 缓存 key 同时看 html 和 assets 的最新 mtime,
        # 否则只改 app.js 不改 html 时, html 缓存不刷新 → 版本戳也不更新 → 白加。
        _asset_mt = 0.0
        try:
            for _f in _ASSETS_DIR.glob("*.js"):
                _asset_mt = max(_asset_mt, _f.stat().st_mtime)
            for _f in _ASSETS_DIR.glob("*.css"):
                _asset_mt = max(_asset_mt, _f.stat().st_mtime)
        except Exception:
            pass
        mtime = max(_HTML_FILE.stat().st_mtime, _asset_mt)
        if mtime != _HTML_MTIME or not _HTML_CACHE:
            raw = _HTML_FILE.read_text(encoding="utf-8")
            # 注入运行时变量：VNC 端口、Web 端口
            novnc_port = int(os.environ.get("NOVNC_PORT", WEB_PORT + 10))
            inject = (
                f'<script>window._vncPort={novnc_port};'
                f'window._webPort={WEB_PORT};</script>\n'
            )
            raw = raw.replace("</head>", inject + "</head>", 1)
            # [cache-bust 2026-09-03] 给本地 /assets/*.js|.css 追加 ?v=<文件mtime>。
            # 之前无版本参数, app.js 内容更新后 URL 不变, 浏览器一直用缓存的旧版
            # (实测: 加了 toggleStrat 但浏览器报 "toggleStrat is not defined")。
            # 用文件 mtime 做版本戳, 内容一变 URL 就变, 浏览器自动拉新的。
            import re as _re_cb
            def _stamp(m):
                _attr, _path = m.group(1), m.group(2)
                try:
                    _mt = int((_ASSETS_DIR / _path.split("/")[-1]).stat().st_mtime)
                    return f'{_attr}="/assets/{_path.split("/")[-1]}?v={_mt}"'
                except Exception:
                    return m.group(0)
            raw = _re_cb.sub(r'(src|href)="(/assets/[a-zA-Z0-9_.-]+\.(?:js|css))"', _stamp, raw)
            _HTML_CACHE = raw
            _HTML_MTIME = mtime
    except FileNotFoundError:
        _HTML_CACHE = "<h1>web_ui.html not found</h1>"
    return _HTML_CACHE

# [router-2026-05] /assets/* + / 已搬到 routers/static_router.py
# 注入 _get_html 到 state, 让 static_router 能访问 (函数定义需先就位)
_shared_state.get_html = _get_html

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else WEB_PORT
    print(f"LiteCode Web UI  →  http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
