#!/usr/bin/env python3
"""
mock_llm_server.py — 本地模拟 LLM 后端, 无需真实大模型就能跑 LiteCode 全链路.

用法:
    # 在 config.json 里把 model.backend_url 改为 http://127.0.0.1:19999/v1
    # 然后:
    python3 tests/mock_llm_server.py &
    python3 litecode_server.py &
    python3 tests/test_full.py --tier smoke

  或者直接用 --port 指定端口, 让 litecode_server.py 连这个 mock。

端点: POST /v1/chat/completions (OpenAI 兼容)
行为:
  根据 request.messages 最后一条用户内容里的关键字, 决定返回什么.
  - "调用 write_file / 写文件 / 创建" → 返回 tool_call: write_file
  - "执行 / 跑命令 / execute_shell" → 返回 tool_call: execute_shell
  - "搜索 / 查询 / web_search" → 返回 tool_call: web_search
  - "vision / 识别图片 / OCR" → 返回 tool_call: vision_ocr
  - "完成 / 总结 / DONE" → 返回 finish (纯文本)
  - 默认: 返回简单的文本回应

支持 stream SSE 输出 (与真实 vLLM 一致).
"""
from __future__ import annotations
import argparse, json, re, time, asyncio, sys
from typing import AsyncGenerator
from pathlib import Path

try:
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse, JSONResponse
except ImportError:
    print("需要 fastapi + uvicorn: pip3 install fastapi uvicorn --break-system-packages")
    sys.exit(1)

app = FastAPI()

# ── 响应剧本 ─────────────────────────────────────────────
# 每次请求返回的预制响应, 根据最后用户消息关键字匹配
SCRIPTS = [
    # (keyword_or_regex, response_template)
    # response_template = {"text": str, "tool_calls": [{"name":..., "args":{...}}]}
    {
        "match": re.compile(r"写.*文件|创建.*文件|write_file|写.*脚本", re.I),
        "text": "好, 我来写文件. ",
        "tool_calls": [{
            "name": "write_file",
            "arguments": {"filepath": "/tmp/openclaw_workspace/mock_test.py",
                          "content": "print('MOCK_OK')\n"}
        }],
    },
    {
        "match": re.compile(r"执行|跑一下|运行.*脚本|execute_shell", re.I),
        "text": "我来执行命令. ",
        "tool_calls": [{"name": "execute_shell", "arguments": {"command": "echo MOCK_RUN_OK"}}],
    },
    {
        "match": re.compile(r"搜索|查询|web_search|查.*资料", re.I),
        "text": "我搜一下. ",
        "tool_calls": [{"name": "web_search", "arguments": {"query": "mock test query"}}],
    },
    {
        "match": re.compile(r"vision|识别图片|OCR|图片.*文字", re.I),
        "text": "识别图片. ",
        "tool_calls": [{"name": "vision_ocr", "arguments": {"path": "/tmp/openclaw_workspace/mock.png"}}],
    },
    {
        "match": re.compile(r"今.*年|2026|日期", re.I),
        "text": "今年是 2026 年.",
        "tool_calls": [],
    },
    {
        "match": re.compile(r"MVRV|指标", re.I),
        "text": "MVRV (Market Value to Realized Value) 是加密货币估值指标, 即市值 / 已实现市值, > 1 说明持币者平均盈利.",
        "tool_calls": [],
    },
]

# 默认响应 (没匹配到时)
DEFAULT_RESP = {"text": "[mock LLM] 已收到, 没有特殊规则匹配, 我直接回答: ok.", "tool_calls": []}

# ── 请求计数器 (后续场景可根据迭代次数返回不同响应) ─────────
_req_counter = {}  # keyed by user id


def _match_script(messages: list) -> dict:
    """按最后一条用户消息关键字匹配响应模板.

    v1.1.1: 如果消息数 > 6 (表示 agent 已循环多轮), 强制返回 finish 避免死循环.
    真实 LLM 会根据 tool_result 内容判断是否完成; mock 用简单计数替代.
    """
    # 超过 N 轮 tool_call 后强制结束 (防止 mock 被 agent 一直调用)
    tool_msgs = sum(1 for m in messages if m.get("role") in ("tool", "assistant"))
    if tool_msgs >= 4:
        return {"text": "任务完成, 结果已记录.", "tool_calls": []}

    for msg in reversed(messages):
        if msg.get("role") == "user":
            # content 可能是 str 或 list (vision)
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            elif not isinstance(content, str):
                content = str(content)
            for script in SCRIPTS:
                if script["match"].search(content):
                    return script
            break
    return DEFAULT_RESP


def _openai_chunk(delta: dict, finish: str = None, mid: str = None, model: str = "mock") -> str:
    """生成单个 OpenAI SSE chunk."""
    cid = mid or f"chatcmpl-mock-{int(time.time()*1000)}"
    body = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish,
        }]
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


async def _stream_response(messages: list, model: str) -> AsyncGenerator[str, None]:
    """把预制脚本响应转成 SSE 流."""
    script = _match_script(messages)
    text = script.get("text", "")
    tool_calls = script.get("tool_calls", [])

    # 1. 先逐字流式吐文本
    for ch in text:
        yield _openai_chunk({"content": ch}, model=model)
        await asyncio.sleep(0.005)

    # 2. 吐 tool_calls (如有)
    if tool_calls:
        for i, tc in enumerate(tool_calls):
            yield _openai_chunk({
                "tool_calls": [{
                    "index": i,
                    "id": f"call_{int(time.time()*1000)}_{i}",
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": ""},
                }]
            }, model=model)
            # 逐块输出 arguments JSON (模拟 vLLM 的 streaming tool args)
            args_str = json.dumps(tc.get("arguments", {}), ensure_ascii=False)
            for part in [args_str]:
                yield _openai_chunk({
                    "tool_calls": [{
                        "index": i,
                        "function": {"arguments": part},
                    }]
                }, model=model)
                await asyncio.sleep(0.002)

        # finish = tool_calls
        yield _openai_chunk({}, finish="tool_calls", model=model)
    else:
        # 纯文本 finish = stop
        yield _openai_chunk({}, finish="stop", model=model)

    # 3. usage
    prompt_tokens = sum(len(str(m.get("content",""))) for m in messages) // 4
    completion_tokens = len(text) // 4 + 10
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    yield _openai_chunk({"usage": usage}, model=model)

    yield "data: [DONE]\n\n"


# ── 路由 ─────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    model    = body.get("model", "mock")
    stream   = body.get("stream", False)

    if not stream:
        # 非流式: 一次性返回
        script = _match_script(messages)
        resp = {
            "id": f"chatcmpl-mock-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": script.get("text", ""),
                    "tool_calls": [{
                        "id": f"call_mock_{i}",
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments",{}))}
                    } for i, tc in enumerate(script.get("tool_calls", []))] or None,
                },
                "finish_reason": "tool_calls" if script.get("tool_calls") else "stop",
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        return JSONResponse(resp)

    return StreamingResponse(
        _stream_response(messages, model),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/models")
async def list_models():
    return {"data": [{"id": "mock", "object": "model"}]}


@app.get("/health")
async def health():
    return {"ok": True, "mock": True}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=19999)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"[mock_llm] listening on http://{args.host}:{args.port}/v1/chat/completions")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
