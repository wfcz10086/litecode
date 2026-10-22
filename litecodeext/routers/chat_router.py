"""chat_router.py — Web 对话: 发消息 + 重连跟随进行中的轮次。

POST /api/chat/{sid}          起一轮 (后台跑, 不随浏览器断开而停), 返回从头跟随的 SSE
GET  /api/chat/{sid}/run      本会话是否有进行中的轮次
GET  /api/chat/{sid}/stream   从 ?from=N 帧回放 + 实时跟随 (刷新 / 切回 / 别的标签页)

轮次生命周期见 lib/chat_runs.py。helper (load_session/save_session/_save_assistant_msg/
_build_multimodal_content/SERVER_URL/HEADERS/MODEL) 仍在 web_ui.py, lazy 拿。
"""
import asyncio
import json
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from routers.auth_router import require_auth
from routers._body import json_object
from lib.chat_runs import REGISTRY
from lib import tool_trace as _tt
from lib.friendly_errors import classify as _fe_classify

router = APIRouter()
# [ui-audit 2026-10-21] 单条消息字数上限: 原来不限, 粘几 MB 日志会整段塞进模型上下文直接爆窗口
MAX_MESSAGE_CHARS = 120_000
ORDER = 140  # 注册顺序 (routers.discover 按此升序 include, 路径重叠时先注册者生效)

SSE_HIGH_VOLUME_WARN = 5000    # [v1.9 P38-b] chunks 超阈值发警告 telemetry
SSE_HARD_LIMIT = 50000         # chunks 硬上限 (防止 LLM 失控刷屏)
FINISHING_WAIT_S = 10.0        # 上一轮已发 [DONE] 正在收尾时, 新消息最多等这么久
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _wui():
    import web_ui  # type: ignore
    return web_ui


def _collect(collected: dict, line: str):
    """从上游帧里收集要落库的 assistant 内容。"""
    if not line.startswith("data: ") or line[6:] in ("[DONE]", ""):
        return
    try:
        delta = json.loads(line[6:])["choices"][0]["delta"]
    except Exception:
        return
    ct = delta.get("content", "")
    if ct:
        collected["text"] += ct
    rz = delta.get("reasoning", "")   # [v1.0] 推理流持久化展示, 不回流到 LLM 上下文
    if rz:
        collected["reasoning"] += rz
    if "usage" in delta:
        collected["usage"] = delta["usage"]
    for key in ("task_exec", "data_collect", "task_analysis"):
        # "推理中…" 占位 (key=preparing) 只是等首包的反馈, 不算工具调用, 不落库
        d = delta.get(key)
        if d and d.get("status") == "executing" and d.get("key") != "preparing":
            det = d.get("detail", "")
            if det:
                collected["tools"].append(det)
    ts = delta.get("turn_skills")   # 本轮用到的技能 → 助手消息 meta.skills (刷新后可见)
    if isinstance(ts, list):
        collected.setdefault("meta", {})["skills"] = [str(x) for x in ts][:20]
    dv = delta.get("diff_view")
    if isinstance(dv, dict) and isinstance(dv.get("diff"), str) and dv["diff"]:
        collected["diffs"].append({"filepath": dv.get("filepath", ""), "diff": dv["diff"]})
    step = delta.get('desktop_step')
    if isinstance(step, dict) and isinstance(step.get('id'), str):
        steps = collected.setdefault('desktop_steps', [])
        if not any(s.get('id') == step['id'] for s in steps):
            steps.append(step)
    # [trace 2026-10-22] 工具结果 / 进度 / 自动重试说明 / 整轮错误 → trace_steps / turn_error 落库, 刷新后重绘
    _tt.collect_delta(collected, delta)


def _error_frame(collected: dict, raw: str, status: int | None = None) -> str:
    """顶层 error 帧 (老格式 {"error": "..."} 不变) + 可选 friendly 字段; 同时记进 collected 落库。"""
    fe = _fe_classify(raw, status=status)
    collected["turn_error"] = fe
    return f"data: {json.dumps({'error': raw, 'friendly': fe}, ensure_ascii=False)}\n\n"


def _emit_stats(sid: str, t0: float, chunks: int, aborted: str):
    try:
        from core.telemetry import emit
        event = ("sse_stream_high_volume" if chunks >= SSE_HIGH_VOLUME_WARN
                 else "sse_stream_aborted" if aborted else "sse_stream_done")
        emit(event=event, jsonl="sse.jsonl", sid=sid, latency_ms=int((time.time() - t0) * 1000),
             fields={"sid": sid[:32], "chunk_count": chunks, "aborted_reason": aborted,
                     "high_volume": chunks >= SSE_HIGH_VOLUME_WARN})
    except Exception:
        pass


def _caption_of(line: str) -> str:
    """SSE task_exec 帧 → 录屏字幕 (当前步骤)。"""
    try:
        d = json.loads(line[6:] if line.startswith("data: ") else line)
        te = (d.get("choices") or [{}])[0].get("delta", {}).get("task_exec") or {}
        return "▶ " + str(te.get("detail") or "")[:80]
    except Exception:
        return ""


def _producer(sid: str, url: str, payload: dict):
    """读上游 server 的 SSE, 逐帧产出; 结束时落库 assistant 消息。与浏览器连接无关。"""
    w = _wui()

    async def gen():
        collected = {"text": "", "reasoning": "", "tools": [], "diffs": [], "usage": None, "desktop_steps": []}
        t0, chunks, aborted = time.time(), 0, ""
        # [record 2026-10-21] 会话开了录屏: 本轮录 LiteCode 这个对话窗口, 结束出 mp4
        from lib import screen_record as _rec
        recording = False
        try:
            if _rec.is_on(w.load_session(sid)):
                await _rec.begin(sid)
                recording = True
        except Exception as _re:
            print(f"[record:{sid}] 启动失败: {_re}")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
                async with client.stream("POST", url, headers=w.HEADERS, json=payload) as resp:
                    if resp.status_code != 200:
                        _body = ""
                        try:
                            _body = (await resp.aread()).decode("utf-8", "replace")[:500]
                        except Exception:
                            pass
                        yield _error_frame(collected, f"HTTP {resp.status_code} {_body}".strip(), resp.status_code)
                        return
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if chunks >= SSE_HARD_LIMIT:
                            aborted = f"hard_limit_{SSE_HARD_LIMIT}"
                            yield _error_frame(collected, f"SSE chunk limit {SSE_HARD_LIMIT} reached, aborted")
                            break
                        chunks += 1
                        _collect(collected, line)
                        if recording and '"task_exec"' in line and '"executing"' in line:
                            _rec.caption(sid, _caption_of(line))
                        yield line + "\n\n"
        except Exception as e:
            aborted = aborted or "upstream_error"
            yield _error_frame(collected, f"{type(e).__name__}: {e}")
        finally:
            _emit_stats(sid, t0, chunks, aborted)
            if recording:
                try:
                    mp4 = await _rec.end(sid)
                    if mp4:
                        note = f"\n\n🎬 本轮录屏：{mp4}"
                        collected["text"] += note
                        yield f"data: {json.dumps({'choices': [{'delta': {'content': note}}]}, ensure_ascii=False)}\n\n"
                except Exception as _re:
                    print(f"[record:{sid}] 收尾失败: {_re}")
            try:
                await asyncio.get_event_loop().run_in_executor(None, w._save_assistant_msg, sid, dict(collected))
            except Exception:
                pass
    return gen


def _follow(request: Request, run, start: int = 0):
    """订阅者: 回放 + 跟随。断开只结束订阅, 不影响轮次。"""
    async def gen():
        async for frame in run.follow(start):
            if frame is None:            # 15s 无新帧: 心跳 + 探断连
                if await request.is_disconnected():
                    return
                yield ": keep-alive\n\n"
                continue
            yield frame
    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/api/chat/{sid}")
async def api_chat(sid: str, request: Request):
    require_auth(request)
    from lib.rate_limit import rate_check   # [P0-#4] chat 节流: 30 次 / 60s / IP
    rate_check(request, "chat", 30, 60)
    body = await json_object(request)
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "empty")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(413, f"消息过长 ({len(message)} 字), 上限 {MAX_MESSAGE_CHARS} 字; 大段内容请存成文件用 📎 上传")
    # 上一轮已发出 [DONE] 只剩收尾 (agent_stream 在 [DONE] 之后才落库历史) 时, 等它结束再开新一轮:
    # 客户端读到 [DONE] 立刻发下一条会撞上这个窗口, 直接 409 不合理; 抢跑又会读不到上一轮历史。
    # 仍在生成中的轮次照旧 409, 同会话串行不变。
    prev = REGISTRY.get(sid)
    if prev and not prev.done and prev.finishing:
        await prev.wait_finished(timeout=FINISHING_WAIT_S)
    # [per-chat-model 2026-10-19] `/model` 指令: 设置本会话模型, 直接回一句确认, 不调模型
    from lib import session_model as _sm
    if _sm.parse_command(message) is not None:
        from lib.owner import require_owner
        require_owner(_wui().load_session(sid), request)
        run = await start_model_command_turn(sid, message)
        return _follow(request, run, 0)
    run = start_chat_turn(sid, message, body)
    return _follow(request, run, 0)


async def start_model_command_turn(sid: str, message: str):
    """把 /model 指令当成一轮"本地回复": 走同一个 REGISTRY (同会话串行 / 断线重连照旧),
    用户消息和回复只记在 web 会话里, 不进 server 侧的模型上下文。"""
    if REGISTRY.running(sid):
        raise HTTPException(409, "该会话正在执行中")
    from lib import session_model as _sm
    from routers.model_router import _active_model_id
    w = _wui()
    reply = _sm.handle_command(sid, message, active_id=await _active_model_id()) or ""
    session = w.load_session(sid)
    ts = time.time()
    session["messages"].append({"role": "user", "content": message.strip(), "ts": ts})
    session["last_used"] = ts
    w.save_session(session)

    def producer():
        async def gen():
            mid = uuid.uuid4().hex[:12]
            yield f"data: {json.dumps({'choices': [{'delta': {'msg_id': mid}}]})}\n\n"
            yield f"data: {json.dumps({'choices': [{'delta': {'content': reply}, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'choices': [{'delta': {'session_model': {'changed': True}}, 'finish_reason': 'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"
            collected = {"text": reply, "reasoning": "", "tools": [], "diffs": [], "usage": None, "desktop_steps": []}
            try:
                await asyncio.get_event_loop().run_in_executor(None, w._save_assistant_msg, sid, collected)
            except Exception:
                pass
        return gen()
    run = REGISTRY.start(sid, producer)
    run.user_ts = ts
    return run


def _forced_skills(raw) -> list:
    if not isinstance(raw, list):
        return []
    from lib.skill_registry import valid_name
    out = []
    for x in raw:
        s = x.strip() if isinstance(x, str) else ""
        if valid_name(s) and s not in out:
            out.append(s)
    return out[:5]


def start_chat_turn(sid: str, message: str, body=None):
    """Web 与定时任务共用入口；同步检查并登记轮次，保持同会话串行。"""
    body = body or {}
    message = message.strip()
    if not message:
        raise HTTPException(400, "empty")
    if REGISTRY.running(sid):
        raise HTTPException(409, "该会话正在执行中")
    w = _wui()
    session = w.load_session(sid)
    ts = time.time()
    umsg = {"role": "user", "content": message, "ts": ts}
    if _forced_skills(body.get("skills")):
        umsg["skills"] = _forced_skills(body.get("skills"))
    session["messages"].append(umsg)
    session["last_used"] = ts
    if len(session["messages"]) == 1:
        session["name"] = message[:40] + ("…" if len(message) > 40 else "")
    w.save_session(session)
    # [v1.4] 消息里引用了本地图片 + 主模型支持视觉 → 组装多模态 content
    content, mm_imgs = w._build_multimodal_content(message)
    if mm_imgs:
        print(f"[api_chat:{sid}] 多模态直传 {len(mm_imgs)} 张图")
    # [V7.e] 前端 chip 参数: 模型 / 思考强度 / DeepSearch (老 UI 不传 → 走全局 MODEL 兼容)
    payload = {"model": body.get("model") or w.MODEL, "stream": True,
               "messages": [{"role": "user", "content": content}], "user": sid,
               "litecode_source": body.get("_usage_source") or "web"}   # [usage-ledger] 账本来源
    effort = (body.get("thinking_effort") or "").strip().lower()
    if effort == "off":   # 显式关闭推理: 各后端格式不同, 一并注入
        payload["enable_thinking"] = False
        payload["thinking_budget"] = 0
    elif effort:
        payload["reasoning_effort"] = effort
    if body.get("deep_search"):
        payload["deep_search"] = True
    if body.get("strategist_mode") or body.get("scenario_mode"):
        payload["strategist_mode"] = True
    forced = _forced_skills(body.get("skills"))
    if forced:   # 用户用 `/` 选择器指定的技能: 本轮全文注入, 不做关键词匹配 (lib/skill_turn.py)
        payload["skills"] = forced
    url = f"{w.SERVER_URL}/v1/chat/completions"
    if "__TRIGGER_EMPTY_RESPONSE__" in message:   # [P46 测试模式] 走 server 空响应路径
        url += "?__test_empty=1"
    run = REGISTRY.start(sid, _producer(sid, url, payload))
    run.user_ts = ts
    return run


@router.post("/api/chat/{sid}/steer")
async def api_chat_steer(sid: str, request: Request):
    """[steer 2026-10-21] 回合进行中插话: 不停止任务, 消息在下一步接进上下文。
    urgent=true 为"立即插话": 放弃正在执行的工具, 马上按插话调整。
    没有进行中的回合 (或已在收尾) 返回 queued=False, 前端改按普通消息发送。"""
    require_auth(request)
    from lib.owner import require_owner
    w = _wui()
    session = w.load_session(sid)
    require_owner(session, request)
    body = await json_object(request)
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "empty")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(413, f"插话过长 ({len(message)} 字), 上限 {MAX_MESSAGE_CHARS} 字")
    run = REGISTRY.get(sid)
    if not run or run.done or run.finishing:
        print(f"[steer:{sid}] 没有进行中的回合, 转普通消息: {message[:40]}")
        return {"ok": True, "queued": False}
    d = await w._aproxy(f"/v1/sessions/{sid}/steer", "POST",
                        json={"text": message, "urgent": bool(body.get("urgent"))})
    if not d or not d.get("queued"):
        print(f"[steer:{sid}] server 未排队 ({d}), 转普通消息: {message[:40]}")
        return {"ok": True, "queued": False}
    session = w.load_session(sid)       # 重新读, 别覆盖轮次里刚写入的内容
    session["messages"].append({"role": "user", "content": message, "ts": time.time(),
                                "steer": True, "urgent": bool(body.get("urgent"))})
    w.save_session(session)
    return {"ok": True, "queued": True, "urgent": bool(d.get("urgent"))}


@router.get("/api/chat/{sid}/run")
async def api_chat_run(sid: str, request: Request):
    require_auth(request)
    run = REGISTRY.get(sid)
    return run.status() if run else {"running": False}


@router.get("/api/chat/{sid}/stream")
async def api_chat_stream(sid: str, request: Request, start: int = 0):
    require_auth(request)
    run = REGISTRY.get(sid)
    if not run:
        raise HTTPException(404, "no turn")
    return _follow(request, run, start)
