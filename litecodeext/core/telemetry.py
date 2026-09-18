"""
core/telemetry.py — 统一埋点入口（v1.8 P36-c）
================================================

设计目的：
- 所有埋点（skills/subagent/tools/dag/mcp/rag/plan_act/...）走唯一入口
- 自动注入基础 5 元组：ts / trace_id / session_id / event / latency_ms
- 跨线请求关联（用 ContextVar trace_id）
- 失败静默（不影响主流程）

API：
    from core.telemetry import emit, get_trace_id, set_trace_id, new_trace

    emit("skill_match", {"name": "novel"}, jsonl="skills.jsonl", sid="abc")
    new_trace()  # 重置 trace_id（每个请求开头调）
    get_trace_id() / set_trace_id(...)

向后兼容：
- 旧 jsonl 路径保留（workspace/telemetry/<name>.jsonl）
- 旧业务字段保留（caller 传什么 fields 就写什么）
"""
import json
import os
import secrets
import time
from contextvars import ContextVar
from pathlib import Path

# ── 工作区路径 ─────────────────────────────────────────────
_WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE",
                                 os.environ.get("LITECODE_WORKSPACE",
                                                "/tmp/litecode_workspace")))
_TELEMETRY_DIR = _WORKSPACE / "telemetry"

# ── trace_id ContextVar（跨子代理/工具链路传播）─────────────
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def _new_trace_id() -> str:
    """生成 8 字符短 hex trace_id"""
    return secrets.token_hex(4)


def get_trace_id() -> str:
    """读当前 trace_id；空串表示未设置"""
    return _trace_id_var.get()


def set_trace_id(tid: str) -> None:
    _trace_id_var.set(tid)


def new_trace() -> str:
    """开新请求时调：重置 trace_id 并返回"""
    tid = _new_trace_id()
    _trace_id_var.set(tid)
    return tid


# ── 主入口 ─────────────────────────────────────────────────
def emit(event: str, fields: dict, jsonl: str, sid: str = "",
         latency_ms: int | None = None) -> bool:
    """
    写入一条埋点。

    Args:
        event:      业务事件名（必填，例 "skill_match"/"tool_call"/"dag_step"）
        fields:     业务字段 dict（自由扩展）
        jsonl:      目标文件名（如 "skills.jsonl"），写到 telemetry_dir/<jsonl>
        sid:        会话 ID（可空）
        latency_ms: 耗时（可空）

    Returns:
        bool — 成功 True / 失败 False（不抛异常）
    """
    try:
        _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        # 5 元组 + 业务字段
        entry = {
            "ts": time.time(),
            "trace_id": _trace_id_var.get() or _new_trace_id(),
            "session_id": (sid or "")[:32],
            "event": event[:40],
        }
        if latency_ms is not None:
            entry["latency_ms"] = int(latency_ms)
        # 业务字段后置（基础字段不被覆盖）
        if isinstance(fields, dict):
            for k, v in fields.items():
                if k in entry:
                    continue
                entry[k] = v
        path = _TELEMETRY_DIR / jsonl
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False
