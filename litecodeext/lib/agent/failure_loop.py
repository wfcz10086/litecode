"""failure_loop.py — 把 lib/failure_memory 接进 agent 主循环, 形成"失败经验"闭环。

  记录: 主循环在失败出口调 mark(ctx, kind, ...) 打标 (异常 / 工具重复熔断 / 迭代上限 /
        超时 / 空回耗尽), 回合收尾时 record_turn() 统一写一条 (任务摘要 + 卡点 + 错误片段,
        全部截断 + 脱敏)。连续工具报错达 MAX_ERROR_STREAK 收尾的回合也算失败。
  召回: recall_source() 注册为 lib/turn_context 的一个来源 (见 lib/turn_sources.py),
        新回合用用户原话 find_similar, 相似度 ≥ 阈值才给一段 ≤ max_inject_chars 的
        "[SYSTEM-EXPERIENCE]" 提示。挂在 user 消息的 turn_context 字段里: 界面不显示,
        记忆提取前被 strip, system prompt 字节不变 (不破前缀缓存)。
  开关: config.json agent.failure_memory = {enabled, threshold, max_inject_chars,
        min_task_chars, scope}; 也可直接写 false。enabled=false 时 mark 照常打标
        (只是 ctx 上一个字段), record/recall 全部短路, 零行为变化。
  存储: 默认全局 WORKSPACE/telemetry/failures.jsonl (经验跨会话才有用; 本机是单人自用)。
        微信/企微会话 (wx- / wecom 前缀, 一个会话对应一个外部联系人) 强制按会话隔离到
        WORKSPACE/sessions/<sid>/telemetry/failures.jsonl, 避免把 A 的任务内容提示给 B;
        scope="session" 时所有会话都按会话隔离。
  任何异常只 log.warning, 绝不影响主流程。成功回合不记录, 但会清掉相似任务的旧失败 (2026-10-19)。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from lib import failure_memory as _fm
from lib import turn_context as _tc
from lib.config import CFG, WORKSPACE, MAX_ERROR_STREAK, log
from lib.scrub import scrub_secrets

PREFIX = "[SYSTEM-EXPERIENCE]"
_DEFAULTS = {
    "enabled": True,
    "threshold": 0.6,          # 字符 bigram Jaccard; 同一任务换个说法约 0.6~0.8, 无关任务 < 0.2
    "max_inject_chars": 400,
    "min_task_chars": 8,       # 太短的消息 ("继续"/"好的") 不记也不召回, 防误命中
    "scope": "global",         # global | session
    "max_age_days": 7,         # [2026-10-19] 超过 N 天的失败经验不再召回 (原来永不过期)
    "resolve_threshold": 0.85, # [2026-10-19] 几乎同一任务后来成功 → 清掉旧失败; 比召回阈值严,
                               # 只是"相似"的成功可能正是靠那条经验做成的, 留着 (靠 max_age_days 过期)
}
_PER_SESSION_PREFIXES = ("wx-", "wecom")
_TASK_CHARS = 200              # 任务摘要截断长度 (记录与查询同长, Jaccard 才可比)

# 失败类型 → 给下一次的规避建议
_HINTS = {
    "tool_loop_break": "同一工具+参数反复重试没有进展; 换思路或换工具, 信息不够就先问用户",
    "max_iter": "迭代轮数用尽仍未收敛; 先把任务拆成子步骤逐个推进, 每步确认结果",
    "timeout": "任务超时; 先交付阶段性结果, 把大任务拆小分批做",
    "error_streak": "工具连续报错; 先读报错定位根因 (路径/依赖/权限/环境), 不要原样重跑",
    "empty_reply": "多次空回复; 直接调用工具动手或简短回答, 不要在思考里反复打草稿",
    "turn_exception": "回合异常中断; 缩小单步规模, 少一次性读大文件/大输出",
}
_CTX_LEN_HINT = "上下文超长; 少一次性读整文件或长输出, 用搜索/分段读取"

# 基础设施故障 (模型服务不可达/断流/5xx/重启中) 与任务本身无关: 记下来只会给相似任务注入误导性"经验"
_INFRA_ERR = re.compile(
    r"(?i)unreachable|connect(ion)?\s*(error|refused|reset)|all connection attempts failed|"
    r"server disconnected|remoteprotocolerror|readerror|readtimeout|read timeout|"
    r"vllm returned 5\d\d|\b50[234]\b|bad gateway|service unavailable|"
    r"连不上模型|模型服务.*(重启|不可用)|restarting")

# scrub_secrets 只认 sk-/Bearer/KEY=value 等格式; 这里补自然语言写法和长随机串
_EXTRA_SECRET_PATS = [
    (re.compile(r"(?i)(password|passwd|pwd|token|api[_\-]?key|secret|密码|口令|令牌|密钥)"
                r"(\s*(?:[:=：]|是|为)\s*)[^\s,，;；\"']{3,}"), r"\1\2<REDACTED>"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "<REDACTED:token>"),
]


def cfg() -> dict:
    raw = (CFG.get("agent") or {}).get("failure_memory", {})
    if isinstance(raw, bool):
        raw = {"enabled": raw}
    out = dict(_DEFAULTS)
    if isinstance(raw, dict):
        out.update(raw)
    return out


def enabled() -> bool:
    return bool(cfg().get("enabled", True))


def scrub(text: Any) -> str:
    s = scrub_secrets(str(text or ""))
    for pat, sub in _EXTRA_SECRET_PATS:
        s = pat.sub(sub, s)
    return s


def _clip(text: Any, n: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def task_text(user_message: Any) -> str:
    return _clip(scrub(user_message if isinstance(user_message, str) else ""), _TASK_CHARS)


def store_dir(session_id: Optional[str]) -> Path:
    sid = session_id or ""
    if sid and (cfg().get("scope") == "session" or sid.startswith(_PER_SESSION_PREFIXES)):
        return Path(WORKSPACE) / "sessions" / sid
    return Path(WORKSPACE)


# ── 记录 ──────────────────────────────────────────────────────────
def mark(ctx, kind: str, stuck_at: str = "", detail: str = "") -> None:
    """失败出口打标。只记第一个信号 (最早的出口最接近根因)。不做 IO, 不会抛。"""
    try:
        if getattr(ctx, "failure", None) is None:
            ctx.failure = {"kind": kind, "stuck_at": stuck_at, "detail": detail}
    except Exception:
        pass


def _last_tool_and_error(new_msgs: list) -> tuple[str, str]:
    """本轮最后出错的工具名 + 最后一条真实报错 (没有真实报错才退回守卫拦截文案)。"""
    names: dict = {}
    last_tool, last_err, last_guard = "", "", ""
    for m in new_msgs or []:
        if not isinstance(m, dict):
            continue
        for tc in m.get("tool_calls") or []:
            try:
                names[tc.get("id")] = tc["function"]["name"]
                last_tool = tc["function"]["name"]
            except Exception:
                continue
        if m.get("role") == "tool":
            c = str(m.get("content") or "")
            if c.startswith("ERROR") or "Traceback" in c[:400]:
                last_err = c.split("[SYSTEM", 1)[0]   # 去掉 tool_exec 追加的 [SYSTEM: ...] 指令尾巴
            elif c.startswith("[SYSTEM-LOOP-GUARD]"):
                last_guard = c
            else:
                continue
            last_tool = names.get(m.get("tool_call_id"), last_tool)
    return last_tool, (last_err or last_guard)


def record_turn(ctx, user_message: Any, error_streak: int = 0) -> bool:
    """回合收尾: 有失败信号才写一条。返回是否写入了新记录。"""
    try:
        if not enabled():
            return False
        f = getattr(ctx, "failure", None)
        if f is None and error_streak >= MAX_ERROR_STREAK:
            f = {"kind": "error_streak", "stuck_at": "", "detail": f"连续 {error_streak} 次工具报错"}
        if not f:
            _resolve_on_success(ctx, user_message)
            return False
        c = cfg()
        task = task_text(user_message)
        if len(task) < int(c.get("min_task_chars", 8)):
            return False
        last_tool, last_err = _last_tool_and_error(getattr(ctx, "new_msgs", []))
        kind = f.get("kind") or "turn_exception"
        detail = f.get("detail") or last_err
        if kind == "turn_exception" and _INFRA_ERR.search(detail or ""):
            log.info(f"  [FAILURE-MEMORY:{getattr(ctx, 'sid_tag', '?')}] 基础设施故障不记经验: {_clip(detail, 80)}")
            return False
        hint = _HINTS.get(kind, _HINTS["turn_exception"])
        if kind == "turn_exception" and re.search(r"(?i)context length|context_length|too many tokens", detail):
            hint = _CTX_LEN_HINT
        new = _fm.record_failure(store_dir(getattr(ctx, "session_id", None)), kind, task, fix_hint=hint, extra={
            "stuck_at": _clip(scrub(f.get("stuck_at") or last_tool or "LLM 调用"), 60),
            "detail": _clip(scrub(detail), 160),
        })
        log.info(f"  [FAILURE-MEMORY:{getattr(ctx, 'sid_tag', '?')}] {kind} "
                 f"{'记录' if new else '已存在(去重)'}: {task[:60]}")
        return new
    except Exception as e:
        log.warning(f"  [FAILURE-MEMORY] record 失败 (已忽略): {e}")
        return False


def _resolve_on_success(ctx, user_message: Any) -> int:
    """[2026-10-19] 回合无失败信号且有最终回复 → 视为成功, 删掉相似任务的旧失败记录。
    原来成功回合不碰记录, 早就做成功的任务仍被反复提示"之前失败过… 避免: …", 模型照着绕远路。"""
    try:
        msgs = getattr(ctx, "new_msgs", None) or []
        final = next((m for m in reversed(msgs) if isinstance(m, dict) and m.get("role") == "assistant"), None)
        if not final or not str(final.get("content") or "").strip() or final.get("tool_calls"):
            return 0   # 没有最终文字回复 (被中断/只调了工具) 不算成功
        c = cfg()
        task = task_text(user_message)
        if len(task) < int(c.get("min_task_chars", 8)):
            return 0
        n = _fm.resolve_similar(store_dir(getattr(ctx, "session_id", None)), task,
                                float(c.get("resolve_threshold", 0.85)))
        if n:
            log.info(f"  [FAILURE-MEMORY:{getattr(ctx, 'sid_tag', '?')}] 相似任务本轮成功, 清除 {n} 条旧失败: {task[:60]}")
        return n
    except Exception as e:
        log.warning(f"  [FAILURE-MEMORY] resolve 失败 (已忽略): {e}")
        return 0


# ── 召回 ──────────────────────────────────────────────────────────
def recall_text(user_message: Any, session_id: Optional[str] = None) -> str:
    """相似度过阈值的最近一条失败经验 → 一段 ≤ max_inject_chars 的提示; 否则空串。"""
    try:
        if not enabled():
            return ""
        c = cfg()
        task = task_text(user_message)
        if len(task) < int(c.get("min_task_chars", 8)):
            return ""
        hits = _fm.find_similar(store_dir(session_id), "", task, limit=1,
                                max_age_days=float(c.get("max_age_days", 7) or 0))
        if not hits or float(hits[0].get("_sim", 0)) < float(c.get("threshold", 0.6)):
            return ""
        h = hits[0]
        text = (f"{PREFIX} 类似任务之前失败过 (相似度 {float(h['_sim']):.2f}): "
                f"「{_clip(h.get('summary'), 60)}」卡在 {_clip(h.get('stuck_at') or '?', 40)}"
                f" ({h.get('error_type', '?')})")
        if h.get("detail"):
            text += f", 报错: {_clip(h['detail'], 100)}"
        text += f"。避免: {h.get('fix_hint') or '换个思路'}。仅供参考, 本次情况不同就忽略。"
        return _clip(text, int(c.get("max_inject_chars", 400)))
    except Exception as e:
        log.warning(f"  [FAILURE-MEMORY] recall 失败 (已忽略): {e}")
        return ""


def recall_source(inp: "_tc.TurnInput") -> str:
    """lib/turn_context 来源: 同一条经验本会话历史里已给过就不再重复给。"""
    text = recall_text(inp.user_message, inp.session_id or None)
    if text and text in _tc.seen_in(inp.history):
        return ""
    return text
