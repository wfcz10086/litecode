"""lib/sse.py — SSE 事件格式化（OpenAI chat.completion.chunk 兼容）"""
import json, uuid
from lib.config import _LIVE

def _mkid() -> str:
    return uuid.uuid4().hex[:8]

def _cur_model() -> str:
    """实时读 _LIVE, 而不是 import 时绑死的 MODEL_ID —
    否则 /api/models/switch 热切换后, SSE 标签仍显示切换前的旧模型."""
    return _LIVE.get("model_id", "")

def sse_content(text: str, model: str | None = None) -> str:
    d = {"id": f"cw-{_mkid()}", "object": "chat.completion.chunk", "model": model or _cur_model(),
         "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

def sse_reasoning(text: str, model: str | None = None) -> str:
    """ 推送推理/思考过程到前端（可折叠显示）"""
    d = {"id": f"cr-{_mkid()}", "object": "chat.completion.chunk", "model": model or _cur_model(),
         "choices": [{"index": 0, "delta": {"reasoning": text}, "finish_reason": None}]}
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

def sse_status(key: str, value: dict, model: str | None = None) -> str:
    d = {"id": f"cs-{_mkid()}", "object": "chat.completion.chunk", "model": model or _cur_model(),
         "choices": [{"index": 0, "delta": {key: value}, "finish_reason": None}]}
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

def sse_stop(model: str | None = None) -> str:
    d = {"id": f"ce-{_mkid()}", "object": "chat.completion.chunk", "model": model or _cur_model(),
         "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

def sse_done() -> str:
    return "data: [DONE]\n\n"

def sse_ping() -> str:
    """SSE 注释帧 (以 : 开头, EventSource/reader 均忽略), 用于活探测."""
    return ": keep-alive\n\n"

def sse_error(reason: str, retry_hint: bool = True, model: str | None = None,
              friendly: dict | None = None) -> str:
    """[P46 修] LLM 空响应 / 上游异常 → 前端可见错误事件
    [trace 2026-10-22] friendly = lib.friendly_errors.classify(...) 结果: 追加可选字段
    kind/title/hint/actions/details (+content_echo), 老消费者只读 reason/retry_hint 不受影响。"""
    er = {"reason": reason, "retry_hint": retry_hint}
    if friendly:
        for k in ("kind", "title", "hint", "actions", "details", "content_echo"):
            if friendly.get(k) is not None:
                er[k] = friendly[k]
    d = {"id": f"cerr-{_mkid()}", "object": "chat.completion.chunk", "model": model or _cur_model(),
         "choices": [{"index": 0, "delta": {"error": er}, "finish_reason": "error"}]}
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

def sse_notice(text: str, level: str = "info", kind: str = "", model: str | None = None) -> str:
    """[trace 2026-10-22] 整轮级的可读说明 (自动重试 / 切换备用模型等), 前端显示在步骤时间线里。"""
    return sse_status("notice", {"text": text, "level": level, "kind": kind}, model)

def sse_meta(event: str, op: str, name_or_id: str, model: str | None = None) -> str:
    """通知前端面板刷新（dag_changed / timer_changed）。工具成功后由 server 层 yield。"""
    d = {"id": f"cm-{_mkid()}", "object": "chat.completion.chunk", "model": model or _cur_model(),
         "choices": [{"index": 0, "delta": {"panel_refresh": {
             "event": event, "op": op, "name": name_or_id
         }}, "finish_reason": None}]}
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

def sse_usage(prompt_tokens: int, completion_tokens: int, iterations: int = 1,
              elapsed: float = 0, model: str | None = None) -> str:
    d = {"id": f"ce-{_mkid()}", "object": "chat.completion.chunk", "model": model or _cur_model(),
         "choices": [{"index": 0, "delta": {"usage": {
             "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
             "total_tokens": prompt_tokens + completion_tokens,
             "iterations": iterations, "elapsed_seconds": round(elapsed, 1)
         }}, "finish_reason": None}]}
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"
