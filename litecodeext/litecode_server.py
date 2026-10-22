#!/usr/bin/env python3
"""
litecode_server.py  -- OpenAI-compatible gateway backed by vLLM + LiteCode agent
Endpoints : POST /v1/chat/completions  GET /v1/models  GET /clawinfo  GET /health
Config    : config.json (same directory)
Start     : python3 litecode_server.py
"""

import asyncio
import json
import os
import re
import sys
import threading
import time
import uuid

if __name__ == "__main__":
    # 以脚本启动时本模块名是 __main__; gateway/_deps._srv()、dag_state 等再 import litecode_server
    # 会得到第二份副本: 顶层初始化重跑一遍, 模型热切换写进副本、主循环却读不到。登记别名, 两者同一对象。
    sys.modules.setdefault("litecode_server", sys.modules[__name__])
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, Request
import uvicorn
import logging

# ── Config (from lib/) ────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "core"))  # core 模块扁平导入
from lib import overhead_calib as _oc
from lib import context_pressure as _cp
from lib import tool_filter as _tf
from lib import project_root as _pr
from lib.config import (
    CFG, BASE, log, load_cfg,
    TOKEN, PORT, HOST, MODEL_ID, BACKEND_URL, API_KEY, BACKEND_TYPE,
    MAX_TOKENS, CONTEXT_WINDOW, ENABLE_THINKING, THINKING_BUDGET, CHARS_PER_TOKEN, est_tokens,
    MAX_ITER, MAX_ERROR_STREAK, TASK_TIMEOUT, SESSION_TTL,
    WORKSPACE, SKILLS_DIR, PROMPTS_DIR, TEMPLATE_DIR, LOGS_DIR, SESSIONS_DISK,
    SEARCH_CFG as _SEARCH_CFG, TRACE_CFG as _TRACE_CFG,
    HAS_ITRACE, make_tracer, trace_est_tokens as _trace_est_tokens,
    HAS_EXECUTOR, execute_shell_async,
    HAS_MEMORY, mem_get as _mem_get, MEM_SOFT as _MEM_SOFT, MEM_HARD as _MEM_HARD,
    auto_update_user as _auto_update_user, auto_update_tools as _auto_update_tools,
    register_skill as _register_skill, smart_auto_memory as _smart_auto_memory,
    HAS_DEEP_SEARCH, deep_search_tool,
    HAS_MEMORY_INDEX, memory_index_get,
)
from lib.sse import sse_content, sse_status, sse_stop, sse_done, sse_usage, sse_reasoning, sse_error, sse_meta, _mkid
from lib.agent_helpers import _auto_map_build_skeleton, _safe_json_args, _um_find_table_end
from lib.agent_state import ProjectMapState, TestTrackState, WebTestState, BatchWriteState, MiscTurnState, UsageCounters, TurnContext, StreamResult
from lib.stats import stats_load as _stats_load, stats_save as _stats_save, stats_add as _stats_add
from lib.guard_stats import GuardStats as _GuardStats
from lib.stats import questions_load as _questions_load, questions_append as _questions_append
from lib.transport import vllm_stream as _vllm_stream, parse_text_tool_calls as _parse_text_tool_calls
from lib.tool_args import parse_tool_args as _parse_tool_args
from lib import usage_ledger as _usage_ledger
from lib.turn_context import (KEY as _TURN_CTX_KEY, attach as _turn_attach,
                              materialize as _turn_materialize, strip as _turn_strip)
from lib.turn_sources import prepare as _turn_prepare, enabled as _turn_ctx_enabled
from lib.tool_prefetch import Prefetcher as _ToolPrefetcher
from lib.tool_result_eviction import strip_old_reasoning as _strip_old_reasoning
from lib.reasoning_limits import stall_limits as _reasoning_stall_limits, RepeatLineDetector as _RepeatLineDetector, salvage_note as _reasoning_salvage
from lib.session import (
    get_history as _get_history, save_history as _save_history,
    wal_append as _wal_append,
    get_artifacts as _get_artifacts, add_artifact as _add_artifact,
    get_session_lock as _get_session_lock, disk_load as _disk_load,
    list_sessions as _list_sessions, delete_session as _delete_session,
    set_interrupt, check_interrupt, clear_interrupt,
    purge_loop as _purge_loop, _sessions, _slock,
    _interrupt_flags, _interrupt_lock,
    MAX_HISTORY, RECENT_KEEP,
)

_SRV        = CFG["server"]
_MDL        = CFG["model"]
_AGT        = CFG.get("agent", {})
_PATHS      = CFG.get("paths", {})

# 写类/操作类工具名单已拆到 lib/agent/tool_names.py, 这里 re-export 保持原名可用
from lib.agent.tool_names import (  # noqa: E402,F401
    _WRITE_TOOL_NAMES, _WRITE_TOOL_NAMES_STR, _ACTION_TOOL_PREFIXES, _ACTION_TOOL_NAMES, _is_action_tool,
)



# ── Aliases (lib/config uses HAS_DEEP_SEARCH, server code uses HAS_SEARCH + _ds) ──
HAS_SEARCH = HAS_DEEP_SEARCH
# [v1.0] deep_search_tool 是类，需要实例化
_ds = None
if HAS_DEEP_SEARCH and deep_search_tool:
    try:
        _ds = deep_search_tool(
            workspace=WORKSPACE,
            backend_url=BACKEND_URL,
            api_key=API_KEY,
            model_id=MODEL_ID,
            search_sources=_SEARCH_CFG.get("domestic", []),
        )
    except Exception as _dse:
        import logging as _lg
        _lg.getLogger("openclaw").warning(f"[deep_search] init failed: {_dse}")
        HAS_SEARCH = False

# ── Iteration Trace ──────────────────────────────────────────
_TRACE_CFG = CFG.get("iteration_trace", {})
try:
    from itrace import make_tracer, _estimate_tokens as _trace_est_tokens
    HAS_ITRACE = True
except ImportError:
    HAS_ITRACE = False
    def make_tracer(*a, **kw):
        class _Null:
            enabled = False
            def __getattr__(self, _): return lambda *a, **kw: None
        return _Null()
    def _trace_est_tokens(msgs): return 0

try:
    from core.telemetry import new_trace as _tlm_new_trace, set_trace_id as _tlm_set_trace_id
except Exception:
    def _tlm_new_trace() -> str: return ""
    def _tlm_set_trace_id(tid: str) -> None: return None


# ── Skills (from lib/) ─────────────────────────────────────
from lib.skills import (
    _SKILL_INDEX, _CN_SKILL_MAP, _STOP, _build_skill_index,
    load_skills_index, _load_superpowers_core, auto_load_skills, load_tools_md,
)
# ── history / tool helpers (moved to core/history_utils.py) ──────────────
from history_utils import (
    _clear_old_tool_results, _count_orphan_user_tail,
    _extract_first_user_summary, _extract_last_completed_turn,
    _auto_rotate_session, _sanitize_history, _nuke_tool_history,
    _fmt_args, _enhance_tool_error, _strip_tool_call_literals,
)
# ── Tool definitions (from lib/) ───────────────────────────
from lib.tools.defs import TOOL_DEFS
# ── Subagent (from lib/) ───────────────────────────────────
from lib.agent.subagent import (
    _SUBAGENT_CONFIGS as SUBAGENT_CONFIGS,
    _run_subagent, _run_subagent_safe,
)
# ── System prompt (from lib/) ──────────────────────────────
from lib.prompt import (
    _probe_env, _get_env_probe, _load_prompt_md, _build_search_sources_text,
    _load_external_prompt, _base_system_prompt, _load_session_context,
    build_request_system_prompt, _detect_auto_delegation, _network_env_hint,
)
# ── Tool dispatch (extracted to core/tool_dispatch.py) ───
from tool_dispatch import (
    execute_tool, _bg_procs, _bg_lock, _read_cache,
    _unregister_bg, _do_web_fetch, _save_checkpoint, _make_diff,
)


# 以下函数已拆到 lib/agent/ (拆分大文件, 逐字搬迁), 这里 re-export 保持原名可用
from lib.agent.turn_phases import _turn_finalize, _turn_setup_context, _turn_setup_autorotate, _turn_setup_orphan_prefix  # noqa: E402,F401
from lib.agent.loop_budget import _loop_context_window, _live_model, _log_anchor, _loop_mid_compress, _loop_context_budget  # noqa: E402,F401
from lib.agent.llm_call import _stream_llm_call  # noqa: E402,F401
from lib.agent.tool_guards import _guard_tool_call, _tool_update_map  # noqa: E402,F401
from lib.agent.spawn_dispatch import _dispatch_spawn_agent  # noqa: E402,F401
from lib.agent.after_tools import _after_tool_batch  # noqa: E402,F401
from lib.agent.text_reply import _salvage_empty_reply, TextReplyState, _handle_text_reply  # noqa: E402,F401
from lib.agent.turn_persist import _turn_persist  # noqa: E402,F401
from lib.agent.steer import apply_steers as _apply_steers
from lib.agent.tool_exec import ToolRunState, _run_tool_calls, _write_call_confirmed_success  # noqa: E402,F401
from lib.agent.turn_errors import _turn_error_message  # noqa: E402,F401
from lib.agent import failure_loop as _fl  # noqa: E402  失败经验记录 (召回走 lib/turn_sources)







async def agent_stream(
    user_message: str,
    session_id: Optional[str],
    model: str = MODEL_ID,
    user_content: Any = None,   # [v1.4] 多模态: 若传 list, 直接作为 user.content
    strategist: bool = False,   # [2026-09-03] 权谋/多方博弈推演模式 (前端 chip)
    disabled_tools: frozenset = frozenset(),   # [2026-10-13] 本次请求禁用的工具, 见 lib/tool_filter.py
    skills: tuple = (),   # [2026-10-22] 用户本轮强制使用的技能名 (lib/skill_turn.py)
    usage_source: str = "",     # [usage-ledger] 账本来源 (web/timer/dag...), 空 = 按会话 id 前缀推
) -> AsyncGenerator[str, None]:
    # 同 session 的并发请求串行化：防止两个 client 同时跑 agent loop 导致 LLM 并行/文件竞争
    _lock = _get_session_lock(session_id) if session_id else None
    if _lock:
        await _lock.acquire()
    # ── AGENT_STREAM_REFACTOR M5a: 防 UnboundLocalError, 必须在 try 之前构造 ──
    # finally 依赖 _ctx.new_msgs / _ctx.base_history / _ctx.sid_tag, 若构造放 try 内
    # 且抛异常, finally 引用 _ctx 会 UnboundLocalError (老 bug 换个形式复活)
    _ctx = TurnContext(sid_tag=session_id[:8] if session_id else "anon", session_id=session_id,
                       disabled_tools=frozenset(disabled_tools or ()), forced_skills=tuple(skills or ()))
    # [usage-ledger 2026-10-22] 本轮记账作用域: turn_id 就是首帧 msg_id; 技能边跑边记进 _ctx.turn_skills
    _turn_id = uuid.uuid4().hex[:12]
    _ledger_tok = _usage_ledger.begin_turn(session_id, _turn_id, source=usage_source, skills=_ctx.turn_skills)
    try:
        # ── 防 UnboundLocalError: finally 依赖的变量必须最先赋值 ──
        # 注意: 不用 `x: list = []` 注解赋值，Python 3.11 在某些异常路径下会误判为未绑定
        _usage = UsageCounters()  # [USAGE] AGENT_STREAM_REFACTOR M4 -- t0 在此刻取时间戳
        # AGENT_STREAM_REFACTOR M6b: SETUP 段前半 (trace/中断flag/委派检测/SESSION-INIT/
        # 历史加载清洗/sys_prompt/memory_index/技能trace) 抽为模块级纯函数, 见 _turn_setup_context
        (_effective_user_content, _force_writer_mode, sys_prompt,
         _mlc_done_this_turn, _guard_stats) = _turn_setup_context(
            user_message, user_content, strategist, _ctx,
        )

        # [orphan-user-detect 2026-05-23] 检测连续 orphan user (上 N 轮空回后 user 又追发 N 条):
        # 1) prepend FORCE-ACTION prefix
        # 2) 删除 history 末尾的 orphan user msg (它们都没用, 否则混淆 vLLM)
        # 3) 如果 history > 30 + orphan, 大裁: 只保留 sys + 最近 1 个 assistant 完整对 + 新 user
        # 4) [auto-session-rotate 2026-05-23] orphan_n >= 3 → 自动 fork 到新 session +
        #    yield SSE event 通知前端切换, 后续 persist 落到新 sid
        # 这是 L4 模式的工程修复 (vLLM tools 在长 history + orphan user 时持续空回)
        # AGENT_STREAM_REFACTOR M6b: rotate 分支(含 yield) → _turn_setup_autorotate,
        # 裁剪+FORCE-ACTION prefix 分支(无 yield) → _turn_setup_orphan_prefix
        _orphan_n = _count_orphan_user_tail(_ctx.base_history)
        async for _setup_sf in _turn_setup_autorotate(_ctx, _orphan_n, model):
            yield _setup_sf
        _effective_user_content = _turn_setup_orphan_prefix(_ctx, _orphan_n, _effective_user_content)

        _ctx.messages = [{"role": "system", "content": sys_prompt}] + _ctx.base_history + [
            {"role": "user", "content": _effective_user_content}
        ]
        # [display-fix 2026-07] new_msgs 用原始 user_message (不含 FORCE-ACTION prefix),
        # 否则持久化后用户在聊天界面看到系统提示文本污染用户消息.
        _ctx.new_msgs = [{"role": "user", "content": user_message}]
        if _ctx.turn_context:   # 原话不变 (界面显示用), 上下文另存一个字段, 下一轮拼回保缓存
            _ctx.new_msgs[0][_TURN_CTX_KEY] = _ctx.turn_context
        _wal_flushed = 0  # [WAL 2026-09-04] new_msgs 已增量落盘到的下标, 每轮迭代顶部 flush 增量

        # 错误连续计数器
        error_streak = 0

        # ── Iteration Trace: 开始 ──
        _ctx.tracer.start(
            user_message=user_message,
            sys_prompt=sys_prompt,
            messages_count=len(_ctx.base_history),
            history_tokens=_trace_est_tokens(_ctx.base_history),
        )

        # [D4] 首 chunk 携带 msg_id, 供前端绑定当前 assistant 消息
        _new_msg_id = _turn_id
        yield f"data: {json.dumps({'choices':[{'delta':{'msg_id': _new_msg_id}}]})}\n\n"

        # [stream-feedback 2026-05-22] 立即发占位 status, 让前端 1s 内有反馈
        # 否则 vllm cold start prefix-cache miss + 30KB system prompt + 27 tools schema
        # prefill 要 4-5s, 用户感觉"等了半天没动静".
        try:
            yield sse_status("task_exec", {"status": "executing",
                                            "detail": "推理中…",
                                            "key": "preparing"}, model)
        except Exception:
            pass

        # ── 动态超时: 写作/长内容任务自动延长 ──
        _is_long_task = any(kw in user_message for kw in [
            "小说", "写作", "万字", "长文", "报告", "论文", "章节",
            "novel", "write", "chapter", "report",
        ])
        _effective_timeout = TASK_TIMEOUT * 6 if _is_long_task else TASK_TIMEOUT
        # [FIX 2026-08-26] config.json max_iterations=9999 等于没有上限 (SWE-bench 实测
        # 空转到 iter=108 没被 max_iter 拦住). 真实长任务观测到的峰值是 53 次工具调用,
        # 500 给了充足余量 (~10x), 同时把最坏情况的"零上限空转"兜到有限轮数.
        # 不动 config.json max_iterations 本身 (9999 仍是显式配置上限), 加一层更早触发的软上限.
        _iter_soft_cap = int(_AGT.get("max_iter_soft_cap", 500))
        _effective_max_iter = min(MAX_ITER, _iter_soft_cap) if _iter_soft_cap > 0 else MAX_ITER
        _pm_state = ProjectMapState()     # [PROJECT_MAP] AGENT_STREAM_REFACTOR M1
        _test_state = TestTrackState()    # [TEST] AGENT_STREAM_REFACTOR M2
        _webtest_state = WebTestState()   # [WEB-TEST] AGENT_STREAM_REFACTOR M2
        _batch_state = BatchWriteState()  # [BATCH] AGENT_STREAM_REFACTOR M3
        _misc_state = MiscTurnState()     # [FIX]/[VALIDATE] AGENT_STREAM_REFACTOR M3

        # ── [TOOL-LOOP] 主循环 tool 重复检测 (2026-05 加, S1-D 抽到 plugins/tools/guard.py)
        from plugins.tools.guard import ToolLoopGuard as _ToolLoopGuard
        _tool_loop_guard = _ToolLoopGuard()
        # [FIX 2026-08-26] 累计拦截达阈值 → 硬熔断本轮 (guard 只拦不导致过空转到 iter=108)
        _tool_loop_break_threshold = int(_AGT.get("tool_loop_break_threshold", 8))

        try:
            _consecutive_empty = 0  # [v1.0] 连续空回复计数器, 跨迭代累计
            _last_reasoning_salvage = ""  # [FIX 2026-09-04] 空回复流里最后一段非空 reasoning, 触顶放弃时取回当正文
            _reasoning_stall_count = 0  # [v4 2026-07] 跨迭代推理超时计数 (用于升级约束)
            # [fix 2026-05-23] iter=0 一开始就遇到 _interrupt_flags 残留时
            # (用户在前一轮按了 interrupt, flag 还在), 中断分支 (行 ~807) 引用
            # _accumulated_text 但它还没在循环体里初始化 → UnboundLocalError.
            # 在 try 外层先 = "" 防御.
            _accumulated_text = ""
            for _iteration in range(_effective_max_iter):
                # [WAL 2026-09-04] 迭代顶部把上一轮新增消息增量落盘 (crash-safe)。
                # 每个 continue 都会回到这里 → 覆盖所有多轮路径; break 路径由 finally 的
                # save_history 兜底。进程中途崩最多丢当前这一轮迭代, 不再整轮 reasoning+调用全丢。
                if _ctx.session_id and len(_ctx.new_msgs) > _wal_flushed:
                    _wal_append(_ctx.session_id, _ctx.new_msgs[_wal_flushed:])
                    _wal_flushed = len(_ctx.new_msgs)
                if _iteration == 0:
                    _apply_steers(_ctx)   # [steer] 上一轮收尾窗口里才到的插话, 并入这一轮
                with _interrupt_lock:
                    _interrupted_here = _ctx.session_id in _interrupt_flags
                    if _interrupted_here:
                        _interrupt_flags.discard(_ctx.session_id)
                if _interrupted_here:
                    log.warning(f"  \033[31m[INTERRUPT@iter-start:{_ctx.sid_tag}]\033[0m "
                                f"session {_ctx.session_id} 用户中断于 iter={_iteration} "
                                f"(累计{_usage.total_iterations}轮, {len(_ctx.new_msgs)}条新消息)")
                    _interrupt_text = "\n\n[任务已被用户中断]"
                    _accumulated_text += _interrupt_text
                    try:
                        yield sse_content(_interrupt_text, model)
                    except GeneratorExit:
                        pass
                    if _accumulated_text.strip():
                        _ctx.new_msgs.append({"role": "assistant", "content": _accumulated_text.strip()})
                    _ctx.tracer.iteration_end("INTERRUPTED", f"user interrupt at iter {_iteration}")
                    break
                # [FIX] docker logs -f 友好: iter 开始时打印关键状态 (模型/消息数/空回复连击)
                log.info(
                    f"  \033[36m[{_ctx.sid_tag}]\033[0m iter={_iteration}/{_effective_max_iter} "
                    f"model={MODEL_ID} backend={BACKEND_URL[:40]} msgs={len(_ctx.messages)} "
                    f"empty_streak={_consecutive_empty} new_msgs={len(_ctx.new_msgs)}"
                )
                # ── Iteration Trace: 迭代开始 ──
                _ctx.tracer.iteration_begin(_iteration, _ctx.messages)

                # ── [SLIDING-WINDOW] 窗口缩紧: 每 5 轮触发, msgs>20 就 trim ──
                _fixed_overhead = _loop_context_window(_ctx, _usage, _iteration, sys_prompt)
                _usage.fixed_overhead = _fixed_overhead   # USAGE-ANCHOR 比值要把固定开销算进去
                # ── [MID-LOOP-COMPRESS] 按 token 水位 + 每15轮 触发 memory 压缩 ──
                _mlc_done_this_turn = _loop_mid_compress(_ctx, _iteration, locals().get("_mlc_done_this_turn"))
                # ── 超时保护 ──
                _usage.elapsed = time.time() - _usage.t0
                if _usage.elapsed > _effective_timeout:
                    _ctx.tracer.iteration_end("TIMEOUT", f"elapsed={int(_usage.elapsed)}s")
                    timeout_msg = (
                        f"\n\n[timeout] 任务已运行 {int(_usage.elapsed)}s，达到时间上限 ({int(_effective_timeout)}s)。"
                        f"请总结当前进度和剩余步骤。"
                    )
                    _fl.mark(_ctx, "timeout", detail=f"运行 {int(_usage.elapsed)}s 超过上限 {int(_effective_timeout)}s")
                    yield sse_content(timeout_msg, model)
                    # 给模型一次最后总结的机会
                    _ctx.messages.append({"role": "user", "content":
                        "[SYSTEM: 时间已到限制，请立即输出当前进度总结和剩余步骤建议，然后结束。]"})
                    final_text = ""
                    async for chunk in _vllm_stream(_ctx.messages, []):  # 不给工具，强制纯文本
                        choices = chunk.get("choices", [])
                        if choices:
                            ct = choices[0].get("delta", {}).get("content") or ""
                            if ct:
                                final_text += ct
                                yield sse_content(ct, model)
                    if final_text:
                        _ctx.new_msgs.append({"role": "assistant", "content": final_text})
                    break

                full_text      = ""
                acc_tools: dict = {}
                finish_reason  = None
                _last_yield_ts = time.time()  # [OPT] P0-C: 心跳计时器
                _stream_interrupted = False  # [INTERRUPT] vLLM流中断标记

                # [FIX] 每轮前统一调用 _sanitize_history 清理非法 arguments + dangling tool_calls
                # 覆盖 Qwen3.5 本地 vLLM 和 MiniMax 2.5 两种后端的严格校验
                # [2026-10-19] 原地清理会改写与 new_msgs 共享的消息 (落盘证据丢 tool_calls/原始 arguments),
                # 改为只清理模型输入副本, 见 loop_budget.sanitize_model_copy。
                from lib.agent.loop_budget import sanitize_model_copy as _sanitize_model_copy
                _ctx.messages = _sanitize_model_copy(_ctx.messages)

                # ── [CONTEXT-BUDGET] LLM 调用前检查 token 预算，超限则裁剪老消息 ──
                _loop_context_budget(_ctx, _fixed_overhead)
                # ── [400-GUARD→M8] vLLM 调用+流解析 (迁至 _stream_llm_call, 见模块级函数) ──
                # [M8-polish] dict → StreamResult dataclass (评审意见落地: 属性访问,
                # 打错字段名 Pyright 静态可抓)。未显式传的 4 项默认值即原段内 init 常量;
                # full_reasoning 保持 "" 种子 (首轮读局部会 Unbound, 门4 实抓过)。
                _sll_out = StreamResult(full_text=full_text, acc_tools=acc_tools,
                                        finish_reason=finish_reason,
                                        stream_interrupted=_stream_interrupted)
                async for _sll_chunk in _stream_llm_call(_ctx, _usage, model,
                        _last_yield_ts, _reasoning_stall_count, _sll_out):
                    yield _sll_chunk
                full_text = _sll_out.full_text
                acc_tools = _sll_out.acc_tools
                finish_reason = _sll_out.finish_reason
                _full_reasoning = _sll_out.full_reasoning
                _stream_interrupted = _sll_out.stream_interrupted
                _loop_broken = _sll_out.loop_broken
                _iter_real_prompt = _sll_out.iter_real_prompt
                _iter_real_completion = _sll_out.iter_real_completion
                # ── [STREAM-INTERRUPT] 如果 vLLM 流中被中断，保存已有内容并退出 ──
                if _stream_interrupted:
                    _interrupt_text = "\n\n[任务已被用户中断]"
                    try:
                        yield sse_content(_interrupt_text, model)
                    except GeneratorExit:
                        pass
                    if full_text:
                        full_text += _interrupt_text
                        _ctx.new_msgs.append({"role": "assistant", "content": full_text})
                        _ctx.messages.append({"role": "assistant", "content": full_text})
                    _ctx.tracer.iteration_end("INTERRUPTED", "stream interrupt")
                    break

                # [v1.4] 推理死循环中断后, 持久化当轮 content 并注入约束消息给下一轮
                if _loop_broken:
                    _reasoning_stall_count += 1
                    _ctx.no_think_next = True   # 下一轮关思考直接动手 (见 llm_call)
                    if full_text:
                        _ctx.new_msgs.append({"role": "assistant", "content": full_text})
                        _ctx.messages.append({"role": "assistant", "content": full_text})
                    if _reasoning_stall_count >= 3:
                        # 第3次以上: 注入强制指令, 要求本轮必须输出内容
                        _constraint = (
                            f"[SYSTEM-EMERGENCY] 你已连续 {_reasoning_stall_count} 次推理超时被强制中断. "
                            "本轮严禁再进入长推理. 要求: 立即输出一句话说明你正在做什么, "
                            "然后调用最简单可行的工具 (execute_shell / write_file). "
                            "若仍超时, 本任务将直接终止."
                        )
                    elif _reasoning_stall_count == 2:
                        _constraint = (
                            "[SYSTEM] 你已连续 2 次推理超时. 下次再超时将强制终止任务. "
                            "要求: 不要在 reasoning 里反复评估方案, 直接选第一个可行方案调用工具."
                        )
                    else:
                        _constraint = (
                            "[SYSTEM] 上一轮思考过长被中断, 没有产出工具调用或正文. "
                            "现在要求: 1) 直接调用一个合适的工具动手, 或用 2-3 句话回答用户; "
                            "2) 大段产出 (代码 / 页面 / 文档) 直接写进工具参数, 不要先在思考里打完整草稿; "
                            "3) 不要在思考里反复评估同一个方案."
                        )
                    _constraint += _reasoning_salvage(_full_reasoning)
                    _ctx.messages.append({"role": "user", "content": _constraint})
                    _ctx.tracer.iteration_end("LOOP-BREAK", f"reasoning loop cut (stall #{_reasoning_stall_count})")
                    # 不 break agent 主循环 — 让下一轮带约束重试, 但计数继续防止无限
                    continue

                # Qwen text-format fallback
                if not acc_tools and full_text:
                    text_calls = _parse_text_tool_calls(full_text)
                    if text_calls:
                        for i, tc in enumerate(text_calls):
                            acc_tools[i] = tc
                        finish_reason = "tool_calls"

                # 过滤掉 name 为空的工具调用（流被中断时可能出现）
                acc_tools = {k: v for k, v in acc_tools.items() if v.get("name")}

                # ── 首轮无文字直接调工具 → 强制重跑，要求先说明意图 ──
                # 所有工具调用（不限 spawn_agent）都应先告知用户意图
                # 例外：load_skill、list_skills、save_memory 等轻量工具不需要打招呼
                # ── [v1.0] 工具调用前置文字检查（仅日志，不阻断执行）──
                # 之前的 enforce 机制会丢弃工具调用并强制重跑，导致 LLM 只输出文字不执行。
                # 现在改为：始终执行工具，只在日志中记录缺少前置说明。
                _SILENT_OK_TOOLS = {"load_skill", "list_skills", "save_memory", "update_profile",
                                    "read_file", "get_tree", "find_files", "execute_shell",
                                    "write_file", "patch_file", "search_code", "get_image"}
                tool_names_set = {v.get("name") for v in acc_tools.values()}
                has_heavy_tool = bool(tool_names_set - _SILENT_OK_TOOLS)
                text_too_short = len((full_text or "").strip()) < 10
                # [FIX v2] pre-tool 自动播报收窄: 只对"用户关心的重型动作"播报, 免得
                # 每个 curl/ls 都噪声。重型 = 写文件 / 子代理 / 网络爬取类。
                _ANN_TOOLS = {"write_file", "patch_file", "spawn_agent",
                              "browser_read", "browser_search", "vision_ocr",
                              "web_search", "pdf_to_md"}
                _ann_hits = [acc_tools[_k] for _k in sorted(acc_tools.keys())
                             if acc_tools[_k].get("name") in _ANN_TOOLS]
                if _ann_hits and text_too_short:
                    _ann_parts = []
                    for _tc in _ann_hits:
                        _nm = _tc.get("name", "?")
                        try:
                            _a = json.loads(_tc.get("arguments") or "{}")
                        except Exception:
                            _a = {}
                        _key_arg = (
                            _a.get("filepath") or _a.get("path")
                            or _a.get("url") or _a.get("query") or _a.get("task") or ""
                        )
                        if isinstance(_key_arg, str) and _key_arg:
                            _ann_parts.append(f"{_nm}({_key_arg[:40]})")
                        else:
                            _ann_parts.append(_nm)
                    _announce = f"▸ 正在: {', '.join(_ann_parts[:4])}\n"
                    try:
                        yield sse_content(_announce, model)
                    except Exception:
                        pass
                    log.info(f"  \033[35m[{_ctx.sid_tag}]\033[0m pre-tool 自动播报: {_announce.strip()}")
                elif acc_tools and has_heavy_tool:
                    log.debug(
                        f"  [{_ctx.sid_tag}] heavy tools: {', '.join(sorted(tool_names_set))}"
                    )

                # ── [TOKEN-STATS] 记录本轮 token 消耗 ──
                _iter_prompt = sum(est_tokens(m.get("content",""))+est_tokens(m.get("tool_calls","")) for m in _ctx.messages)
                # [FIX 2026-08-31] 原先不含 reasoning。思考模型的 reasoning 是计费输出 token,
                # 漏算会让账单显著高于统计值 (自建不计费无感, 切中转付费模型时才暴露)。
                _iter_completion = (est_tokens(full_text) + est_tokens(_full_reasoning or "")
                                    + sum(est_tokens(tc.get("arguments","")) for tc in acc_tools.values()))
                # [TOKEN-STATS 真值 2026-09-05] 本轮若拿到上游真实 usage 就用真值记账 (账单口径与
                # 上游一致, 消除思考模型下估算偏差); 没拿到则退回估算。
                _p_stat = _iter_real_prompt if _iter_real_prompt > 0 else _iter_prompt
                _c_stat = _iter_real_completion if _iter_real_completion > 0 else _iter_completion
                if _p_stat > 0 or _c_stat > 0:
                    _stats_add(_ctx.session_id or "unknown", _p_stat, _c_stat)
                    _usage.total_prompt_tokens += _p_stat
                    if _iter_real_completion <= 0:
                        # 真值分支: total_completion_tokens 已在 usage 锚点处累加过真值,
                        # 这里不再叠加估算 (否则真值+估算双记, 是原先的一个隐性 bug)。
                        _usage.total_completion_tokens += _iter_completion
                _usage.total_iterations += 1

                if acc_tools and finish_reason in ("tool_calls", None):

                    tool_call_list = [
                        {
                            "id":   acc_tools[i]["id"],
                            "type": "function",
                            "function": {
                                "name":      acc_tools[i]["name"],
                                "arguments": _safe_json_args(acc_tools[i]["arguments"])
                            }
                        }
                        for i in sorted(acc_tools.keys())
                    ]

                    # ── Trace: LLM 输出记录 ──
                    _ctx.tracer.llm_output(
                        text=full_text,
                        tool_calls=tool_call_list,
                        tokens_est_out=est_tokens(full_text) if full_text else 0,
                    )

                    asst_msg = {
                        "role":       "assistant",
                        "content":    full_text or None,
                        "tool_calls": tool_call_list
                    }
                    # [DeepSeek-compat 2026-05-23 / 2026-05-25 强制化]
                    # DeepSeek V4 系列 thinking mode: 有 tool_calls 时 reasoning_content
                    # **必须**字段存在 (空字符串也行, 字段不在就 400).
                    # 官方契约 (api-docs.deepseek.com/guides/thinking_mode):
                    #   "the intermediate assistant's reasoning_content must participate
                    #    in the context concatenation and must be passed back to the API"
                    # 老条件 `if _full_reasoning and .strip()` 在 stream 早 break / vLLM 字段
                    # 重命名导致 _full_reasoning 空时不 attach → 下轮 400. 改成:
                    #   1) deepseek-v4 + 有 tool_calls → 强制 attach (空就空字符串占位)
                    #   2) 其它情况 → 有真 reasoning 才 attach (Qwen/glm/anthropic 兼容)
                    # 当前模型 id 读运行时真源 _LIVE (原 `'MODEL_ID' in dir()` 在函数内只看局部变量, 恒假, 实际一直走这里)
                    from lib.config import _LIVE as _live_cfg_dict
                    _model_id_low = str(_live_cfg_dict.get("model_id", "")).lower()
                    _is_deepseek_v4 = ("deepseek-v4" in _model_id_low or
                                       "deepseek-chat" in _model_id_low or
                                       "deepseek-flash" in _model_id_low)
                    if _full_reasoning and _full_reasoning.strip():
                        asst_msg["reasoning_content"] = _full_reasoning
                    elif _is_deepseek_v4 and tool_call_list:
                        asst_msg["reasoning_content"] = ""   # 占位, DeepSeek V4 接受空
                    _ctx.messages.append(asst_msg)
                    _ctx.new_msgs.append(asst_msg)

                    tool_results = []
                    # [EXEC-GATE] 本轮(本次迭代)是否已发生过*成功*的写类工具调用, 按执行结果算
                    # (不能像旧版那样在循环开始前只看工具名 —— 判不出这次写入是否成功时一律
                    # 按"没成功"处理, 见下面循环体里 is_err 判定), 供 task_done 证据提示和
                    # [READONLY]/[EXEC-GATE] 收尾闸共用。task_done 可能和写工具同批出现且顺序
                    # 不定, 所以证据提示推迟到本批全部工具执行完之后再统一判定 (见循环尾部)。
                    _iter_write_success = False
                    _task_done_result_indices: list = []
                    # ── 子智能体 SSE 透传回调 ──
                    # [v1.0] 改用 asyncio.Queue, 支持实时 drain + 中断响应
                    _subagent_sse_queue: asyncio.Queue = asyncio.Queue()

                    async def _sse_emit_to_buffer(chunk_str: str):
                        await _subagent_sse_queue.put(chunk_str)

                    # 批次开头连续的只读调用提前并发跑 (lib/tool_prefetch.py), 下面照旧逐个过守卫
                    _prefetch = _ToolPrefetcher.start(
                        tool_call_list, _parse_tool_args,
                        lambda _n, _a: execute_tool(_n, _a, sid=_ctx.session_id),
                        enabled=(CFG.get("agent") or {}).get("parallel_readonly_tools", True))
                    if len(_prefetch):
                        log.info(f"  [PREFETCH] {len(_prefetch)} 个只读工具并发执行")
                    # 逐个执行本批工具调用, 已提取到 lib/agent/tool_exec.py
                    _trs = ToolRunState(_effective_timeout, error_streak, _iter_write_success)
                    async for _ev in _run_tool_calls(_ctx, _trs, tool_call_list, tool_results,
                                                     _batch_state, _misc_state, _pm_state, _test_state, _prefetch,
                                                     _sse_emit_to_buffer, _subagent_sse_queue, _task_done_result_indices,
                                                     _tool_loop_guard, _usage, _force_writer_mode, model):
                        yield _ev
                    _effective_timeout, error_streak, _iter_write_success = (
                        _trs.effective_timeout, _trs.error_streak, _trs.iter_write_success)

                    _prefetch.cancel_rest()   # 被守卫拦下没取走的预取任务
                    # ── [EXEC-GATE] task_done 证据提示: 本批(含同批写工具)执行完后统一判定 ──
                    if (_task_done_result_indices
                            and _batch_state.exec_gate_task_done_needs_warning(_iter_write_success)):
                        for _tdi in _task_done_result_indices:
                            tool_results[_tdi]["content"] += (
                                "\n\n[SYSTEM-EXEC-GATE] 注意: 本回合尚无任何文件写入。"
                                "如果这一步本应产生代码改动, 它可能并未真正完成; "
                                "如果这一步本来就不需要改文件, 忽略这条。"
                            )

                    _ctx.messages.extend(tool_results)
                    _ctx.new_msgs.extend(tool_results)
                    # 先补齐所有 tool 响应，再附最新桌面图，保持 OpenAI 工具消息链完整。
                    from lib.desktop_observation import append_latest_observation
                    if append_latest_observation(_ctx.messages, tool_call_list, tool_results, WORKSPACE):
                        log.info(f'  [DESKTOP-VISION:{_ctx.sid_tag}] 最新截图已回流下一轮模型输入')

                    # ── [TOOL-LOOP-BREAK] 累计拦截达阈值 → 终止本轮, 走正常收尾路径 ──
                    # [FIX 2026-08-26] SWE-bench 实测: guard 只拦不导致 iter=108 空转
                    # (拦截提示模型认不出/换个数字又试). block_count 是整个 turn 累计值
                    # (不是本 iter), 阈值到了直接 break 出主循环, 不硬跳 finally.
                    if _tool_loop_guard.block_count >= _tool_loop_break_threshold:
                        log.warning(
                            f"  \033[31m[TOOL-LOOP-BREAK:{_ctx.sid_tag}]\033[0m "
                            f"iter={_iteration} 累计拦截 {_tool_loop_guard.block_count} 次, 终止本轮"
                        )
                        _fl.mark(_ctx, "tool_loop_break")
                        _tlb_msg = (
                            "\n\n（同一个操作反复重试了好多次都没有进展，已经先停下来了，"
                            "换个思路，或者告诉我更多信息再继续吧。）\n"
                        )
                        try:
                            yield sse_content(_tlb_msg, model)
                        except GeneratorExit:
                            pass
                        _ctx.new_msgs.append({"role": "assistant", "content": _tlb_msg.strip()})
                        _ctx.tracer.iteration_end(
                            "TOOL-LOOP-BREAK",
                            f"blocked {_tool_loop_guard.block_count} times at iter {_iteration}",
                        )
                        break

                    # ── [POST-TOOL-INTERRUPT] 工具执行完毕后立即检查中断标记 ──
                    with _interrupt_lock:
                        _pti = _ctx.session_id in _interrupt_flags
                        if _pti:
                            _interrupt_flags.discard(_ctx.session_id)
                    if _pti:
                        log.warning(
                            f"  \033[31m[INTERRUPT@post-tool:{_ctx.sid_tag}]\033[0m "
                            f"iter={_iteration} 工具执行完立刻中断, "
                            f"已完成 {len(tool_results)} 个工具结果"
                        )
                        _interrupt_text = "\n\n[任务已被用户中断 (工具执行后)]"
                        try:
                            yield sse_content(_interrupt_text, model)
                        except GeneratorExit:
                            pass
                        # [FIX] 让中断的摘要也入库, 不然前端只看得到"中断了"但不知道中断前做到哪
                        _ctx.new_msgs.append({"role": "assistant", "content": _interrupt_text.strip()})
                        _ctx.tracer.iteration_end("INTERRUPTED", f"post-tool interrupt at iter {_iteration}")
                        break

                    # 工具批次之后的写入追踪与各类提醒/闸门, 已提取到 lib/agent/after_tools.py
                    _after_tool_batch(_ctx, _guard_stats, _iteration, _iter_write_success, _misc_state, _pm_state,
                                      _test_state, _webtest_state, _batch_state, tool_call_list, tool_results)

                    # [steer 2026-10-21] 用户运行中插话: 本批工具结果之后、下次调模型之前接进上下文
                    _steered = _apply_steers(_ctx)
                    if _steered:
                        log.info(f"  [STEER:{_ctx.sid_tag}] 接入 {len(_steered)} 条插话: {_steered[0][:60]}")
                        yield sse_status("task_exec", {"status": "done",
                                                       "detail": f"📝 已收到插话: {_steered[0][:60]}"}, model)


                    # 重置本轮标志
                    _test_state.iter_wrote_src = False
                    _test_state.iter_wrote_test = False

                    # [v1.0] 任何工具调用 = 模型有产出 → 清零空回复连击计数
                    _consecutive_empty = 0

                    _ctx.tracer.iteration_end("ITER_TOOL_CALL", f"{len(tool_results)} results")
                    continue

                else:
                    # 本轮没调工具: 正文收尾 / 空回复治理, 已提取到 lib/agent/text_reply.py
                    _tr = TextReplyState(_consecutive_empty, _last_reasoning_salvage)
                    async for _ev in _handle_text_reply(_ctx, _batch_state, _tr, full_text, _full_reasoning,
                                                        _iteration, _effective_max_iter, model):
                        yield _ev
                    _consecutive_empty, _last_reasoning_salvage = _tr.consecutive_empty, _tr.last_reasoning_salvage
                    if _tr.action == "break":
                        # [steer] 本该收尾时还有插话 → 不结束回合, 接着按插话做
                        _steered = _apply_steers(_ctx)
                        if not _steered:
                            break
                        log.info(f"  [STEER:{_ctx.sid_tag}] 收尾前接入 {len(_steered)} 条插话, 回合继续")
                        yield sse_status("task_exec", {"status": "done",
                                                       "detail": f"📝 已收到插话: {_steered[0][:60]}"}, model)
                    continue
            else:   # for 正常耗尽 (没有任何 break) = 迭代上限
                _fl.mark(_ctx, "max_iter", detail=f"{_effective_max_iter} 轮迭代用尽")

            # [FIX] for 循环正常耗尽时不做任何处理 — MAX_ITER 由 config.json 控制
            # (当前 999), 已足够大；真正耗尽说明任务已完成或模型主动 break；
            # 不需要兜底总结

        except Exception as e:
            _ctx.tracer.finish(error=str(e))
            _ctx.tracer = make_tracer({})  # 替换为空 tracer 防止下面重复 finish
            # [FIX] 以前只 yield 给前端不入 new_msgs → finally 里 save 只剩开头 user → 日志 saved 1 msgs
            # 现在把错误作为 assistant 消息落库, 前端刷新 session 能看到上次失败原因
            # [FIX 2026-08-31] 原先把异常原文直接当内容发给用户, 微信里出现过
            #   [ERROR] vLLM returned 400: b'{"error":{"message":"This model's maximum
            #   context length is 204800 tokens...
            # 这种东西对用户毫无意义, 还暴露内部实现。原文只进日志, 用户看到人话。
            _raw_err = str(e)
            log.exception(f"  [{_ctx.sid_tag}] 回合异常: {_raw_err[:500]}")
            _err_msg = _turn_error_message(_raw_err)
            _fl.mark(_ctx, "turn_exception", detail=f"{type(e).__name__}: {_raw_err[:300]}")
            try:
                yield sse_content(_err_msg, model)
                # [trace 2026-10-22] 再发一帧结构化错误 (标题/建议/按钮/脱敏原文), Web 渲染成错误卡片;
                # content_echo = 上面那句人话, 前端据此去重。CLI / 微信不读 delta.error, 行为不变。
                from lib.friendly_errors import classify as _fe_classify
                _fe = _fe_classify(e)
                _fe["content_echo"] = _err_msg.strip()
                yield sse_error(_fe["title"], retry_hint=True, model=model, friendly=_fe)
            except GeneratorExit:
                pass
            try:
                _ctx.new_msgs.append({"role": "assistant", "content": _err_msg.strip()})
            except Exception:
                pass

        _fl.record_turn(_ctx, user_message, error_streak)   # 失败回合写经验 (内部吞异常)
        # AGENT_STREAM_REFACTOR M6a: FINALIZE 段提取为子生成器 (原 L2981-3053)
        async for _ev in _turn_finalize(_ctx, _usage, _batch_state, _guard_stats, model):
            yield _ev

    finally:
        # [v1.0] 安全网: 确保 TRACE_END 一定被写入
        _ctx.tracer.ensure_finished()
        # ── 无论客户端是否断连，都必须保存历史和记忆 ──
        try:
            _turn_persist(_ctx, _test_state, _pm_state, _batch_state)
        except Exception as _save_exc:
            log.warning(f"  [persist:{_ctx.sid_tag}] save failed: {_save_exc}")
        finally:
            # [interrupt-stale-fix 2026-07] 请求结束再清一次 flag,
            # 兜底. 覆盖场景: 用户在 finally save 期间 (毫秒窗口) 点了停止 →
            # flag 添加后本次请求已过检查点 → 下一次请求上来会伪中断.
            if _ctx.session_id:
                with _interrupt_lock:
                    _interrupt_flags.discard(_ctx.session_id)
            if _lock:
                _lock.release()
            _usage_ledger.end_turn(_ledger_tok, ok=_ctx.failure is None, session_id=_ctx.session_id)


# ── FastAPI ───────────────────────────────────────────────────
# Initialize skill index at module load (uvicorn imports this)
_build_skill_index()
_BASE_PROMPT: str = _base_system_prompt()

app = FastAPI(title="LiteCode Server")

# ── Routes: 抽出到 gateway/ 包 (T-52c). 保持原 endpoint 完全一致 ──
from gateway import mount as _mount_gateway_routes
_mount_gateway_routes(app)

# ── Entry ─────────────────────────────────────────────────────
if __name__ == "__main__":
    _build_skill_index()
    _BASE_PROMPT = _base_system_prompt()
    n_skills = len(_SKILL_INDEX)
    print(f"LiteCode Server  port={PORT}  model={MODEL_ID}")
    print(f"Backend : {BACKEND_URL}")
    print(f"Skills  : {n_skills} loaded ({', '.join(s[0] for s in _SKILL_INDEX[:5])}...)")
    from lib.bind_host import bind_host
    _host = bind_host(HOST)   # LITECODE_BIND_HOST (桌面版 127.0.0.1) > config server.host > 0.0.0.0
    print(f"Listen  : {_host}:{PORT}")
    uvicorn.run(app, host=_host, port=PORT, log_level="warning")
