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

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from routers.auth_router import require_auth
from lib.chat_runs import REGISTRY

router = APIRouter()

SSE_HIGH_VOLUME_WARN = 5000    # [v1.9 P38-b] chunks 超阈值发警告 telemetry
SSE_HARD_LIMIT = 50000         # chunks 硬上限 (防止 LLM 失控刷屏)
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
    dv = delta.get("diff_view")
    if isinstance(dv, dict) and isinstance(dv.get("diff"), str) and dv["diff"]:
        collected["diffs"].append({"filepath": dv.get("filepath", ""), "diff": dv["diff"]})
    step = delta.get('desktop_step')
    if isinstance(step, dict) and isinstance(step.get('id'), str):
        steps = collected.setdefault('desktop_steps', [])
        if not any(s.get('id') == step['id'] for s in steps):
            steps.append(step)


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


def _producer(sid: str, url: str, payload: dict):
    """读上游 server 的 SSE, 逐帧产出; 结束时落库 assistant 消息。与浏览器连接无关。"""
    w = _wui()

    async def gen():
        collected = {"text": "", "reasoning": "", "tools": [], "diffs": [], "usage": None, "desktop_steps": []}
        t0, chunks, aborted = time.time(), 0, ""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
                async with client.stream("POST", url, headers=w.HEADERS, json=payload) as resp:
                    if resp.status_code != 200:
                        yield f'data: {{"error":"HTTP {resp.status_code}"}}\n\n'
                        return
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if chunks >= SSE_HARD_LIMIT:
                            aborted = f"hard_limit_{SSE_HARD_LIMIT}"
                            yield f'data: {{"error":"SSE chunk limit {SSE_HARD_LIMIT} reached, aborted"}}\n\n'
                            break
                        chunks += 1
                        _collect(collected, line)
                        yield line + "\n\n"
        except Exception as e:
            aborted = aborted or "upstream_error"
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            _emit_stats(sid, t0, chunks, aborted)
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
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(400, "empty")
    run = start_chat_turn(sid, message, body)
    return _follow(request, run, 0)


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
    session["messages"].append({"role": "user", "content": message, "ts": ts})
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
               "messages": [{"role": "user", "content": content}], "user": sid}
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
    url = f"{w.SERVER_URL}/v1/chat/completions"
    if "__TRIGGER_EMPTY_RESPONSE__" in message:   # [P46 测试模式] 走 server 空响应路径
        url += "?__test_empty=1"
    run = REGISTRY.start(sid, _producer(sid, url, payload))
    run.user_ts = ts
    return run


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
