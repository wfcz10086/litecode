"""主循环单次 LLM 调用: 400 守卫 + vLLM 流解析, 逐帧产出 SSE 并回填 StreamResult。

从 litecode_server.py 原样搬出 (拆分大文件), 函数体未改动; litecode_server 仍 re-export 这些名字。
"""
from lib import config as _cfg   # 模型热切换后读当前值 (reload_model 更新其模块变量), 不在导入时拷快照
import time
import uuid
from history_utils import _nuke_tool_history
from history_utils import _sanitize_history
from history_utils import _strip_tool_call_literals
from lib import tool_filter as _tf
from lib.agent_state import StreamResult
from lib.config import est_tokens
from lib.config import log
from lib.reasoning_limits import RepeatLineDetector as _RepeatLineDetector
from lib.reasoning_limits import stall_limits as _reasoning_stall_limits
from lib.session import _interrupt_flags
from lib.session import _interrupt_lock
from lib.sse import sse_content
from lib.sse import sse_reasoning
from lib.tools.defs import TOOL_DEFS
from lib.transport import vllm_stream as _vllm_stream
from typing import Optional
from lib.agent.loop_budget import _log_anchor


# ── [AGENT_STREAM_REFACTOR M8 2026-09-18] vLLM 调用+流解析段, 迁自主循环
#    (原 L1523-1879, 358 行/17 yield: 400-GUARD 重试升级/流式增量解析/usage 锚点/
#    reasoning 归一/工具增量拼装/中断检查/空流哨兵/溢出缩 max_tokens)。
#    13 个 continue/break 经 AST 分类全部指向段内循环, 零外层出口 → 整段可迁。
#    out 8 字段 = AST 实测段内写∩段后读; reasoning_stall_count 段内只读故为参数。
#    写回放 finally: 与内联时"异常抛出前变量已就地更新"的可见性语义一致。──
async def _stream_llm_call(ctx, usage, model, last_yield_ts, reasoning_stall_count, out: StreamResult):
    """vLLM 流式调用主体: yield 透传 SSE chunk, 结果经 out 回传 8 字段。"""
    full_text = out.full_text
    acc_tools = out.acc_tools
    finish_reason = out.finish_reason
    full_reasoning = out.full_reasoning
    stream_interrupted = out.stream_interrupted
    loop_broken = out.loop_broken
    iter_real_prompt = out.iter_real_prompt
    iter_real_completion = out.iter_real_completion
    try:
        # ── [400-GUARD] vLLM 400 绝对防御：最多重试3次，逐级升级清理 ──
        _vllm_retry_count = 0
        _vllm_max_retry   = 3
        _vllm_success     = False
        _overflow_max_tokens: Optional[int] = None  # [overflow-fix] 上下文溢出时动态缩 max_tokens
        # [v1.0] <think> 标签跨 chunk 状态（处理嵌入式思考）
        _think_open = False
        _think_buf  = ""
        # [v1.4] 推理死循环检测 — Qwen3 系列有时会陷入"我先搜索X\n我先查天气\n..."
        # 的无限复述, 从不 emit content 或 tool_call. 这里检测:
        #   1) 短句 (≤50字) 在最近 8 行里重复 ≥4 次 → 立即中断
        #   2) reasoning 累积 > 3000 字但仍无 content/tool_call → 中断
        _loop_reasoning_chars = 0      # reasoning 累积字数
        _loop_detector = _RepeatLineDetector()  # 最近 8 个完整推理行的复读检测
        loop_broken = False
        _reasoning_nudge_sent = False  # [v4 2026-07] 5000c nudge, 7000c 断 (原 12000/13500)
        # [FIX] 累加本轮 reasoning 原文，用于 "只推理不干活" 的回落持久化。
        # 以前 delta.reasoning 只 yield 给前端不入库 → 一旦模型只思考不产出，
        # new_msgs 里就只剩开头那条 user → 日志 saved 1 msgs → UX 直接"退出"。
        full_reasoning = ""
        # [TOKEN-STATS 真值 2026-09-05] 本轮上游回报的真实 token (0=本轮没拿到锚点 → 退回估算)。
        # usage 锚点此前只喂上下文预算, 没回写 token_stats → 账单是估的、预算是真的, 思考模型下系统性偏差。
        iter_real_prompt = 0
        iter_real_completion = 0
        while _vllm_retry_count <= _vllm_max_retry:
            try:
                async for chunk in _vllm_stream(ctx.messages, _tf.visible(TOOL_DEFS, ctx.disabled_tools),
                                                 max_tokens_override=_overflow_max_tokens):
                    # ── [STREAM-INTERRUPT] vLLM 流式输出期间检查中断 ──
                    with _interrupt_lock:
                        if ctx.session_id in _interrupt_flags:
                            _interrupt_flags.discard(ctx.session_id)
                            log.warning(
                                f"  \033[31m[INTERRUPT@stream:{ctx.sid_tag}]\033[0m "
                                f"vLLM 流中断, 已累积 text={len(full_text)}c "
                                f"reasoning={len(full_reasoning)}c tools={len(acc_tools)}"
                            )
                            stream_interrupted = True
                            break
                    # [USAGE-ANCHOR] 标准 OpenAI 的 usage 包是 choices=[] + 顶层 usage,
                    # 会被下面 `if not choices: continue` 丢掉 —— 先在这抓一次顶层 usage。
                    _top_usage = chunk.get("usage") if isinstance(chunk, dict) else None
                    choices = chunk.get("choices", [])
                    if not choices:
                        if isinstance(_top_usage, dict):
                            _tpt = _top_usage.get("prompt_tokens")
                            if isinstance(_tpt, int) and _tpt > 0:
                                _cur_est_t = sum(est_tokens(_m.get("content",""))+est_tokens(_m.get("tool_calls",""))+est_tokens(_m.get("reasoning_content",""))
                                                 for _m in ctx.messages)
                                _log_anchor(ctx, usage, _tpt, _cur_est_t)
                                usage.last_real_prompt_tokens = _tpt
                                usage.last_est_at_anchor = _cur_est_t
                                usage.usage_reports += 1
                                iter_real_prompt = _tpt  # [TOKEN-STATS] 回写 stats 用
                                _tct = _top_usage.get("completion_tokens")
                                if isinstance(_tct, int) and _tct > 0:
                                    usage.total_completion_tokens += _tct
                                    iter_real_completion += _tct
                        continue
                    choice        = choices[0]
                    delta         = choice.get("delta", {})
                    finish_reason = choice.get("finish_reason") or finish_reason

                    # [USAGE-ANCHOR 2026-09-03] 采集上游真实 usage 作锚点。
                    # 本中转 vLLM 把 usage 塞在非标准位置 delta.usage
                    # (标准 OpenAI 是顶层 usage), 所以框架此前从没读到。
                    # 两处都认: delta.usage 和 chunk 顶层 usage。
                    _up_usage = delta.get("usage")
                    if not isinstance(_up_usage, dict):
                        _up_usage = chunk.get("usage") if isinstance(chunk, dict) else None
                    if isinstance(_up_usage, dict):
                        _pt = _up_usage.get("prompt_tokens")
                        if isinstance(_pt, int) and _pt > 0:
                            # 锚定: 真实 prompt_tokens ↔ 我方对当前历史的估算值。
                            # 保守取: 真值 >= 估算才用真值 (防上游漏报导致误以为还很空);
                            # 真值偏小时保留估算的锚, 但更新真值供 completion 计费参考。
                            _cur_est = sum(
                                est_tokens(_m.get("content","")) + est_tokens(_m.get("tool_calls",""))
                                + est_tokens(_m.get("reasoning_content",""))
                                for _m in ctx.messages)
                            # [USAGE-ANCHOR] 每次锚定打一行比值 —— 隐形漂移在这里现形。
                            # 比值稳定偏离 1.0 就说明有东西没被估算计入 (今天的 reasoning
                            # 漏算、系统提示词盲区都会在第一次锚定就暴露)。
                            _log_anchor(ctx, usage, _pt, _cur_est)
                            usage.last_real_prompt_tokens = _pt
                            usage.last_est_at_anchor = _cur_est
                            usage.usage_reports += 1
                            iter_real_prompt = _pt  # [TOKEN-STATS] 回写 stats 用
                            _ct2 = _up_usage.get("completion_tokens")
                            if isinstance(_ct2, int) and _ct2 > 0:
                                usage.total_completion_tokens += _ct2  # 真值优先, 覆盖估算
                                iter_real_completion += _ct2

                    # [v1.0] 推理/思考内容提取 — 多后端兼容
                    # vLLM 旧版 (< 0.9)    → delta.reasoning_content
                    # vLLM 新版 (>= 0.9)   → delta.reasoning
                    # Ollama /v1/           → delta.reasoning_content
                    # Ollama /api/chat      → delta.thinking (原生)
                    # 无 parser 的模型       → <think>...</think> 嵌入 content（下方单独处理）
                    _reasoning_chunk = (
                        delta.get("reasoning_content") or
                        delta.get("reasoning") or
                        delta.get("thinking") or
                        ""
                    )
                    if _reasoning_chunk:
                        _rc_clean, _rc_stripped = _strip_tool_call_literals(_reasoning_chunk)
                        if _rc_stripped:
                            log.debug(f"[reasoning-strip] stripped tool-call literals: {_rc_stripped!r}")
                        if _rc_clean:
                            yield sse_reasoning(_rc_clean, model)
                        last_yield_ts = time.time()
                        # [FIX] 累积 reasoning 原文, 流结束若无 content/tools 时留作落库
                        full_reasoning += _rc_clean
                        # [v1.4] 死循环检测
                        _loop_reasoning_chars += len(_reasoning_chunk)
                        if _loop_detector.feed(_reasoning_chunk):
                            log.warning(
                                f"  [REASONING-LOOP] detected, breaking stream. "
                                f"recent={_loop_detector.recent()}"
                            )
                            _loop_msg = ("\n（思考绕进了同一个圈子里，已经先停下来了，"
                                         "换个问法或补充点细节再试一次吧。）\n")
                            full_text += _loop_msg
                            yield sse_content(_loop_msg, model)
                            loop_broken = True
                            break
                        # 字数阈值随当前模型 thinking_budget 变化 (见 lib/reasoning_limits.py),
                        # 下限 7000c; 叠加 reasoning_stall_count 跨迭代升级约束.
                        _nudge_thresh, _break_thresh = _reasoning_stall_limits()
                        if (_loop_reasoning_chars > _nudge_thresh
                                and _loop_reasoning_chars <= _nudge_thresh + 200
                                and not full_text and not acc_tools
                                and not _reasoning_nudge_sent):
                            _reasoning_nudge_sent = True
                            _nudge = (
                                "\n（这次想得有点久了，马上就好，稍等一下……）\n"
                                if reasoning_stall_count > 0 else
                                "\n（还在思考中，稍等一下……）\n"
                            )
                            yield sse_content(_nudge, model)
                        if (_loop_reasoning_chars > _break_thresh
                                and not full_text and not acc_tools):
                            log.warning(
                                f"  [REASONING-STALL] {_loop_reasoning_chars} 字 reasoning 无进展, "
                                f"中断 (阈值 {_break_thresh}, 本次第 {reasoning_stall_count + 1} 次)")
                            _stall_msg = (
                                "\n（这轮想得太久了，已经先停下来了，"
                                "你可以换个说法或拆成更小的问题再问我一次。）\n"
                            )
                            full_text += _stall_msg
                            yield sse_content(_stall_msg, model)
                            loop_broken = True
                            break

                    if delta.get("content"):
                        _ct = delta["content"]
                        # [v1.0] 兼容嵌入式 <think>...</think>（某些模型把思考塞在 content 里）
                        # 用简易正则 + 跨 chunk 状态机（记在 acc_tools 邻位的局部变量里）
                        if "<think>" in _ct or "</think>" in _ct or _think_open or _think_buf or (
                                "<" in _ct and any(_ct.endswith(_p) for _p in (
                                    "<", "<t", "<th", "<thi", "<thin", "<think",
                                    "</", "</t", "</th", "</thi", "</thin", "</think",
                                ))):
                            import re as _re_tt
                            _buf = _think_buf + _ct  # 带上一轮残留
                            _think_buf = ""
                            while True:
                                if not _think_open:
                                    _i = _buf.find("<think>")
                                    if _i < 0:
                                        # 无开标签——但末尾若像疑似前缀，缓存不立即 yield
                                        _PARTIAL_PREFIXES = (
                                            "<think", "<thi", "<th", "<t",
                                            "</think", "</thi", "</th", "</t", "</",
                                            "<",
                                        )
                                        _hold = 0
                                        for _p in _PARTIAL_PREFIXES:
                                            if _buf.endswith(_p):
                                                _hold = len(_p)
                                                break
                                        if _hold > 0:
                                            _emit_part = _buf[:-_hold]
                                            _think_buf = _buf[-_hold:]
                                        else:
                                            _emit_part = _buf
                                        if _emit_part:
                                            full_text += _emit_part
                                            yield sse_content(_emit_part, model)
                                        break
                                    # 开标签前的是内容
                                    if _i > 0:
                                        _pre = _buf[:_i]
                                        full_text += _pre
                                        yield sse_content(_pre, model)
                                    _buf = _buf[_i + 7:]  # 去掉 <think>
                                    _think_open = True
                                else:
                                    _j = _buf.find("</think>")
                                    if _j < 0:
                                        # 闭标签没到, 把剩余当推理 emit, 等下一 chunk
                                        if _buf:
                                            full_reasoning += _buf
                                            yield sse_reasoning(_buf, model)
                                        _buf = ""
                                        break
                                    # 标签内是推理
                                    if _j > 0:
                                        full_reasoning += _buf[:_j]
                                        yield sse_reasoning(_buf[:_j], model)
                                    _buf = _buf[_j + 8:]  # 去掉 </think>
                                    _think_open = False
                        else:
                            full_text += _ct
                            yield sse_content(_ct, model)
                        last_yield_ts = time.time()
                        # [v1.4] 有实质内容 → 重置死循环计数
                        _loop_reasoning_chars = 0
                        _loop_detector.reset()

                    for tc in delta.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        if idx not in acc_tools:
                            acc_tools[idx] = {
                                "id":        tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                "name":      "",
                                "arguments": ""
                            }
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            acc_tools[idx]["name"] = fn["name"]
                        if tc.get("id"):
                            acc_tools[idx]["id"] = tc["id"]
                        acc_tools[idx]["arguments"] += fn.get("arguments") or ""
                        # [v1.4] 有 tool_call 进来 → 重置死循环计数
                        _loop_reasoning_chars = 0
                        _loop_detector.reset()
                        _reasoning_nudge_sent = False  # [v2 2026-05] 一并重置 nudge 状态

                    # [OPT] P0-C: 工具参数累积期间发送心跳，防止客户端 read timeout
                    if time.time() - last_yield_ts > 15:
                        yield ": heartbeat llm_stream\n\n"
                        last_yield_ts = time.time()

                # async for 正常结束
                _vllm_success = True
                break  # 退出 while 重试循环
            except RuntimeError as _vllm_err:
                _vllm_err_str = str(_vllm_err)
                _is_reasoning_400 = "reasoning_content" in _vllm_err_str.lower()
                # [overflow-fix 2026-07] 上下文长度溢出 — 从错误信息里解析 input tokens,
                # 重算安全 max_tokens, 直接重试 (不需要清理 history).
                _is_overflow_400 = (
                    "400" in _vllm_err_str and
                    "maximum context length" in _vllm_err_str.lower() and
                    "input tokens" in _vllm_err_str.lower()
                )
                if _is_overflow_400 and _vllm_retry_count < _vllm_max_retry:
                    _vllm_retry_count += 1
                    import re as _re_ov
                    _input_m = _re_ov.search(r'(\d{4,6})\s+input tokens', _vllm_err_str)
                    _ctx_m   = _re_ov.search(r'maximum context length is (\d+)', _vllm_err_str, _re_ov.I)
                    _input_tokens = int(_input_m.group(1)) if _input_m else 60000
                    _ctx_limit    = int(_ctx_m.group(1))   if _ctx_m  else _cfg.CONTEXT_WINDOW
                    _safe_out     = max(1024, _ctx_limit - _input_tokens - 200)
                    _overflow_max_tokens = _safe_out
                    log.warning(
                        f"  [400-OVERFLOW] ctx={_ctx_limit} input={_input_tokens} "
                        f"→ retry with max_tokens={_safe_out}"
                    )
                    yield f": [400-overflow-retry] clamping max_tokens to {_safe_out}...\n\n"
                    full_text = ""
                    acc_tools = {}
                    finish_reason = None
                    full_reasoning = ""
                    continue
                _is_400 = "400" in _vllm_err_str and (
                    "arguments" in _vllm_err_str.lower() or
                    "invalid_parameter" in _vllm_err_str.lower() or
                    "JSON format" in _vllm_err_str or
                    _is_reasoning_400  # [DeepSeek-compat 2026-05-23]
                )
                if _is_400 and _vllm_retry_count < _vllm_max_retry:
                    _vllm_retry_count += 1
                    # [DeepSeek-compat 2026-05-23] reasoning_content 400 → 给每条 assistant
                    # 补上 reasoning_content="" (DeepSeek 接受空字符串).
                    # 这种错误不需要 sanitize/nuke, 单独走快路径.
                    if _is_reasoning_400:
                        log.warning(f"  [400-GUARD] retry {_vllm_retry_count}/3: patch missing reasoning_content (DeepSeek-compat)")
                        yield f": [400-retry-{_vllm_retry_count}] patching reasoning_content...\n\n"
                        for _msg in ctx.messages:
                            if _msg.get("role") == "assistant" and "reasoning_content" not in _msg:
                                _msg["reasoning_content"] = ""
                        full_text = ""
                        acc_tools = {}
                        finish_reason = None
                        full_reasoning = ""
                        continue  # 重试
                    # 其他 400: 逐级升级清理策略
                    if _vllm_retry_count == 1:
                        # 第1次重试：标准 sanitize（Pass 1-4）
                        log.warning(f"  [400-GUARD] retry {_vllm_retry_count}/3: sanitize history")
                        yield f": [400-retry-{_vllm_retry_count}] sanitizing history...\n\n"
                        _sanitize_history(ctx.messages)
                    elif _vllm_retry_count == 2:
                        # 第2次重试：强制重建 tool_result 配对
                        log.warning(f"  [400-GUARD] retry {_vllm_retry_count}/3: rebuilding pairs")
                        yield f": [400-retry-{_vllm_retry_count}] rebuilding tool pairs...\n\n"
                        # 只保留最近 8 条，强制重新 sanitize
                        sys_msgs = [m for m in ctx.messages if m.get("role") == "system"]
                        recent   = [m for m in ctx.messages if m.get("role") != "system"][-8:]
                        ctx.messages = sys_msgs + recent
                        _sanitize_history(ctx.messages)
                    else:
                        # 第3次重试：核武器 — 完全清除所有 tool_calls/tool results
                        log.warning(f"  [400-GUARD] retry {_vllm_retry_count}/3: NUCLEAR - strip all tools")
                        yield f": [400-retry-{_vllm_retry_count}] nuclear clean...\n\n"
                        ctx.messages = _nuke_tool_history(ctx.messages)
                    full_text = ""
                    acc_tools = {}
                    finish_reason = None
                    full_reasoning = ""  # [FIX] 400 重试时也清, 和 full_text/acc_tools 一致
                    continue  # 重试 while 循环
                else:
                    # 非 400 错误，或已超重试次数 → 重新抛出
                    if not _is_400:
                        raise
                    # 最终兜底：核清理后透出一条友好消息
                    log.error(f"  [400-GUARD] all {_vllm_max_retry} retries failed: {_vllm_err_str[:200]}")
                    yield sse_content(
                        "\n（对话记录出了点小问题，已经自动修复，请重新发一下你的问题。）",
                        model
                    )
                    ctx.messages = _nuke_tool_history(ctx.messages)
                    _vllm_success = True
                    break
            break  # 正常跳出 while（非 400 情况）
        # while end

    finally:
        out.full_text = full_text
        out.acc_tools = acc_tools
        out.finish_reason = finish_reason
        out.full_reasoning = full_reasoning
        out.stream_interrupted = stream_interrupted
        out.loop_broken = loop_broken
        out.iter_real_prompt = iter_real_prompt
        out.iter_real_completion = iter_real_completion
