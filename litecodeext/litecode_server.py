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
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, Request
import uvicorn
import logging

# ── Config (from lib/) ────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "core"))  # core 模块扁平导入
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
from lib.agent_state import ProjectMapState, TestTrackState, WebTestState, BatchWriteState, MiscTurnState, UsageCounters, TurnContext
from lib.stats import stats_load as _stats_load, stats_save as _stats_save, stats_add as _stats_add
from lib.guard_stats import GuardStats as _GuardStats
from lib.stats import questions_load as _questions_load, questions_append as _questions_append
from lib.transport import vllm_stream as _vllm_stream, parse_text_tool_calls as _parse_text_tool_calls
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

# [READONLY]/[EXEC-GATE] 共用: "算改了代码文件"的写类工具名单 (单一来源, 避免两处各写一份漂移)。
# 不含 ssh_write_file (远程写不产生本地 git diff), 不含 create_dag/create_skill/create_timer/
# patch_dag_node/task_create (不改代码文件本身)。
_WRITE_TOOL_NAMES = ("write_file", "patch_file", "apply_blocks", "edit_symbol")
_WRITE_TOOL_NAMES_STR = " / ".join(_WRITE_TOOL_NAMES)  # 提醒文案里展示用, 同一份来源

# [EXEC-GATE]/[READONLY] 判定"这次写类工具调用是否*确定*成功落盘"。
# 不用 `not result.startswith("ERROR")` (乐观判据: 空字符串/"Permission denied..."/
# "WARNING: ..." 这类不带 ERROR 前缀的失败输出都会被误判成功) —— 反过来, 只在命中
# 该工具*已知的成功输出格式*时才算成功, 命中不了一律算没成功 (判不准时往"没写成功"倒)。
# apply_blocks 用 "Applied 0/" 排除掉"全部块都失败"这一条 (真实生产 bug, 见 TESTER 修复轮#2
# 报告), 其余块数 >0 (哪怕只是部分成功) 仍算成功, 与 patch_file/apply_blocks 的
# "per-block atomic" 语义一致。
def _salvage_empty_reply(iteration: int, done_tools: list, reasoning: str,
                         max_salvage: int = 1200) -> str:
    """连续空回复触顶放弃时的回传文案 —— 「content 通道永不为空」兜底。

    [FIX 2026-09-04] 若模型把答案误路由进了 reasoning (content 空、reasoning 却
    有实质内容 —— 实测 deep_thinking=false / 某些思考模型会这样), 触顶放弃时不要
    只回一条干瘪的工作摘要, 把 reasoning 尾段取回当正文交给用户, 免得用户对着空白
    干等、有效产出被默默丢掉。reasoning 太短 (无实质内容) 才退回纯摘要。
    """
    tools_note = ", ".join(done_tools[-6:]) or "(无)"
    summary = (f"[本轮已停止生成 — 共 {iteration + 1} 轮, {len(done_tools)} 次工具调用: "
               f"{tools_note}. 如需继续, 请补一条新指令。]")
    rz = (reasoning or "").strip()
    if len(rz) >= 20:
        tail = rz[-max_salvage:]
        if len(rz) > max_salvage:
            tail = "…" + tail
        return ("[以下内容模型放在了思考区、没有作为正文输出, 已为你取回]\n\n"
                f"{tail}\n\n{summary}")
    return summary


def _write_call_confirmed_success(fn_name: str, result: str) -> bool:
    if fn_name == "write_file":
        return result.startswith("Written ")
    if fn_name == "patch_file":
        return result.startswith("Patched ") or result.startswith("Appended -> ")
    if fn_name == "apply_blocks":
        return result.startswith("Applied ") and not result.startswith("Applied 0/")
    if fn_name == "edit_symbol":
        return result.startswith("[edit_symbol] ")
    return False

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


async def _turn_finalize(
    _ctx: TurnContext,
    _usage: UsageCounters,
    _batch_state: BatchWriteState,
    _guard_stats: "_GuardStats",
    model: str,
) -> AsyncGenerator[str, None]:
    """AGENT_STREAM_REFACTOR M6a: agent_stream() 主循环收尾段, 迁自原 L2981-3053。
    [TURN-END] 回合快照日志 + [P46] 全空流 yield sse_error + 尾部 yield sse_usage/sse_stop/sse_done。
    纯结构搬移, 不改行为; 段内 last_reply/total_msgs/_te_*/_gs_line/_had_assistant_msg/
    _empty_reason/_empty_log/_f 均为段内局部, 不跨段读写, 保持裸局部不升状态对象。
    """
    _usage.elapsed = time.time() - _usage.t0
    last_reply = next((m.get("content") or "" for m in reversed(_ctx.new_msgs) if m.get("role") == "assistant" and m.get("content")), "")
    total_msgs = len(_ctx.base_history) + len(_ctx.new_msgs)
    log.info(f"\033[36m[{_ctx.sid_tag}]\033[0m <<< {str(last_reply)[:120]} \033[2m[{_usage.elapsed:.1f}s, total={total_msgs} delta={len(_ctx.new_msgs)}]\033[0m")

    # ── [TURN-END] 回合收尾状态快照 (单点埋点, 不逐个改 break) ──────────────
    # 起因: [EXEC-GATE] 只挂在"模型主动收尾"这一个出口上, 循环里还有若干别的出口
    # (工具重复熔断 / 用户中断 / 空回耗尽 / max_iter 耗尽 / 异常) 会绕过它。
    # 跑 A/B 实验时若只看到"闸没触发 + 依然零写入", 无法区分两种完全不同的原因:
    #   (a) 条件本就不满足 → 闸不该触发 (正常)
    #   (b) 条件满足但走了别的出口 → 闸没机会触发 (实验被污染)
    # 这里不去逐个 break 打标 (改动面大、易漏), 而是记录收尾时的三元组:
    # 迭代数 / 确认成功的写入数 / 闸是否注入过。(b) 的特征是
    # gate=no + iters>=阈值 + writes=0 —— 一眼可辨, 无需知道具体是哪条出口。
    try:
        # 工具轮数复用 plan_gate_tool_call_iters (exec_gate_tick 的调用点传的就是它,
        # 语义一致: "本轮累计发生过工具调用的迭代轮数", 不重复维护第二个计数器)
        _te_iters = _batch_state.plan_gate_tool_call_iters
        _te_writes = _batch_state.exec_gate_write_calls
        _te_gate = _batch_state.exec_gate_injected
        _te_min = int(_AGT.get("exec_gate_min_tool_iters", 4))
        _te_bypassed = (not _te_gate) and _te_iters >= _te_min and _te_writes == 0
        log.info(
            f"  \033[2m[TURN-END]\033[0m [{_ctx.sid_tag}] tool_iters={_te_iters} "
            f"writes_ok={_te_writes} exec_gate={'fired' if _te_gate else 'no'}"
            + ("  \033[33m← 条件满足但闸未触发, 本回合走了绕过出口\033[0m" if _te_bypassed else "")
        )
        # [guard-stats] 本回合各守卫注入次数 —— 空转的守卫会在这里现形。
        # 实测背景: SYSTEM-TEST 曾用错约定误报 79 轮无人知晓, 因为没人统计。
        _gs_line = _guard_stats.summary()
        if _gs_line:
            log.info(f"  \033[2m[GUARD-STATS]\033[0m [{_ctx.sid_tag}] {_gs_line}")
        _guard_stats.flush()
    except Exception:
        pass  # 埋点绝不能影响主流程
    _ctx.tracer.finish(final_text=str(last_reply)[:500])

    # [P46 修] 流末若 content/reasoning/tool_call 全空 → yield error + 落埋点
    try:
        _had_assistant_msg = any(
            m.get("role") == "assistant" and (
                (m.get("content") or "").strip()
                or m.get("tool_calls")
            ) for m in _ctx.new_msgs
        )
        if not _had_assistant_msg:
            _empty_reason = "LLM 返回空 (可能上游超时/限流), 请重试"
            yield sse_error(_empty_reason, retry_hint=True, model=model)
            # 落埋点
            try:
                from pathlib import Path
                _empty_log = Path("/tmp/litecode_workspace/telemetry/empty_response.jsonl")
                _empty_log.parent.mkdir(parents=True, exist_ok=True)
                with open(_empty_log, "a") as _f:
                    _f.write(json.dumps({
                        "ts": time.time(), "sid": _ctx.session_id, "model": model,
                        "duration": _usage.elapsed, "reason": _empty_reason,
                        "prompt_tokens": _usage.total_prompt_tokens,
                    }) + "\n")
            except Exception as _e:
                log.warning(f"empty_response telemetry write fail: {_e}")
    except Exception as _ee:
        log.warning(f"empty check fail: {_ee}")

    # 先把 SSE 结束标记发给客户端，解锁前端 streaming 状态
    try:
        yield sse_usage(_usage.total_prompt_tokens, _usage.total_completion_tokens,
                       _usage.total_iterations, _usage.elapsed, model)
        yield sse_stop(model)
        yield sse_done()
    except GeneratorExit:
        pass  # 客户端已断连，但仍需保存记忆


def _turn_setup_context(
    user_message: str,
    user_content: Any,
    strategist: bool,
    _ctx: TurnContext,
) -> tuple:
    """AGENT_STREAM_REFACTOR M6b: agent_stream() SETUP 段前半, 迁自原 L287-419。
    trace 初始化/中断 flag 清理/委派检测/SESSION-INIT 模板同步/历史加载清洗/
    sys_prompt 构建(含 memory_index 注入)/技能自动加载 trace。纯结构搬移, 不改行为;
    _ctx.tracer/base_history 等经引用原地改写; 返回 (_effective_user_content,
    _force_writer_mode, sys_prompt, _mlc_done_this_turn, _guard_stats) 供调用者接回局部
    (均为主循环要读的松散局部, 函数无法直接回写调用者作用域)。
    """
    # 每个请求开头开新 trace — telemetry.emit 自动注入此 trace_id, 跨子代理/工具链路可 join
    _tlm_new_trace()
    _ctx.tracer = make_tracer(_TRACE_CFG, workspace=WORKSPACE, session_id=_ctx.session_id or "anon")
    # [v1.4] 若没传 user_content, fallback 到 user_message 文本
    _effective_user_content: Any = (
        user_content if user_content is not None else user_message
    )
    _is_multimodal_input = isinstance(_effective_user_content, list)
    if _is_multimodal_input:
        _imgs = sum(1 for p in _effective_user_content
                    if isinstance(p, dict) and p.get("type") == "image_url")
        log.info(f"\033[36m[{_ctx.sid_tag}]\033[0m 🖼️  多模态输入: {_imgs} 张图 + 文字")

    log.info(f"\033[36m[{_ctx.sid_tag}]\033[0m >>> {user_message[:120]}")
    # [interrupt-stale-fix 2026-07] 新请求进来时先清残留 flag.
    # 原来: 用户点停止但当时无 active stream (流刚结束) → flag 添加后无人消费,
    # 下一条 user 消息 iter=0 的 iter-start 检查捞到 → 立刻回复"[任务已被用户中断]".
    # 用户在 UI 看到"点一次停止, 却出现两次中断" (上次空回 + 这次伪中断).
    if _ctx.session_id:
        with _interrupt_lock:
            if _ctx.session_id in _interrupt_flags:
                _interrupt_flags.discard(_ctx.session_id)
                log.info(f"  [{_ctx.sid_tag}] cleared stale interrupt flag at request start")
    # [USER-Q-LOG] 记录用户提问（只存用户文字，不计工具/系统消息）
    try:
        _questions_append(_ctx.session_id, user_message)
    except Exception:
        pass

    # [v1.0] 检测任务是否需要强制分发
    _delegation_info = _detect_auto_delegation(user_message)
    _force_writer_mode = "FORCE_WRITER" in (_delegation_info or "")

    # ── [SESSION-INIT] 新 session：把 workspace_template/*.md 复制进 session 目录 ──
    if _ctx.session_id:
        _sess_dir = SESSIONS_DISK / _ctx.session_id
        _sess_dir.mkdir(parents=True, exist_ok=True)
        _tmpl_mds = ["SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md",
                     "HEARTBEAT.md", "AGENTS.md", "BOOTSTRAP.md"]
        import shutil as _shutil, hashlib as _hashlib, json as _json2
        # [FIX 2026-08-31] 原先是 `if not _dst.exists()` —— 只在会话首次创建时复制,
        # 之后模板再怎么改都不会同步。实测 224 个会话的 SOUL.md md5 全同且全是旧版,
        # 人设/思维方式的改动对生产影响为零。
        # 现改为: 记下复制时的模板 hash; 若会话副本自那以后未被改动 (hash 未变)
        # 而模板已更新, 则同步; 若用户用 update_profile 改过副本, 保留不动。
        _mf = _sess_dir / ".tmpl_hashes.json"
        try:
            _seen = _json2.loads(_mf.read_text()) if _mf.exists() else {}
        except Exception:
            _seen = {}
        _h = lambda _p: _hashlib.md5(_p.read_bytes()).hexdigest()
        _mf_dirty = False
        for _mdf in _tmpl_mds:
            _dst = _sess_dir / _mdf
            _src = next((_d / _mdf for _d in [TEMPLATE_DIR, BASE / "workspace_template"]
                         if (_d / _mdf).exists()), None)
            if _src is None:
                continue
            try:
                if not _dst.exists():
                    _shutil.copy2(str(_src), str(_dst))
                    _seen[_mdf] = _h(_src); _mf_dirty = True
                    log.debug(f"  [SESSION-INIT] copied {_mdf} → {_dst}")
                else:
                    _sh, _dh, _prev = _h(_src), _h(_dst), _seen.get(_mdf)
                    if _sh == _dh:
                        if _prev != _sh:
                            _seen[_mdf] = _sh; _mf_dirty = True   # 补记基线
                    elif _prev is None:
                        # 无基线 (老会话): 副本与模板不同但无从判断是用户改的还是模板旧了。
                        # 保守起见按"未被用户改动"处理一次同步, 并落基线。
                        _shutil.copy2(str(_src), str(_dst))
                        _seen[_mdf] = _sh; _mf_dirty = True
                        log.info(f"  [SESSION-INIT] 首次建立基线并同步 {_mdf} → {_dst}")
                    elif _dh == _prev:
                        # 副本自复制后未被动过, 而模板变了 → 同步
                        _shutil.copy2(str(_src), str(_dst))
                        _seen[_mdf] = _sh; _mf_dirty = True
                        log.info(f"  [SESSION-INIT] 模板已更新, 同步 {_mdf} → {_dst}")
                    # else: 用户改过副本 → 保留, 不覆盖
            except Exception as _ce:
                log.warning(f"  [SESSION-INIT] {_mdf} 同步跳过: {_ce}")
        if _mf_dirty:
            try:
                _mf.write_text(_json2.dumps(_seen, ensure_ascii=False, indent=1))
            except Exception:
                pass
        # memory 子目录
        (_sess_dir / "memory").mkdir(exist_ok=True)

    _ctx.base_history = _get_history(_ctx.session_id) if _ctx.session_id else []
    # P0-1: 清理历史中的旧 tool result，减少发给 vLLM 的 token 量
    # [FIX] 长对话入口裁剪: keep_recent 从 6 降到 4, 防止 session 重进上下文还是 300+ 条
    _ctx.base_history = _clear_old_tool_results(list(_ctx.base_history), keep_recent=4)
    # P0-1b: 修复 dangling tool_calls + 非法 JSON arguments，防止 vLLM/MiniMax 400
    # [FIX 2026-08-31] 剥离历史里的 reasoning_content。
    # 实测会话 wx-b3607: 118 条消息带 reasoning_content 共 86,409 t, 比正文
    # (65,620 t) 还多, 占历史 57%。而两道预算闸门只数 content + tool_calls,
    # 对这个字段完全看不见 —— 这是 192,513 t 撑爆 204,800 窗口的主因
    # (估 107,104 + 漏掉的 86,409 = 193,513, 与 vLLM 实报差 0.5%)。
    # 上一轮的思考过程对下一轮没有价值, 只留最近 1 条;
    # 置空而非删键 —— DeepSeek 要求该字段存在 (见下方 reasoning_content 400 兼容分支)。
    _rc_kept = 0
    for _m in reversed(_ctx.base_history):
        if _m.get("role") == "assistant" and _m.get("reasoning_content"):
            if _rc_kept < 1:
                _rc_kept += 1
            else:
                _m["reasoning_content"] = ""
    _ctx.base_history = _sanitize_history(_ctx.base_history)
    sys_prompt   = build_request_system_prompt(user_message, session_id=_ctx.session_id, strategist=strategist)
    _mlc_done_this_turn = False   # [FIX 2026-08-31] 每回合最多压缩一次
    # [guard-stats] 守卫注入计数 —— 让'某条守卫在空转'可见 (见 lib/guard_stats.py)
    _guard_stats = _GuardStats(workspace=WORKSPACE, session_id=_ctx.session_id or '')

    # ── v1.0: memory_index 历史经验注入 ──
    if HAS_MEMORY_INDEX:
        try:
            _midx = memory_index_get(WORKSPACE)
            _hist_ctx = _midx.search_as_context(user_message, limit=3, max_chars=1500)
            if _hist_ctx:
                sys_prompt += "\n\n" + _hist_ctx
        except Exception as _me:
            log.warning(f"  [memory_index] search failed: {_me}")

    # ── Trace: 记录技能自动加载 ──
    if _ctx.tracer.enabled:
        _matched = auto_load_skills(user_message)
        if _matched:
            import re as _re_trace
            _skill_names = _re_trace.findall(r'\[Active Skill: (\S+)\]', _matched)
            _ctx.tracer.skill_loaded(_skill_names, trigger="auto_match")

    return _effective_user_content, _force_writer_mode, sys_prompt, _mlc_done_this_turn, _guard_stats


async def _turn_setup_autorotate(
    _ctx: TurnContext,
    _orphan_n: int,
    model: str,
) -> AsyncGenerator[str, None]:
    """AGENT_STREAM_REFACTOR M6b: agent_stream() SETUP 段 orphan_n>=3 自动 rotate 分支,
    迁自原 L432-469 (含 session_rotated 状态帧 + 文本提示两处 yield)。纯结构搬移, 不改行为;
    _new_sid/_first_user/_last_turn/_auto_rotated_to 均为段内局部, 不跨段读写, 保持裸局部;
    _ctx.session_id/sid_tag/base_history 经引用原地改写, 调用者无需接回。
    """
    _auto_rotated_to: Optional[str] = None
    if not (_orphan_n >= 3 and _ctx.session_id and SESSIONS_DISK):
        return
    # 触发 auto-rotate
    _first_user = _extract_first_user_summary(_ctx.base_history)
    _last_turn = _extract_last_completed_turn(_ctx.base_history)
    _new_sid = _auto_rotate_session(
        _ctx.session_id, _ctx.base_history, SESSIONS_DISK,
        first_user=_first_user, last_turn=_last_turn,
        reason=f"orphan_overflow(n={_orphan_n})",
    )
    if _new_sid:
        log.warning(f"  \033[35m[auto-rotate]\033[0m {_ctx.session_id} → {_new_sid} (orphan_n={_orphan_n})")
        _auto_rotated_to = _new_sid
        # 切换: _ctx.session_id 改成 new_sid, base_history 用 fresh first_user + last_turn
        # 注意: 不改函数参数 session_id (caller 的), 只改 _ctx.session_id (M5b)
        _ctx.session_id = _new_sid
        _ctx.sid_tag = _new_sid[:8]
        # 重新读 base_history (fresh fork 后的)
        _ctx.base_history = _get_history(_ctx.session_id) if _ctx.session_id else []
        _ctx.base_history = _clear_old_tool_results(list(_ctx.base_history), keep_recent=4)
        _ctx.base_history = _sanitize_history(_ctx.base_history)
        # 通知前端切换 — 通过 SSE status event (key="session_rotated")
        try:
            yield sse_status("session_rotated", {
                "new_sid": _new_sid,
                "reason": f"orphan_overflow(n={_orphan_n})",
                "hint": f"老 session 因 vLLM 状态污染已自动 fork 到 {_new_sid[:12]}, 已继承任务描述 + 最近一段对话",
            }, model)
        except Exception:
            pass
        # 也 yield 一条文本提示让用户在 chat 里能看见
        try:
            yield sse_content(
                f"\n[🔄 AUTO-ROTATE] 检测到老 session 连续 {_orphan_n} 次空回 (vLLM 状态污染), "
                f"已自动 fork 到新 session `{_new_sid[:12]}`, 任务继续在新 session 跑.\n",
                model
            )
        except Exception:
            pass


def _turn_setup_orphan_prefix(
    _ctx: TurnContext,
    _orphan_n: int,
    _effective_user_content: Any,
) -> Any:
    """AGENT_STREAM_REFACTOR M6b: agent_stream() SETUP 段 orphan_n>=1 裁剪 + FORCE-ACTION
    prefix 注入, 迁自原 L470-528。纯结构搬移, 不改行为; _ctx.base_history 经引用原地改写;
    返回更新后的 _effective_user_content (字符串场景是不可变对象重绑定, 调用者需接回局部)。
    """
    if _orphan_n < 1:
        return _effective_user_content
    # 删 orphan user
    while _ctx.base_history and _ctx.base_history[-1].get("role") == "user":
        _ctx.base_history.pop()
    log.info(f"  [orphan-user] detected {_orphan_n} orphans, stripped from history (now {len(_ctx.base_history)} msgs)")
    # 长 history 大裁
    if len(_ctx.base_history) > 30:
        _keep = 10
        _ctx.base_history = _ctx.base_history[-_keep:]
        # 修复 dangling tool_calls (头部如果是 tool 没对应的 assistant)
        while _ctx.base_history and _ctx.base_history[0].get("role") == "tool":
            _ctx.base_history.pop(0)
        log.info(f"  [orphan-user] history > 30, trimmed to last {len(_ctx.base_history)} msgs")
    # [P2-fix 2026-05-23] FORCE-ACTION prefix 三段式升级:
    # n=1 温和提醒, n=2 严厉提醒, n>=3 给具体起手 tool_call 范例
    # 实测 (L4-prod) FORCE-ACTION 第 2 层救场率比第 1 层高很多, 第 3 层应该给最强引导
    if _orphan_n == 1:
        _fa_prefix = (
            "[SYSTEM-FORCE-ACTION] 你上一轮没有产出 tool_call 或正文回复, 用户在等. "
            "请继续推进任务: 调一个工具 (write_file/execute_shell/web_search), "
            "或用 ≥10 字的正文回答.\n\n=== 用户指令 ===\n"
        )
    elif _orphan_n == 2:
        _fa_prefix = (
            f"[SYSTEM-FORCE-ACTION ×2] 你已经 2 次空回, 用户连续追问. "
            f"这次必须立即 emit tool_call, 严禁纯推理无输出. "
            f"如果不确定下一步, 先 execute_shell 'ls -la <相关目录>' 看现状.\n\n"
            f"=== 用户指令 ===\n"
        )
    else:  # n >= 3 (auto-rotate 已触发, 但 prefix 仍打)
        # 提取用户指令的核心动词作为起手建议
        _user_str = ""
        if isinstance(_effective_user_content, str):
            _user_str = _effective_user_content
        elif isinstance(_effective_user_content, list):
            for _p in _effective_user_content:
                if isinstance(_p, dict) and _p.get("type") == "text":
                    _user_str = _p.get("text", "")
                    break
        _hint = "write_file 写第一个相关文件"
        _us = _user_str.lower()
        if any(k in _us for k in ["搜索", "search", "查"]):
            _hint = "web_search 查关键词"
        elif any(k in _us for k in ["跑", "起服务", "build", "test"]):
            _hint = "execute_shell 跑命令"
        elif any(k in _us for k in ["读", "看代码", "看文件"]):
            _hint = "read_file 读相关文件"
        _fa_prefix = (
            f"[SYSTEM-FORCE-ACTION ×{_orphan_n}] 你已经 {_orphan_n} 次空回, 已自动 rotate session. "
            f"这次必须立即 emit 1 个 tool_call. 建议第一个动作: {_hint}.\n"
            f"如果还失败, LiteCode 将放弃本轮.\n\n=== 用户指令 ===\n"
        )
    if isinstance(_effective_user_content, str):
        _effective_user_content = _fa_prefix + _effective_user_content
    elif isinstance(_effective_user_content, list):
        for _part in _effective_user_content:
            if isinstance(_part, dict) and _part.get("type") == "text":
                _part["text"] = _fa_prefix + _part.get("text", "")
                break
    return _effective_user_content





# ── [AGENT_STREAM_REFACTOR M7c 2026-09-18] spawn_agent 分派段, 迁自主循环
#    (原 ~120 行: subagent 注入/超时拉伸/多代理路由/单agent/SSE drain 透传/
#    中断取消/心跳/结果回收)。含 yield → 异步子生成器 (M6a _turn_finalize 同款),
#    结果经 out 持有者回传: out["result"], out["effective_timeout"]。
#    queue 与 emit 回调为调用方每轮闭包, 经参数传入; 其余依赖均模块级。──
async def _dispatch_spawn_agent(fn_args: dict, ctx, sse_emit, sse_queue, out):
    """spawn_agent 工具分派: 透传子代理 SSE + 心跳 + 父会话中断级联取消。"""
    # [v1.0] 确保 execute_tool 已注入到 subagent 模块
    try:
        from lib.agent.subagent import set_execute_tool, execute_tool as _sa_et
        if _sa_et is None:
            set_execute_tool(execute_tool)
    except Exception:
        pass
    # [v1.0] 动态延长超时: spawn_agent(writer) 需要大量时间
    out["effective_timeout"] = max(out["effective_timeout"], TASK_TIMEOUT * 6)

    # [v1.0] 检测多代理编排模式
    # [batch-items-2026-05] 加 batch_items 模式 (批量同类任务, 比如 N 章小说)
    _multi_tasks = (fn_args.get("dag_tasks")
                   or fn_args.get("pipeline_tasks")
                   or fn_args.get("parallel_tasks")
                   or fn_args.get("competitive_tasks")
                   or fn_args.get("batch_items"))
    # [v1.0] 将子代理包装为 task, 同时 drain SSE 队列做实时透传 + 中断检查
    if _multi_tasks and isinstance(_multi_tasks, list) and len(_multi_tasks) > 0:
        # 路由到 multi-agent 编排器
        try:
            from multi_agent import handle_spawn_agent_enhanced
            _sa_coro = handle_spawn_agent_enhanced(
                fn_args,
                run_subagent_fn=_run_subagent,
                sse_emit=sse_emit,
                session_id=ctx.session_id,
                tracer=ctx.tracer,  # [v1.1] 让 DAG 事件进日志
            )
        except ImportError:
            _sa_coro = None
            out["result"] = "ERROR: multi_agent.py not available"
    else:
        # 单 agent 模式
        task_arg   = fn_args.get("task", "")
        agent_type = fn_args.get("agent_type", "coder")
        context_arg = fn_args.get("context", "")
        # [v1.0] max_iter 调大后, timeout 相应放宽
        # coder 50 iter * 平均 10s = 500s, 给 600s
        # researcher 每搜 30-40s, 8 次搜 + 整理 = 400s
        _sa_timeout = 600
        if agent_type == "writer":
            _sa_timeout = 900
        elif agent_type == "researcher":
            _sa_timeout = 400
        elif agent_type == "critic":
            _sa_timeout = 180
        _sa_coro = _run_subagent_safe(
            task_arg, agent_type, context_arg,
            sse_emit=sse_emit,
            timeout=_sa_timeout,
            parent_sid=ctx.session_id,
        )

    if _sa_coro is not None:
        _sa_task = asyncio.ensure_future(_sa_coro)
        try:
            while not _sa_task.done():
                # drain 队列中已有事件（实时透传到前端）
                drained = 0
                while True:
                    try:
                        _chunk = sse_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield _chunk
                    drained += 1
                    if drained >= 50:  # 防止 starvation
                        break
                # 等队列新事件 / 任务完成 / 超时心跳
                _waiters = [
                    asyncio.ensure_future(sse_queue.get()),
                    _sa_task,
                ]
                done, pending = await asyncio.wait(
                    _waiters, timeout=5,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # 清理 queue.get 的 waiter
                for _p in pending:
                    if _p is not _sa_task:
                        _p.cancel()
                # 如果 queue.get 完成了, 把取出的 chunk 发出去
                for _d in done:
                    if _d is not _sa_task:
                        try:
                            yield _d.result()
                        except Exception:
                            pass
                # [v1.0] 中断检查: 父 session 被标记中断 -> 取消子任务
                with _interrupt_lock:
                    if ctx.session_id in _interrupt_flags:
                        log.warning(f"  [INTERRUPT-SPAWN] cancelling subagent task for sid={ctx.session_id}")
                        _sa_task.cancel()
                        try:
                            await _sa_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        break
                # 无事件时发心跳, 避免 SSE 连接被关
                if not done:
                    yield f": heartbeat spawn_agent\n\n"
            # 任务完成 → 获取结果
            if _sa_task.done() and not _sa_task.cancelled():
                try:
                    out["result"] = _sa_task.result()
                except Exception as e:
                    out["result"] = f"ERROR in subagent: {e}"
            elif _sa_task.cancelled():
                out["result"] = "[子代理已取消: 父会话被中断]"
            # drain 队列中剩余事件
            while True:
                try:
                    yield sse_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        except Exception as e:
            out["result"] = f"ERROR in subagent dispatch: {e}"



# ── [AGENT_STREAM_REFACTOR M7d 2026-09-18] 循环顶部上下文治理家族之一:
#    SLIDING-WINDOW 窗口缩紧 + token-budget 硬裁剪 (迁自主循环, 纯同步)。
#    返回本轮重算的 fixed_overhead (原局部 _fixed_overhead, 每轮无条件重算,
#    供 CONTEXT-BUDGET 段使用 —— 原实现靠 locals() 隐式借用, 现显式返回)。──
def _loop_context_window(ctx, usage, iteration, sys_prompt) -> int:
    # ── [SLIDING-WINDOW] 窗口缩紧: 每 5 轮触发, msgs>20 就 trim ──
    # [FIX] 老逻辑 iter%10 + msgs>30: msgs=302 的长 session 要 10 轮才 trim 一次, 上下文早就爆
    # 新逻辑: iter%5, 保留最近 14 条完整, 再配合 token-budget 硬裁剪 (下一段)
    _sw_every   = int(_AGT.get("sliding_window_every", 5))
    _sw_min     = int(_AGT.get("sliding_window_min_msgs", 20))
    _sw_keep    = int(_AGT.get("sliding_window_keep_recent", 14))
    if iteration > 0 and iteration % _sw_every == 0 and len(ctx.messages) > _sw_min:
        _trim_boundary = max(2, len(ctx.messages) - _sw_keep)
        _trimmed_count = 0
        for _mi in range(2, _trim_boundary):  # 跳过 system + 第一条 user
            _msg = ctx.messages[_mi]
            if _msg.get("role") == "tool":
                _old_content = _msg.get("content", "")
                if isinstance(_old_content, str) and len(_old_content) > 120:
                    _first_line = _old_content.split("\n", 1)[0][:120]
                    _msg["content"] = _first_line + " [trimmed]"
                    _trimmed_count += 1
            elif _msg.get("role") == "assistant" and isinstance(_msg.get("content"), str):
                if len(_msg["content"]) > 200:
                    _msg["content"] = _msg["content"][:150] + "..."
                    _trimmed_count += 1
        if _trimmed_count > 0:
            log.info(f"  [SLIDING-WINDOW] trimmed {_trimmed_count} old msgs at iter {iteration} (keep_recent={_sw_keep})")

    # [FIX] token-budget 硬裁剪: 估算 prompt tokens, 超 (CONTEXT_WINDOW*0.6) 就持续丢最早 tool/asst
    # 理由: 单靠 sliding-window 删不干净, 几轮对话后老 tool result 堆 200 条也没删
    _tb_pct     = float(_AGT.get("token_budget_pct", 0.6))
    # [FIX 2026-08-31] 预算此前只数 ctx.messages —— 而 messages 里**没有**
    # system 消息, 工具定义也不在其中。实测会话 wx-b3607: 历史估 99,370 t,
    # vLLM 实报 180,828 t, 差的 8 万就是系统提示词 + 84 个工具的 JSON schema。
    # 预算看不见这块固定开销, 于是永远判定"还够", 最后由 vLLM 返回 400。
    # 现按 (窗口*pct - 固定开销) 作为历史可用额度。
    try:
        _fixed_overhead = est_tokens(sys_prompt) + est_tokens(json.dumps(TOOL_DEFS, ensure_ascii=False))
    except Exception:
        _fixed_overhead = 0
    # 两个口径分开, 不混:
    #   锚点真值 = 整个输入 (含 system+工具+对话) → 直接和 窗口*pct 比
    #   纯估算   = 只有对话历史 → 和 窗口*pct - 固定开销 比
    _tb_budget_est    = max(4096, int(CONTEXT_WINDOW * _tb_pct) - _fixed_overhead)  # 估算口径
    _tb_budget_real   = max(4096, int(CONTEXT_WINDOW * _tb_pct))                    # 锚点口径
    if len(ctx.messages) > 20:
        # [USAGE-ANCHOR] 有上游真实 usage 时, 用"锚点真值 + 自锚点以来的增量估算"。
        # 真值自带全部开销, 比 est 整段更准。保守: 真值 >= 当时估算才用锚 (防上游漏报)。
        _est_full = sum(est_tokens(m.get("content",""))+est_tokens(m.get("tool_calls","")) + est_tokens(m.get("reasoning_content",""))
                        for m in ctx.messages)
        _use_anchor = usage.usage_reports > 0 and usage.last_real_prompt_tokens >= usage.last_est_at_anchor
        if _use_anchor:
            _delta_since = max(0, _est_full - usage.last_est_at_anchor)   # 自锚点后历史增量 (估)
            _cur_tokens = usage.last_real_prompt_tokens + _delta_since
            _tb_budget  = _tb_budget_real
        else:
            _cur_tokens = _est_full
            _tb_budget  = _tb_budget_est
        if _cur_tokens > _tb_budget:
            _removed = 0
            # 从第 3 条开始删 (保留 system + 首 user), 保留最后 10 条
            _drop_cutoff = max(2, len(ctx.messages) - 10)
            _mi = 2
            while _mi < _drop_cutoff and _cur_tokens > _tb_budget:
                _role = ctx.messages[_mi].get("role")
                if _role in ("tool", "assistant"):
                    _c = str(ctx.messages[_mi].get("content") or "")
                    _tc = str(ctx.messages[_mi].get("tool_calls") or "")
                    _cur_tokens -= est_tokens(_c) + est_tokens(_tc)
                    ctx.messages.pop(_mi)
                    _drop_cutoff -= 1
                    _removed += 1
                    continue
                _mi += 1
            if _removed:
                # 在被删位置插一条标记, 防止 tool_calls/tool pair 残缺导致 400
                ctx.messages.insert(2, {"role": "user", "content": f"[SYSTEM: trimmed {_removed} old msgs to fit token budget {_tb_budget}t]"})
                _sanitize_history(ctx.messages)
                log.warning(f"  [TOKEN-BUDGET] removed {_removed} msgs, {_cur_tokens}t/{_tb_budget}t")

    return _fixed_overhead


# ── [M7d] 上下文治理家族之二: MID-LOOP-COMPRESS 记忆压缩触发。
#    原 locals().get("_mlc_done_this_turn") 跨轮标志显式化为参数+返回值。──
def _loop_mid_compress(ctx, iteration, done_flag):
    # ── [MID-LOOP-COMPRESS] 按 token 水位 + 每15轮 触发 memory 压缩 ──
    # [FIX 2026-08-31] 原条件是 `iteration % 15 == 0` —— 只在第 15/30/45 轮检查。
    # 而微信/短对话一轮只跑 2-5 次迭代, iteration 永远到不了 15,
    # **压缩对这类会话从未运行过**。实测会话 wx-b3607 因此涨到 300 条 /
    # 192,513 input tokens, 反复吃 vLLM 400。
    # 现改为: 每 15 轮照旧, 另加"任一轮超过 MEM_HARD 水位即压缩"(每回合最多一次)。
    _mlc_due = (iteration > 0 and iteration % 15 == 0)
    if not _mlc_due and HAS_MEMORY and ctx.session_id and not done_flag:
        _mlc_due = sum(est_tokens(m.get("content","")) for m in ctx.messages) > _MEM_HARD
    if _mlc_due and HAS_MEMORY and ctx.session_id:
        done_flag = True
        _est_tokens = sum(est_tokens(m.get("content","")) for m in ctx.messages)
        if _est_tokens > _MEM_HARD:
            try:
                _mlc_mgr = _mem_get(
                    workspace=WORKSPACE, session_id=ctx.session_id,
                    vllm_url=BACKEND_URL, model_id=MODEL_ID,
                    api_key=API_KEY, context_window=CONTEXT_WINDOW,
                )
                _mlc_mgr.check_and_compact(ctx.messages, _force_async=True)
                log.info(f"  [MID-LOOP-COMPRESS] triggered at iter {iteration}, est_tokens={_est_tokens}")
            except Exception:
                pass

    return done_flag


# ── [M7d] 上下文治理家族之三: CONTEXT-BUDGET LLM 调用前预算裁剪。
#    原 locals().get("_fixed_overhead", 0) 显式化为参数。──
def _loop_context_budget(ctx, fixed_overhead):
    # ── [CONTEXT-BUDGET] LLM 调用前检查 token 预算，超限则裁剪老消息 ──
    # [FIX 2026-08-31] 原先只数 content, 不数 tool_calls —— 而 write_file 的
    # 文件正文全在 tool_calls[].arguments 里, 对写代码的 agent 恰是最大一块。
    # 同文件 L986 的记账口径本来就数了 tool_calls, 两处口径不一致导致
    # 裁剪闸门系统性低估, 真实溢出时直接吃 vLLM 400 而非走优雅裁剪。
    _ctx_est = sum(est_tokens(m.get("content", "")) + est_tokens(m.get("tool_calls", "")) + est_tokens(m.get("reasoning_content",""))
                   for m in ctx.messages)
    # [FIX 2026-08-31] 同样扣掉 system + 工具定义的固定开销 (见上方 TOKEN-BUDGET 注释)
    _ctx_limit = max(4096, int(CONTEXT_WINDOW * 0.75) - fixed_overhead)
    if _ctx_est > _ctx_limit and len(ctx.messages) > 10:
        _ctx_trimmed = 0
        # 从第3条消息开始（跳过 system + 第1条 user），逐条压缩直到低于预算
        for _ci in range(2, len(ctx.messages) - 8):  # 保留最近8条完整
            _cm = ctx.messages[_ci]
            _cc = str(_cm.get("content", ""))
            if len(_cc) > 100:
                if _cm.get("role") == "tool":
                    _cm["content"] = _cc.split("\n", 1)[0][:100] + " [budget-trimmed]"
                elif _cm.get("role") == "user" and "[SYSTEM" in _cc:
                    _cm["content"] = _cc[:80] + " [trimmed]"
                elif _cm.get("role") == "assistant" and isinstance(_cc, str):
                    _cm["content"] = _cc[:120] + "..."
                _ctx_trimmed += 1
            # 重新估算
            _ctx_est2 = sum(est_tokens(m.get("content", "")) + est_tokens(m.get("tool_calls", "")) + est_tokens(m.get("reasoning_content",""))
                            for m in ctx.messages)
            if _ctx_est2 <= _ctx_limit:
                break
        if _ctx_trimmed > 0:
            log.info(f"  [CONTEXT-BUDGET] trimmed {_ctx_trimmed} msgs, {_ctx_est}→{_ctx_est2} tokens est")


# ── [AGENT_STREAM_REFACTOR M7b 2026-09-18] 工具调用前守卫家族, 从主循环整体迁出
#    (原 ~242 行, 5 站点: BLOCK-MAP 强制建图+AUTO-MAP / BLOCK1.5 强制填图 /
#    BLOCK2 强制测试 / BLOCK-OVERWRITE 防覆盖 / ROUTE 长文强制 spawn_agent)。
#    纯同步。返回 None=放行(可能已执行 AUTO-MAP 副作用); 返回 str=拦截理由。
#    共享尾巴 (tool_results.append + tracer + continue) 收拢到调用点写一次; 站点专属
#    log 与 misc_state.system_injected 副作用保留在本函数内。tool_call_id 统一取
#    tc.get("id","") 的容错形态 (原 4 站点 tc["id"] / 1 站点 tc.get, 语义超集)。──
def _guard_tool_call(fn_name: str, fn_args: dict, pm_state, test_state,
                     misc_state, force_writer_mode: bool):
    # ── [BLOCK] PROJECT_MAP 强制: 写第3个源文件前必须有 PROJECT_MAP ──
    if fn_name == "write_file" and pm_state.project_root:
        import os as _bos
        _bfp = fn_args.get("filepath", "").replace("\\", "/")
        _bfn = _bfp.split("/")[-1]
        _bext = "." + _bfn.rsplit(".", 1)[-1] if "." in _bfn else ""
        _BSRC = {".py", ".js", ".ts", ".sh", ".go"}
        _BSKIP = ("test_", "_test.", ".test.", ".spec.", "__init__",
                  "config", "settings", "constants", "conftest",
                  "requirements", "PROJECT_MAP", ".gitkeep")
        _is_src = _bext in _BSRC and not any(s in _bfn for s in _BSKIP)
        _is_test = any(p in _bfn for p in ("test_", "_test.", ".test.", ".spec."))
        _map_path_b = pm_state.project_root + "/PROJECT_MAP.md"

        # BLOCK 1: 写第6个源文件时 PROJECT_MAP 必须存在 → 自动创建
        if (_is_src and pm_state.py_files_written >= 5
                and not pm_state.project_map_written
                and not _bos.path.exists(_map_path_b)):
            # ── [AUTO-MAP] 用 project_map_watcher 深度 AST 分析 ──
            try:
                from project_map_watcher import get_global_watcher
                _watcher = get_global_watcher()
                _watcher.force_update(pm_state.project_root)
                pm_state.project_map_written = True
                pm_state.py_at_map_write = pm_state.py_files_written
                log.info(f"  [AUTO-MAP] AST-analyzed {_map_path_b} via project_map_watcher")
            except ImportError:
                # fallback: 用内联骨架
                _bm_content = _auto_map_build_skeleton(test_state.src_files_written, test_state.test_files_written, pm_state.project_root)
                with open(_map_path_b, "w", encoding="utf-8") as _bmfp:
                    _bmfp.write(_bm_content)
                pm_state.project_map_written = True
                pm_state.py_at_map_write = pm_state.py_files_written
                log.info(f"  [AUTO-MAP] skeleton-created {_map_path_b} (fallback, no AST)")
            except Exception as _bme:
                log.warning(f"  [AUTO-MAP] auto-create failed: {_bme}, falling back to block")
                _reject_result = (
                    f"ERROR[BLOCK-MAP]: 已有 {pm_state.py_files_written} 个源文件但 PROJECT_MAP.md 不存在。\n"
                    f"必须先创建 {_map_path_b}，然后才能继续写源文件。\n"
                    f"本次 write_file({_bfn}) 已被拦截，创建 PROJECT_MAP.md 后重试。"
                )
                log.warning(f"  [BLOCK-MAP] rejected write_file({_bfn}): no PROJECT_MAP")
                return _reject_result

        # BLOCK 1.5: 源文件过多未调 update_map → 拦截并强制填充 PROJECT_MAP
        if (_is_src and pm_state.files_since_map_update >= 5
                and pm_state.project_map_written and pm_state.project_root):
            import os as _bmo
            _map_check = pm_state.project_root + "/PROJECT_MAP.md"
            _map_empty = True
            if _bmo.path.exists(_map_check):
                _mc = open(_map_check, "r", encoding="utf-8").read()
                # 检查 API合约 和 函数索引 表是否有内容行
                _has_api = False
                _has_func = False
                _in_api = False
                _in_func = False
                for _ml in _mc.splitlines():
                    if "## API" in _ml: _in_api = True; _in_func = False; continue
                    if "## 函数索引" in _ml: _in_func = True; _in_api = False; continue
                    if _ml.startswith("## "): _in_api = False; _in_func = False; continue
                    if _in_api and _ml.startswith("|") and not _ml.startswith("|--") and "Method" not in _ml:
                        _has_api = True
                    if _in_func and _ml.startswith("|") and not _ml.startswith("|--") and "文件" not in _ml:
                        _has_func = True
                _map_empty = not (_has_api or _has_func)
            if _map_empty:
                _src_written_list = [f.split("/")[-1] for f in test_state.src_files_written[:10]]
                _reject_result = (
                    f"ERROR[BLOCK-MAP-UPDATE]: 已写入 {pm_state.files_since_map_update} 个源文件未调用 update_map，PROJECT_MAP.md 关键表仍为空。\n"
                    f"本次 write_file({_bfn}) 被拦截。每写5个源文件必须调用一次 update_map。\n"
                    f"步骤: 对每个已写的源文件依次执行:\n"
                    f"  1. read_file(<源文件>)  ← 获取真实签名\n"
                    f"  2. update_map(\n"
                    f"       filepath=<相对路径>,\n"
                    f"       routes=[{{method,path,handler,params含类型,returns响应格式}}],\n"
                    f"       functions=[{{name,params含类型,returns类型,calls调用链,called_by被调用}}],\n"
                    f"       classes=[{{name,bases,methods}}],\n"
                    f"       deps={{imports,db_tables,ext_apis,used_by}},\n"
                    f"       config=[{{key,default,required}}],\n"
                    f"       progress={{status:done/partial/todo,done,todo,issues}},\n"
                    f"       tests=[{{test_file,test_func,status:pass|fail|pending,cmd,covers}}],\n"
                    f"       templates=[{{path,type,purpose}}],  ← 关联html/sql/yaml/prompt资产\n"
                    f"     )\n"
                    f"待注册文件: {', '.join(_src_written_list)}"
                )
                log.warning(f"  [BLOCK-MAP-UPDATE] rejected write_file({_bfn}): {pm_state.files_since_map_update} files without update_map")
                misc_state.system_injected = True
                return _reject_result

        # BLOCK 2: 每3个源文件必须有至少1个对应测试，否则拦截
        if _is_src and pm_state.py_files_written >= 2:
            import os as _tos
            # 构建已测试模块集合（基于文件名+内容扫描）
            _tested_modules = set()
            for _tf_path in test_state.test_files_written:
                _tf_basename = _tf_path.split("/")[-1].rsplit(".", 1)[0]
                # 从文件名提取: test_ssh_client → ssh_client
                if _tf_basename.startswith("test_"):
                    _tested_modules.add(_tf_basename[5:])
                # 扫描文件内容找 def test_xxx / import xxx
                try:
                    _tf_content = open(_tf_path).read(5000)
                    import re as _re
                    for _m in _re.findall(r'def test_(\w+)', _tf_content):
                        _tested_modules.add(_m)
                    for _m in _re.findall(r'from\s+\S+\.(\w+)\s+import', _tf_content):
                        _tested_modules.add(_m)
                except Exception:
                    pass
            # 同样扫描磁盘上 tests/ 目录
            if pm_state.project_root:
                _tests_dir = pm_state.project_root + "/tests"
                if _tos.path.isdir(_tests_dir):
                    for _tdf in _tos.listdir(_tests_dir):
                        if _tdf.startswith("test_") and _tdf.endswith((".py",".js",".ts",".sh",".go")):
                            _tested_modules.add(_tdf.split("test_",1)[1].rsplit(".",1)[0])
                            try:
                                _tdc = open(_tos.path.join(_tests_dir, _tdf)).read(5000)
                                for _m in _re.findall(r'def test_(\w+)', _tdc):
                                    _tested_modules.add(_m)
                                for _m in _re.findall(r'from\s+\S+\.(\w+)\s+import', _tdc):
                                    _tested_modules.add(_m)
                            except Exception:
                                pass

            _untested_b = []
            for _tsf in test_state.src_files_written:
                _tsfname = _tsf.split("/")[-1].rsplit(".", 1)[0]
                _tsfext  = "." + _tsf.rsplit(".", 1)[-1] if "." in _tsf else ".py"
                _tsfdir  = _tsf.rsplit("/", 1)[0]
                _thas = (
                    _tsfname in _tested_modules
                    or _tos.path.exists(f"{_tsfdir}/test_{_tsfname}{_tsfext}")
                    or _tos.path.exists(f"{pm_state.project_root}/tests/test_{_tsfname}{_tsfext}")
                )
                if not _thas:
                    _untested_b.append(_tsf.split("/")[-1])
            # 规则: 累计无测试的源文件超过5个则拦截
            if len(_untested_b) >= 5:
                _ext_tpl = {
                    ".py": f"tests/test_{_untested_b[0].rsplit('.',1)[0]}.py",
                    ".js": f"{_untested_b[0].rsplit('.',1)[0]}.test.js",
                    ".sh": f"tests/test_{_untested_b[0].rsplit('.',1)[0]}.sh",
                    ".go": f"{_untested_b[0].rsplit('.',1)[0]}_test.go",
                }
                _tpl_file = _ext_tpl.get(_bext, f"tests/test_{_untested_b[0].rsplit('.',1)[0]}.py")
                _tpl_body = {
                    ".py": (
                        "import pytest\n"
                        f"# from app.services.{_untested_b[0].rsplit('.',1)[0]} import ...\n"
                        "def test_happy():\n    assert True  # 替换为真实断言\n"
                        "def test_error():\n    with pytest.raises(Exception): pass"
                    ),
                    ".js": (
                        "const assert = require('assert');\n"
                        f"// const mod = require('./{_untested_b[0].rsplit('.',1)[0]}');\n"
                        "assert.ok(true, 'basic test');\nconsole.log('PASS');"
                    ),
                    ".sh": (
                        "#!/usr/bin/env bash\nset -euo pipefail\n"
                        "assert_eq() { [ \"$1\" = \"$2\" ] || { echo \"FAIL: $1 != $2\"; exit 1; }; }\n"
                        f"# source ../app/services/{_untested_b[0]}\n"
                        "assert_eq \"expected\" \"expected\"\necho ALL PASS"
                    ),
                }.get(_bext, "import pytest\ndef test_happy(): assert True")
                _reject_result = (
                    f"ERROR[BLOCK-TEST]: {len(_untested_b)} 个源文件没有测试，"
                    f"超过限额(5)，本次 write_file({_bfn}) 被拦截。\n"
                    f"必须先为以下模块写测试才能继续写源文件:\n"
                    + "\n".join(f"  - {f}" for f in _untested_b[:3])
                    + f"\n\n立即创建: {_tpl_file}\n"
                    f"模板:\n{_tpl_body}\n\n"
                    f"写完测试后必须立即执行验证（不能跳过）。每累计5个无测试模块会再次拦截。"
                )
                log.warning(f"  [BLOCK-TEST] rejected write_file({_bfn}): {len(_untested_b)} untested")
                misc_state.system_injected = True  # 下轮跳过enforce
                return _reject_result

    # ── [BLOCK] PROJECT_MAP patch_file 保护: 检测到 write_file 覆盖 PROJECT_MAP ──
    if (fn_name == "write_file"
            and "PROJECT_MAP" in fn_args.get("filepath", "")
            and pm_state.project_map_written):
        # 已经有 PROJECT_MAP 了，用 write_file 会覆盖，拦截并要求 patch_file
        _reject_result = (
            "ERROR[BLOCK-OVERWRITE]: PROJECT_MAP.md 已存在，不能用 write_file 覆盖（会丢失已有内容）。\n"
            "必须用 patch_file 追加新行到对应表格。\n"
            "示例: patch_file(filepath='PROJECT_MAP.md', old_str='| 最后一行 |', new_str='| 最后一行 |\n| 新模块:行 | 函数(参数) | 返回 |')"
        )
        log.warning(f"  [BLOCK-OVERWRITE] rejected write_file(PROJECT_MAP): use patch_file")
        return _reject_result

    # [v1.0] 架构级强制路由: 长文 write_file 拦截转 spawn_agent
    # 当写作任务模式下, LLM 试图直接 write_file 写 >5000c 的 .md 文件时,
    # 拒绝执行并返回错误, 强制 LLM 改用 spawn_agent(writer)
    if (fn_name == "write_file" and force_writer_mode
            and fn_args.get("filepath", "").endswith(".md")):
        _content = fn_args.get("content") or ""
        if len(_content) > 5000:
            log.warning(f"  [ROUTE] write_file intercepted: {len(_content)}c .md in writer mode -> forcing spawn_agent")
            _reject_result = (
                f"ERROR[ROUTE]: 写作任务中禁止在主循环直接 write_file 超过 5000 字的 .md 文件 "
                f"(当前 {len(_content)} 字)。\n"
                "你必须使用 spawn_agent(agent_type='writer', task='写第N章...') 来分批写作。\n"
                "每次 spawn_agent 只写 1-3 章, 每章一个独立文件。\n"
                "已拦截本次 write_file, 请立即改用 spawn_agent。"
            )
            return _reject_result

    return None

# ── [AGENT_STREAM_REFACTOR M7a 2026-09-18] update_map 内部工具实现, 从主循环
#    工具分派段整体迁出 (原 ~295 行内联块)。纯同步、无 yield/await、无 _ctx 依赖,
#    接口只收 fn_args + ProjectMapState。行为与迁移前逐行一致 (dedent + pm_state 重命名)。──
def _tool_update_map(fn_args: dict, pm_state) -> str:
    """update_map: LLM 传结构化数据, 系统写入 PROJECT_MAP.md (per-file sections)。
    返回 result 字符串 (OK:.../ERROR:...); 调用方负责 diff=None 与后续分派。"""
    try:
        import os as _um_os
        import re as _um_re
        pm_state.files_since_map_update = 0  # 重置计数
        _um_fp = fn_args.get("filepath", "")
        _um_routes = fn_args.get("routes", [])
        _um_funcs = fn_args.get("functions", [])
        _um_classes = fn_args.get("classes", [])
        _um_file_desc = fn_args.get("file_description", "")
        _um_constants = fn_args.get("constants", "")
        _um_deps_graph = fn_args.get("deps_graph", "")
        _um_core_flow = fn_args.get("core_flow", "")
        _um_map = (pm_state.project_root + "/PROJECT_MAP.md") if pm_state.project_root else ""

        if not _um_map or not _um_os.path.exists(_um_map):
            result = "ERROR: PROJECT_MAP.md not found"
        else:
            _um_content = open(_um_map, "r", encoding="utf-8").read()
            _um_bn = _um_fp.split("/")[-1]
            _inserted = []

            # ── 构建该文件的完整 section 内容 ──
            _sec_lines = []

            # 文件描述
            _lc = 0
            try:
                _full_path = _um_os.path.join(pm_state.project_root, _um_fp)
                if _um_os.path.exists(_full_path):
                    with open(_full_path, "r", encoding="utf-8", errors="ignore") as _lcf:
                        _lc = sum(1 for _ in _lcf)
            except Exception:
                pass
            _desc_str = f"**{_um_file_desc}**\n" if _um_file_desc else ""
            _sec_lines.append(f"\n---\n## {_um_bn}\n{_desc_str}")

            # API Endpoints 子表
            if _um_routes:
                _sec_lines.append(f"\n### API Endpoints\n")
                _sec_lines.append(f"| Route | Function | Params | Doc |")
                _sec_lines.append(f"|-------|----------|--------|-----|")
                for _r in _um_routes:
                    _route_str = f"`{_r.get('method','?')}` `{_r.get('path','?')}`"
                    _sec_lines.append(
                        f"| {_route_str} | `{_r.get('handler','-')}` "
                        f"| `{_r.get('params','-')}` | {_r.get('note','-')} |"
                    )
                _inserted.append(f"{len(_um_routes)} routes")

            # Classes 子表 (每个 class 独立小节)
            if _um_classes:
                for _c in _um_classes:
                    _c_ln = _c.get("line_no", "")
                    _c_name = _c.get("name", "")
                    _c_bases = _c.get("bases", "")
                    _c_bases_str = f"({_c_bases})" if _c_bases else ""
                    _sec_lines.append(f"\n### class {_c_name}{_c_bases_str}\n")
                    # 如果有 methods 字符串，解析成表
                    _methods_str = _c.get("methods", "")
                    if _methods_str:
                        _sec_lines.append(f"| L# | Method | Params | Return | Doc |")
                        _sec_lines.append(f"|----|--------|--------|--------|-----|")
                        for _m in _methods_str.split(","):
                            _m = _m.strip()
                            if _m:
                                _sec_lines.append(f"| - | `{_m}` | - | - | - |")
                    _note = _c.get("note", "")
                    if _note:
                        _sec_lines.append(f"\n{_note}\n")
                _inserted.append(f"{len(_um_classes)} classes")

            # Functions 子表
            if _um_funcs:
                _sec_lines.append(f"\n### Functions\n")
                _sec_lines.append(f"| L# | Function | Params | Return | Doc |")
                _sec_lines.append(f"|----|----------|--------|--------|-----|")
                for _f in _um_funcs:
                    _ln = _f.get("line_no", "-")
                    _sec_lines.append(
                        f"| {_ln} | `{_f.get('name','-')}` "
                        f"| `{_f.get('params','-')}` | `{_f.get('returns','-')}` "
                        f"| {_f.get('note','-')} |"
                    )
                _inserted.append(f"{len(_um_funcs)} functions")

            # Constants
            if _um_constants:
                _sec_lines.append(f"\n**Constants**: `{_um_constants}`\n")
                _inserted.append("constants")

            # ── 替换或插入该文件的 section ──
            # 查找已有的 ## {_um_bn} section 并替换
            _sec_pattern = f"\n---\n## {_um_re.escape(_um_bn)}\n"
            _sec_start = _um_content.find(f"\n---\n## {_um_bn}\n")
            if _sec_start == -1:
                _sec_start = _um_content.find(f"## {_um_bn}\n")
            
            if _sec_start >= 0:
                # 找到 section 结束位置 (下一个 \n---\n## 或文件末尾)
                _rest = _um_content[_sec_start + 1:]
                # 跳过当前 section header 行
                _next_sec = _rest.find("\n---\n## ", 5)
                if _next_sec > 0:
                    _sec_end = _sec_start + 1 + _next_sec
                else:
                    # 检查是否有其他顶级 ## section (测试覆盖、配置项等)
                    _next_top = -1
                    for _top_sec in ["\n---\n## 测试覆盖", "\n---\n## 配置项", "\n---\n## 实现进度",
                                     "\n---\n## 变更日志", "\n---\n## 核心论点", "\n---\n## 场景列表",
                                     "\n---\n## 角色表", "\n---\n## 章节大纲"]:
                        _pos = _um_content.find(_top_sec, _sec_start + 5)
                        if _pos > _sec_start and (_next_top == -1 or _pos < _next_top):
                            _next_top = _pos
                    _sec_end = _next_top if _next_top > 0 else len(_um_content)
                # 替换
                _um_content = _um_content[:_sec_start] + "\n".join(_sec_lines) + "\n" + _um_content[_sec_end:]
            else:
                # 没有找到，在 测试覆盖 或 配置项 之前插入
                _insert_before = None
                for _anchor in ["\n---\n## 测试覆盖", "\n---\n## 配置项", "\n---\n## 实现进度",
                                "\n---\n## 变更日志", "\n---\n## 核心论点", "\n---\n## 场景列表"]:
                    _pos = _um_content.find(_anchor)
                    if _pos > 0:
                        _insert_before = _pos
                        break
                if _insert_before:
                    _um_content = _um_content[:_insert_before] + "\n".join(_sec_lines) + "\n" + _um_content[_insert_before:]
                else:
                    _um_content += "\n" + "\n".join(_sec_lines) + "\n"

            # ── 更新全局 sections (deps_graph, core_flow) ──
            if _um_deps_graph:
                _dep_marker = "## 模块依赖\n```"
                _dep_pos = _um_content.find(_dep_marker)
                if _dep_pos >= 0:
                    _dep_end = _um_content.find("```\n", _dep_pos + len(_dep_marker))
                    if _dep_end >= 0:
                        _um_content = (_um_content[:_dep_pos] +
                                       f"## 模块依赖\n```\n{_um_deps_graph}\n```\n" +
                                       _um_content[_dep_end + 4:])
                        _inserted.append("deps_graph")

            if _um_core_flow:
                _flow_marker = "## 核心流程\n```"
                _flow_pos = _um_content.find(_flow_marker)
                if _flow_pos >= 0:
                    _flow_end = _um_content.find("```\n", _flow_pos + len(_flow_marker))
                    if _flow_end >= 0:
                        _um_content = (_um_content[:_flow_pos] +
                                       f"## 核心流程\n```\n{_um_core_flow}\n```\n" +
                                       _um_content[_flow_end + 4:])
                        _inserted.append("core_flow")

            # ── 更新扁平表 (测试覆盖, 配置项, 实现进度, 章节, 论点, 场景, 角色, 变更日志) ──
            _um_lines = _um_content.split("\n")

            # 依赖关系表
            _um_deps = fn_args.get("deps", {})
            if _um_deps:
                # 清除旧条目
                _um_lines = [l for l in _um_lines
                             if not (l.startswith("|") and
                                     len([c.strip() for c in l.split("|")]) > 1 and
                                     [c.strip() for c in l.split("|")][1] == _um_fp)]

            # 测试覆盖
            _um_tests = fn_args.get("tests", [])
            if _um_tests:
                # 清除该文件旧测试行
                _um_lines = [l for l in _um_lines
                             if not (l.startswith("|") and _um_bn in l and "test" in l.lower())]
                _pos_tst = _um_find_table_end(_um_lines, "## 测试覆盖")
                if _pos_tst > 0:
                    for _t in reversed(_um_tests):
                        _tst_icon = {"pass": "✅", "fail": "❌", "pending": "⏳", "skip": "⏭"}.get(_t.get("status", "pending"), "⏳")
                        _row = (f"| {_um_bn} | {_t.get('test_file','-')} | "
                                f"{_t.get('test_func','-')} | {_tst_icon}{_t.get('status','pending')} | "
                                f"{_t.get('cmd','-')} | {_t.get('covers','-')} |")
                        _um_lines.insert(_pos_tst + 1, _row)
                    _inserted.append(f"{len(_um_tests)} tests")

            # 配置项
            _um_config = fn_args.get("config", [])
            if _um_config:
                _um_lines = [l for l in _um_lines
                             if not (l.startswith("|") and
                                     len([c.strip() for c in l.split("|")]) > 1 and
                                     [c.strip() for c in l.split("|")][1] == _um_fp)]
                _pos_cfg = _um_find_table_end(_um_lines, "## 配置项")
                if _pos_cfg > 0:
                    for _cfg in reversed(_um_config):
                        _row = (f"| {_um_fp} | {_cfg.get('key','')} | "
                                f"{_cfg.get('default','-')} | {'是' if _cfg.get('required') else '否'} | "
                                f"{_cfg.get('note','')} |")
                        _um_lines.insert(_pos_cfg + 1, _row)
                    _inserted.append(f"{len(_um_config)} configs")

            # 实现进度
            _um_prog = fn_args.get("progress", {})
            if _um_prog:
                _um_lines = [l for l in _um_lines
                             if not (l.startswith("|") and
                                     len([c.strip() for c in l.split("|")]) > 1 and
                                     [c.strip() for c in l.split("|")][1] in (_um_fp, _um_bn))]
                _pos_prg = _um_find_table_end(_um_lines, "## 实现进度")
                if _pos_prg > 0:
                    _row = (f"| {_um_bn} | {_um_prog.get('status','-')} | "
                            f"{_um_prog.get('done','-')} | {_um_prog.get('todo','-')} | "
                            f"{_um_prog.get('issues','-')} |")
                    _um_lines.insert(_pos_prg + 1, _row)
                    _inserted.append("1 progress")

            # 章节大纲
            _um_chapters = fn_args.get("chapters", [])
            if _um_chapters:
                _pos_ch = _um_find_table_end(_um_lines, "## 章节大纲")
                if _pos_ch > 0:
                    for _ch in reversed(_um_chapters):
                        _row = f"| {_ch.get('order','')} | {_ch.get('title','')} | {_ch.get('status','')} | {_ch.get('words','')} | {_ch.get('note','')} |"
                        _um_lines.insert(_pos_ch + 1, _row)
                    _inserted.append(f"{len(_um_chapters)} chapters")

            # 论点
            _um_args = fn_args.get("arguments", [])
            if _um_args:
                _pos_ar = _um_find_table_end(_um_lines, "## 核心论点")
                if _pos_ar > 0:
                    for _ag in reversed(_um_args):
                        _row = f"| {_ag.get('claim','')} | {_ag.get('evidence','')} | {_ag.get('source','')} | {_ag.get('strength','')} |"
                        _um_lines.insert(_pos_ar + 1, _row)
                    _inserted.append(f"{len(_um_args)} arguments")

            # 场景
            _um_scenes = fn_args.get("scenes", [])
            if _um_scenes:
                _pos_sc = _um_find_table_end(_um_lines, "## 场景列表")
                if _pos_sc > 0:
                    for _sc in reversed(_um_scenes):
                        _sc_icon = {"done": "✅", "draft": "✏️", "review": "🔍"}.get(_sc.get("status",""), "⏳")
                        _row = (f"| {_sc.get('scene_id','-')} | {_um_fp} | "
                                f"{_sc.get('title','-')} | {_sc.get('location','-')} | "
                                f"{_sc.get('characters','-')} | {_sc_icon}{_sc.get('status','-')} | "
                                f"{_sc.get('words','-')} | {_sc.get('note','')} |")
                        _um_lines.insert(_pos_sc + 1, _row)
                    _inserted.append(f"{len(_um_scenes)} scenes")

            # 角色
            _um_chars = fn_args.get("characters", [])
            if _um_chars:
                _pos_chr = _um_find_table_end(_um_lines, "## 角色表")
                if _pos_chr > 0:
                    for _ch in reversed(_um_chars):
                        _chr_name = _ch.get("name","")
                        _um_lines = [l for l in _um_lines
                                     if not (l.startswith("|") and
                                             [c.strip() for c in l.split("|")][1:2] == [_chr_name])]
                        _pos_chr = _um_find_table_end(_um_lines, "## 角色表")
                        _row = (f"| {_chr_name} | {_ch.get('role','-')} | "
                                f"{_ch.get('arc','-')} | {_ch.get('first_appearance',_um_fp)} | "
                                f"{_ch.get('status','active')} |")
                        _um_lines.insert(_pos_chr + 1, _row)
                    _inserted.append(f"{len(_um_chars)} characters")

            # 变更日志
            _um_clog = fn_args.get("changelog", "")
            if _um_clog:
                import datetime as _clog_dt
                _now = _clog_dt.datetime.now().strftime("%m-%d %H:%M")
                _pos_cl = _um_find_table_end(_um_lines, "## 变更日志")
                if _pos_cl > 0:
                    _row = f"| {_now} | update | {_um_fp} | {_um_clog} |"
                    _um_lines.insert(_pos_cl + 1, _row)
                    _inserted.append("1 changelog")

            # 模板资产
            _um_tmpls = fn_args.get("templates", [])
            if _um_tmpls:
                # 追加到文件 section 末尾（已在上面 _sec_lines 处理）
                pass

            # 写回
            _final_content = "\n".join(_um_lines)
            with pm_state.project_map_lock:
                with open(_um_map, "w", encoding="utf-8") as _umw:
                    _umw.write(_final_content)

            _ins_str = ", ".join(_inserted) if _inserted else "section updated"
            result = f"OK: updated PROJECT_MAP for {_um_fp} ({_ins_str})"
            log.info(f"  [update_map] {_um_fp}: {_ins_str}")
    except Exception as _ume:
        result = f"ERROR: update_map failed: {_ume}"
        log.warning(f"  [update_map] failed: {_ume}")
    return result

async def agent_stream(
    user_message: str,
    session_id: Optional[str],
    model: str = MODEL_ID,
    user_content: Any = None,   # [v1.4] 多模态: 若传 list, 直接作为 user.content
    strategist: bool = False,   # [2026-09-03] 权谋/多方博弈推演模式 (前端 chip)
) -> AsyncGenerator[str, None]:
    # 同 session 的并发请求串行化：防止两个 client 同时跑 agent loop 导致 LLM 并行/文件竞争
    _lock = _get_session_lock(session_id) if session_id else None
    if _lock:
        await _lock.acquire()
    # ── AGENT_STREAM_REFACTOR M5a: 防 UnboundLocalError, 必须在 try 之前构造 ──
    # finally 依赖 _ctx.new_msgs / _ctx.base_history / _ctx.sid_tag, 若构造放 try 内
    # 且抛异常, finally 引用 _ctx 会 UnboundLocalError (老 bug 换个形式复活)
    _ctx = TurnContext(sid_tag=session_id[:8] if session_id else "anon", session_id=session_id)
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
        _new_msg_id = uuid.uuid4().hex[:12]
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
        _consecutive_enforces = 0  # [FIX] 连续 enforce 计数器 -- 声明后全函数体内零读零写引用, AGENT_STREAM_REFACTOR M3 判定纯死代码, 保留原地不迁 (见迭代回执)
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
                _sanitize_history(_ctx.messages)

                # ── [CONTEXT-BUDGET] LLM 调用前检查 token 预算，超限则裁剪老消息 ──
                _loop_context_budget(_ctx, _fixed_overhead)
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
                _loop_reasoning_lines: list = []  # 最近 8 行 reasoning (清洗后)
                _loop_broken = False
                _reasoning_nudge_sent = False  # [v4 2026-07] 5000c nudge, 7000c 断 (原 12000/13500)
                # [FIX] 累加本轮 reasoning 原文，用于 "只推理不干活" 的回落持久化。
                # 以前 delta.reasoning 只 yield 给前端不入库 → 一旦模型只思考不产出，
                # new_msgs 里就只剩开头那条 user → 日志 saved 1 msgs → UX 直接"退出"。
                _full_reasoning = ""
                # [TOKEN-STATS 真值 2026-09-05] 本轮上游回报的真实 token (0=本轮没拿到锚点 → 退回估算)。
                # usage 锚点此前只喂上下文预算, 没回写 token_stats → 账单是估的、预算是真的, 思考模型下系统性偏差。
                _iter_real_prompt = 0
                _iter_real_completion = 0
                while _vllm_retry_count <= _vllm_max_retry:
                    try:
                        async for chunk in _vllm_stream(_ctx.messages, TOOL_DEFS,
                                                         max_tokens_override=_overflow_max_tokens):
                            # ── [STREAM-INTERRUPT] vLLM 流式输出期间检查中断 ──
                            with _interrupt_lock:
                                if _ctx.session_id in _interrupt_flags:
                                    _interrupt_flags.discard(_ctx.session_id)
                                    log.warning(
                                        f"  \033[31m[INTERRUPT@stream:{_ctx.sid_tag}]\033[0m "
                                        f"vLLM 流中断, 已累积 text={len(full_text)}c "
                                        f"reasoning={len(_full_reasoning)}c tools={len(acc_tools)}"
                                    )
                                    _stream_interrupted = True
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
                                                         for _m in _ctx.messages)
                                        if _cur_est_t > 0 and (_tpt/_cur_est_t < 0.7 or _tpt/_cur_est_t > 1.5 or _usage.usage_reports == 0):
                                            log.info(f"  \033[2m[USAGE-ANCHOR]\033[0m [{_ctx.sid_tag}] 真实={_tpt} 估算={_cur_est_t} 比值={_tpt/_cur_est_t:.2f}"
                                                     + ("  \033[33m← 估算偏离\033[0m" if (_tpt/_cur_est_t<0.7 or _tpt/_cur_est_t>1.5) else ""))
                                        _usage.last_real_prompt_tokens = _tpt
                                        _usage.last_est_at_anchor = _cur_est_t
                                        _usage.usage_reports += 1
                                        _iter_real_prompt = _tpt  # [TOKEN-STATS] 回写 stats 用
                                        _tct = _top_usage.get("completion_tokens")
                                        if isinstance(_tct, int) and _tct > 0:
                                            _usage.total_completion_tokens += _tct
                                            _iter_real_completion += _tct
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
                                        for _m in _ctx.messages)
                                    # [USAGE-ANCHOR] 每次锚定打一行比值 —— 隐形漂移在这里现形。
                                    # 比值稳定偏离 1.0 就说明有东西没被估算计入 (今天的 reasoning
                                    # 漏算、系统提示词盲区都会在第一次锚定就暴露)。
                                    if _cur_est > 0:
                                        _ratio = _pt / _cur_est
                                        if _ratio < 0.7 or _ratio > 1.5 or _usage.usage_reports == 0:
                                            log.info(f"  \033[2m[USAGE-ANCHOR]\033[0m [{_ctx.sid_tag}] "
                                                     f"真实={_pt} 估算={_cur_est} 比值={_ratio:.2f}"
                                                     + ("  \033[33m← 估算偏离, 有开销没计入\033[0m" if (_ratio<0.7 or _ratio>1.5) else ""))
                                    _usage.last_real_prompt_tokens = _pt
                                    _usage.last_est_at_anchor = _cur_est
                                    _usage.usage_reports += 1
                                    _iter_real_prompt = _pt  # [TOKEN-STATS] 回写 stats 用
                                    _ct2 = _up_usage.get("completion_tokens")
                                    if isinstance(_ct2, int) and _ct2 > 0:
                                        _usage.total_completion_tokens += _ct2  # 真值优先, 覆盖估算
                                        _iter_real_completion += _ct2

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
                                _last_yield_ts = time.time()
                                # [FIX] 累积 reasoning 原文, 流结束若无 content/tools 时留作落库
                                _full_reasoning += _rc_clean
                                # [v1.4] 死循环检测
                                _loop_reasoning_chars += len(_reasoning_chunk)
                                # 按换行切, 清洗后只留非空短行 (长句很少重复, 不检测)
                                for _ln in _reasoning_chunk.split("\n"):
                                    _ln_strip = _ln.strip()
                                    if _ln_strip and len(_ln_strip) <= 50:
                                        _loop_reasoning_lines.append(_ln_strip)
                                        _loop_reasoning_lines = _loop_reasoning_lines[-8:]
                                # 8 行里不同内容 ≤2 → 循环
                                if (len(_loop_reasoning_lines) >= 6
                                        and len(set(_loop_reasoning_lines)) <= 2):
                                    log.warning(
                                        f"  [REASONING-LOOP] detected, breaking stream. "
                                        f"recent={list(set(_loop_reasoning_lines))[:2]}"
                                    )
                                    _loop_msg = ("\n（思考绕进了同一个圈子里，已经先停下来了，"
                                                 "换个问法或补充点细节再试一次吧。）\n")
                                    full_text += _loop_msg
                                    yield sse_content(_loop_msg, model)
                                    _loop_broken = True
                                    break
                                # [v4 2026-07] 阈值收紧: nudge 5000c / 中断 7000c
                                # 之前 12000/13500 → 模型每轮浪费 13500c, 多轮累计"几万字".
                                # 现在 5000c nudge → 7000c 断, 每轮最多浪费 7000c.
                                # 叠加 _reasoning_stall_count 跨迭代升级约束.
                                _nudge_thresh = 5000
                                _break_thresh = 7000
                                if (_loop_reasoning_chars > _nudge_thresh
                                        and _loop_reasoning_chars <= _nudge_thresh + 200
                                        and not full_text and not acc_tools
                                        and not _reasoning_nudge_sent):
                                    _reasoning_nudge_sent = True
                                    _nudge = (
                                        "\n（这次想得有点久了，马上就好，稍等一下……）\n"
                                        if _reasoning_stall_count > 0 else
                                        "\n（还在思考中，稍等一下……）\n"
                                    )
                                    yield sse_content(_nudge, model)
                                if (_loop_reasoning_chars > _break_thresh
                                        and not full_text and not acc_tools):
                                    log.warning(
                                        f"  [REASONING-STALL] {_loop_reasoning_chars} 字 reasoning 无进展, "
                                        f"中断 (阈值 {_break_thresh}, 本次第 {_reasoning_stall_count + 1} 次)")
                                    _stall_msg = (
                                        "\n（这轮想得太久了，已经先停下来了，"
                                        "你可以换个说法或拆成更小的问题再问我一次。）\n"
                                    )
                                    full_text += _stall_msg
                                    yield sse_content(_stall_msg, model)
                                    _loop_broken = True
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
                                                    _full_reasoning += _buf
                                                    yield sse_reasoning(_buf, model)
                                                _buf = ""
                                                break
                                            # 标签内是推理
                                            if _j > 0:
                                                _full_reasoning += _buf[:_j]
                                                yield sse_reasoning(_buf[:_j], model)
                                            _buf = _buf[_j + 8:]  # 去掉 </think>
                                            _think_open = False
                                else:
                                    full_text += _ct
                                    yield sse_content(_ct, model)
                                _last_yield_ts = time.time()
                                # [v1.4] 有实质内容 → 重置死循环计数
                                _loop_reasoning_chars = 0
                                _loop_reasoning_lines.clear()

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
                                _loop_reasoning_lines.clear()
                                _reasoning_nudge_sent = False  # [v2 2026-05] 一并重置 nudge 状态

                            # [OPT] P0-C: 工具参数累积期间发送心跳，防止客户端 read timeout
                            if time.time() - _last_yield_ts > 15:
                                yield ": heartbeat llm_stream\n\n"
                                _last_yield_ts = time.time()

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
                            _ctx_limit    = int(_ctx_m.group(1))   if _ctx_m  else CONTEXT_WINDOW
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
                            _full_reasoning = ""
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
                                for _msg in _ctx.messages:
                                    if _msg.get("role") == "assistant" and "reasoning_content" not in _msg:
                                        _msg["reasoning_content"] = ""
                                full_text = ""
                                acc_tools = {}
                                finish_reason = None
                                _full_reasoning = ""
                                continue  # 重试
                            # 其他 400: 逐级升级清理策略
                            if _vllm_retry_count == 1:
                                # 第1次重试：标准 sanitize（Pass 1-4）
                                log.warning(f"  [400-GUARD] retry {_vllm_retry_count}/3: sanitize history")
                                yield f": [400-retry-{_vllm_retry_count}] sanitizing history...\n\n"
                                _sanitize_history(_ctx.messages)
                            elif _vllm_retry_count == 2:
                                # 第2次重试：强制重建 tool_result 配对
                                log.warning(f"  [400-GUARD] retry {_vllm_retry_count}/3: rebuilding pairs")
                                yield f": [400-retry-{_vllm_retry_count}] rebuilding tool pairs...\n\n"
                                # 只保留最近 8 条，强制重新 sanitize
                                sys_msgs = [m for m in _ctx.messages if m.get("role") == "system"]
                                recent   = [m for m in _ctx.messages if m.get("role") != "system"][-8:]
                                _ctx.messages = sys_msgs + recent
                                _sanitize_history(_ctx.messages)
                            else:
                                # 第3次重试：核武器 — 完全清除所有 tool_calls/tool results
                                log.warning(f"  [400-GUARD] retry {_vllm_retry_count}/3: NUCLEAR - strip all tools")
                                yield f": [400-retry-{_vllm_retry_count}] nuclear clean...\n\n"
                                _ctx.messages = _nuke_tool_history(_ctx.messages)
                            full_text = ""
                            acc_tools = {}
                            finish_reason = None
                            _full_reasoning = ""  # [FIX] 400 重试时也清, 和 full_text/acc_tools 一致
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
                            _ctx.messages = _nuke_tool_history(_ctx.messages)
                            _vllm_success = True
                            break
                    break  # 正常跳出 while（非 400 情况）
                # while end

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
                            "[SYSTEM] 上一轮你陷入了推理复述循环 (没有 emit 任何 tool_call / content). "
                            "现在要求: 1) 直接调用一个合适的 tool 或用 2-3 句话回答用户; "
                            "2) 不要在 reasoning 里反复说'我先搜索X'或'我先查Y'; "
                            "3) 如果搜索确实需要, 调用 web_search 工具就对了, 不要反复思考."
                        )
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
                    _model_id_low = (MODEL_ID or "").lower() if 'MODEL_ID' in dir() else ""
                    if not _model_id_low:
                        # 兜底: 从 _LIVE 拿
                        try:
                            from lib.config import _LIVE as _live_cfg_dict
                            _model_id_low = str(_live_cfg_dict.get("model_id", "")).lower()
                        except Exception:
                            _model_id_low = ""
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

                    for tc in tool_call_list:
                        fn_name = tc["function"]["name"]
                        try:
                            fn_args = json.loads(tc["function"]["arguments"] or "{}")
                        except Exception:
                            fn_args = {}

                        # ── [TOOL-LOOP] 主循环工具重复死循环检测 (S1-D → plugins/tools/guard.py) ──
                        _tl_blocked, _tl_warn, _tl_sig = _tool_loop_guard.check(fn_name, fn_args)
                        if _tl_blocked:
                            tool_results.append({
                                "role": "tool", "tool_call_id": tc["id"],
                                "content": _tl_warn,
                            })
                            _ctx.tracer.tool_call(name=fn_name, args=fn_args,
                                              result=_tl_warn, elapsed=0, is_error=True)
                            log.warning(f"  \033[33m[TOOL-LOOP-GUARD]\033[0m blocked repeated {_tl_sig} "
                                        f"(block_count={_tool_loop_guard.block_count})")
                            continue

                        # ── [BLOCK] PROJECT_MAP 强制: 写第3个源文件前必须有 PROJECT_MAP ──
                        _reject_result = _guard_tool_call(fn_name, fn_args, _pm_state,
                                                          _test_state, _misc_state, _force_writer_mode)
                        if _reject_result is not None:
                            tool_results.append({
                                "role": "tool", "tool_call_id": tc.get("id", ""),
                                "content": _reject_result,
                            })
                            _ctx.tracer.tool_call(name=fn_name, args=fn_args,
                                              result=_reject_result, elapsed=0, is_error=True)
                            continue

                        detail_str = f"{fn_name}: {_fmt_args(fn_name, fn_args)}"
                        # [FIX] 原日志截 100 字 → shell 多行命令看不全; 现在对 execute_shell
                        # 特殊处理: 完整命令多行缩进展示, 其他工具保留单行但放宽到 240c
                        _fmt_full = _fmt_args(fn_name, fn_args)
                        if fn_name == "execute_shell":
                            _cmd = fn_args.get("command", "") if isinstance(fn_args, dict) else ""
                            _bg  = fn_args.get("background", False) if isinstance(fn_args, dict) else False
                            _cmd_one = str(_cmd).replace("\n", " \\n ")[:500]
                            log.info(
                                f"  \033[35m⚙ {fn_name}\033[0m"
                                f"{' [bg]' if _bg else ''} {_cmd_one}"
                            )
                        else:
                            log.info(f"  \033[35m⚙ {fn_name}\033[0m {_fmt_full[:240]}")
                        yield sse_status(
                            "task_exec",
                            {"status": "executing", "detail": detail_str},
                            model
                        )

                        # ── [update_map] 内部工具: LLM 传结构化数据，系统写入 PROJECT_MAP.md (per-file sections) ──
                        if fn_name == "update_map":
                            result = _tool_update_map(fn_args, _pm_state)
                            diff = None

                        # spawn_agent 特殊处理：传入 sse_emit 回调
                        elif fn_name == "spawn_agent":
                            # [M7c] result 初值取 None 而非读 result: 原代码本分支从不读 result
                            # (各分派分支各自赋值), 首工具即 spawn 时读它会 UnboundLocalError
                            _sa_out = {"result": None, "effective_timeout": _effective_timeout}
                            async for _sa_chunk in _dispatch_spawn_agent(
                                    fn_args, _ctx, _sse_emit_to_buffer, _subagent_sse_queue, _sa_out):
                                yield _sa_chunk
                            result = _sa_out["result"]
                            _effective_timeout = _sa_out["effective_timeout"]
                            diff = None
                        else:
                            try:
                                # ── 心跳保活：长时间工具执行期间发送 SSE 注释防止连接超时 ──
                                _tool_task = asyncio.ensure_future(
                                    execute_tool(fn_name, fn_args, sid=_ctx.session_id)
                                )
                                _hb_interval = 15  # 每 15 秒一次心跳
                                while not _tool_task.done():
                                    try:
                                        await asyncio.wait_for(
                                            asyncio.shield(_tool_task), timeout=_hb_interval
                                        )
                                    except asyncio.TimeoutError:
                                        # 工具仍在执行，发送心跳
                                        yield f": heartbeat {fn_name}\n\n"
                                result, diff = _tool_task.result()
                            except Exception as _tool_exc:
                                result = f"ERROR: tool execution failed: {_tool_exc}"
                                diff = None

                        if diff:
                            yield sse_status(
                                "diff_view",
                                {"filepath": fn_args.get("filepath", ""), "diff": diff},
                                model
                            )

                        preview = result[:200].replace("\n", " ")
                        is_warn = "[STDERR_WARNINGS" in result and not result.startswith("ERROR")
                        is_err  = result.startswith("ERROR")
                        # [EXEC-GATE]/[READONLY] 按执行结果计数, 只在能*明确认定成功*时才计入
                        # (见 _write_call_confirmed_success 注释) —— 不是 `not is_err`。这个
                        # 独立信号只影响 exec-gate/readonly 的写入判定, 不改 is_err 本身, 所以
                        # error_streak / _enhance_tool_error / tracer.is_error / DAG 面板通知
                        # 等其余消费者的行为一律不受影响。
                        if fn_name in _WRITE_TOOL_NAMES and _write_call_confirmed_success(fn_name, result):
                            _iter_write_success = True
                        log.info(f"  \033[2m  ↳ {preview[:100]}\033[0m")
                        yield sse_status("task_exec", {
                            "status": "done",
                            "detail": ("[warn] " if is_warn else "") + preview
                        }, model)

                        # [P43-c] DAG/Timer 工具变更后通知前端刷新面板
                        if not is_err:
                            _dag_tools = {
                                "create_dag":     ("dag_changed",   "create"),
                                "update_dag":     ("dag_changed",   "update"),
                                "delete_dag":     ("dag_changed",   "delete"),
                                "patch_dag_node": ("dag_changed",   "update"),
                                "run_dag":        ("dag_changed",   "run"),
                                "create_timer":   ("timer_changed", "create"),
                                "update_timer":   ("timer_changed", "update"),
                                "delete_timer":   ("timer_changed", "delete"),
                                "run_timer_now":  ("timer_changed", "run"),
                            }
                            if fn_name in _dag_tools:
                                _ev, _op = _dag_tools[fn_name]
                                _name_or_id = fn_args.get("name") or fn_args.get("id") or ""
                                yield sse_meta(_ev, _op, _name_or_id)

                        # ── 错误注入强制继续 ──
                        if is_err:
                            error_streak += 1
                            # [OPT] 增强错误提示: 根据错误类型注入调试建议
                            result = _enhance_tool_error(fn_name, result, fn_args)
                            if error_streak < MAX_ERROR_STREAK:
                                result += (
                                    "\n\n[SYSTEM: 这是一个错误，不是终止信号。"
                                    "分析根因，制定新方案，继续执行任务的其余部分。"
                                    "禁止输出'无法继续'或等待用户指令。]"
                                )
                            else:
                                result += (
                                    f"\n\n[SYSTEM: 已连续 {error_streak} 次错误。"
                                    "可以向用户报告当前状况，说明已尝试的方案和建议。]"
                                )
                        else:
                            error_streak = 0

                        # ── Trace: 工具调用记录 ──
                        _ctx.tracer.tool_call(
                            name=fn_name, args=fn_args, result=result,
                            elapsed=time.time() - _usage.t0,  # 粗略
                            is_error=is_err,
                        )
                        # [OPT] P1-B: 如果检测到循环模式，注入策略变更提示
                        loop_hint = _ctx.tracer.get_loop_hint()
                        if loop_hint:
                            result += loop_hint

                        # [v1.0] 硬中断: 连续 3 次 LOGIC_LOOP 后强制打断
                        # 灵感来自 Claude Code: 不是提示, 直接干预执行流程
                        if _ctx.tracer.should_force_break():
                            log.warning(f"  [LOOP_BREAK] forcing strategy change after 3 consecutive loops")
                            # 清除最近重复的 tool results 减少 context 噪音
                            _recent_tool_ids = [
                                i for i, m in enumerate(_ctx.messages)
                                if m.get("role") == "tool"
                            ]
                            for _tid in _recent_tool_ids[-3:]:
                                if _tid < len(_ctx.messages):
                                    _old = _ctx.messages[_tid].get("content") or ""
                                    _ctx.messages[_tid]["content"] = f"[CLEARED: loop detected, {len(_old)}c removed]"
                            _ctx.tracer.reset_loop()
                            # 注入强制策略切换消息到 tool result
                            result = (
                                "[SYSTEM OVERRIDE] 检测到死循环, 已清除重复结果。"
                                "你必须立刻停止当前方法, 用完全不同的策略继续:\n"
                                "- 写作任务: 跳过当前章节, 写下一章\n"
                                "- 调试任务: 用 write_file 完全重写, 不要再 patch\n"
                                "- 搜索任务: 换用 web_search, 不要再 web_fetch 同一域名\n"
                                "- 后台服务启动失败: 停止 background=true, 改用前台运行查看报错: timeout 5 python3 main.py 2>&1\n"
                                "- 如果已超过 5 次尝试: 向用户报告当前状况, 继续后续步骤"
                            )
                        if fn_name == "spawn_agent":
                            _ctx.tracer.subagent_event(
                                fn_args.get("agent_type", "?"),
                                fn_args.get("task", "")[:100],
                                event="end", result=result[:200],
                            )

                        tool_results.append({
                            "role":         "tool",
                            "tool_call_id": tc["id"],
                            "content":      result
                        })
                        # [EXEC-GATE] task_done 证据提示: 记下位置, 等本批工具全部跑完、
                        # 拿到最终的 _iter_write_success 后再统一判定 (task_done 可能和写
                        # 工具同批出现且顺序不定, 写工具在 task_done 之后才执行的话, 判定这
                        # 一刻还不知道它会不会成功, 必须等整批结束才能下结论)。
                        if fn_name == "task_done":
                            _task_done_result_indices.append(len(tool_results) - 1)

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

                    # ── [TOOL-LOOP-BREAK] 累计拦截达阈值 → 终止本轮, 走正常收尾路径 ──
                    # [FIX 2026-08-26] SWE-bench 实测: guard 只拦不导致 iter=108 空转
                    # (拦截提示模型认不出/换个数字又试). block_count 是整个 turn 累计值
                    # (不是本 iter), 阈值到了直接 break 出主循环, 不硬跳 finally.
                    if _tool_loop_guard.block_count >= _tool_loop_break_threshold:
                        log.warning(
                            f"  \033[31m[TOOL-LOOP-BREAK:{_ctx.sid_tag}]\033[0m "
                            f"iter={_iteration} 累计拦截 {_tool_loop_guard.block_count} 次, 终止本轮"
                        )
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

                    # ── [PROJECT_MAP + TEST] 追踪写入文件 ──
                    _SRC_EXTS = {".py", ".js", ".ts", ".sh", ".bash", ".go", ".rs", ".java"}
                    _SKIP_NAMES = {"__init__", "config", "settings", "constants", "conftest",
                                   "setup", "requirements", ".gitkeep", "PROJECT_MAP"}
                    _TEST_PATTERNS = ("test_", "_test.", ".test.", ".spec.", "_spec.")
                    for _tc in tool_call_list:
                        if _tc["function"]["name"] == "write_file":
                            try:
                                import os as _os
                                _args = json.loads(_tc["function"].get("arguments") or "{}")
                                _fp = _args.get("filepath", "").replace("\\", "/")
                                _fname = _fp.split("/")[-1]
                                _ext = "." + _fname.rsplit(".", 1)[-1] if "." in _fname else ""
                                _is_test = any(p in _fname for p in _TEST_PATTERNS)
                                _is_skip = any(s in _fname for s in _SKIP_NAMES)

                                if _is_test:
                                    _test_state.test_files_written.add(_fp)
                                    _test_state.iter_wrote_test = True
                                elif _ext in _SRC_EXTS and not _is_skip:
                                    _test_state.src_files_written.append(_fp)
                                    _test_state.iter_wrote_src = True
                                    # 推断项目根目录: WORKSPACE 下第一级目录
                                    _ws = str(WORKSPACE).rstrip("/")
                                    if _fp.startswith(_ws + "/"):
                                        _rel = _fp[len(_ws)+1:]
                                        _top = _rel.split("/")[0]
                                        if _top:
                                            _pm_state.project_root = _ws + "/" + _top
                                    # py 计数 (PROJECT_MAP 阈值用)
                                    if _ext == ".py":
                                        _pm_state.py_files_written += 1
                                        _pm_state.files_since_map_update += 1

                                if "PROJECT_MAP" in _fp:
                                    _pm_state.project_map_written = True
                                    _pm_state.py_at_map_write = _pm_state.py_files_written
                                if _fname.lower() == "readme.md":
                                    _webtest_state.readme_written = True
                                # 追踪输出产物文件
                                _ARTIFACT_EXTS = {".docx", ".pptx", ".xlsx", ".pdf", ".csv", ".html", ".json", ".xml", ".svg"}
                                if _ext.lower() in _ARTIFACT_EXTS:
                                    _misc_state.output_artifacts.append(_fp)
                            except Exception:
                                pass

                        # ── [SHELL-BYPASS] 检测 execute_shell 中的 cat > / echo > 文件写入 ──
                        if _tc["function"]["name"] == "execute_shell":
                            try:
                                import re as _re_sh
                                _sh_args = json.loads(_tc["function"].get("arguments") or "{}")
                                _sh_cmd = _sh_args.get("command", "")
                                # 检测 cat > filepath, echo > filepath, tee filepath 等模式
                                _sh_writes = _re_sh.findall(
                                    r'(?:cat|tee|echo|printf)\s*>+\s*([^\s<|&;]+\.(?:py|js|ts|go|sh|rs|java))',
                                    _sh_cmd
                                )
                                for _sh_fp in _sh_writes:
                                    _sh_fname = _sh_fp.split("/")[-1]
                                    _sh_ext = "." + _sh_fname.rsplit(".", 1)[-1] if "." in _sh_fname else ""
                                    _sh_is_test = any(p in _sh_fname for p in _TEST_PATTERNS)
                                    _sh_is_skip = any(s in _sh_fname for s in _SKIP_NAMES)
                                    if _sh_is_test:
                                        _test_state.test_files_written.add(_sh_fp)
                                        _test_state.iter_wrote_test = True
                                        log.info(f"  [SHELL-TRACK] detected test write via shell: {_sh_fname}")
                                    elif _sh_ext in _SRC_EXTS and not _sh_is_skip:
                                        _test_state.src_files_written.append(_sh_fp)
                                        _test_state.iter_wrote_src = True
                                        if _sh_ext == ".py":
                                            _pm_state.py_files_written += 1
                                            _pm_state.files_since_map_update += 1
                                        log.info(f"  [SHELL-TRACK] detected src write via shell: {_sh_fname}")
                            except Exception:
                                pass

                    if (_pm_state.py_files_written >= 3 and not _pm_state.project_map_written and _pm_state.project_root):
                        import os as _os
                        _map_path = _pm_state.project_root + "/PROJECT_MAP.md"
                        _map_exists = _os.path.exists(_map_path)
                        if not _map_exists:
                            # ── [AUTO-MAP] 自动创建 PROJECT_MAP.md，不依赖 LLM ──
                            try:
                                _auto_content = _auto_map_build_skeleton(_test_state.src_files_written, _test_state.test_files_written, _pm_state.project_root)
                                with open(_map_path, "w", encoding="utf-8") as _amfp:
                                    _amfp.write(_auto_content)
                                _pm_state.project_map_written = True
                                _pm_state.py_at_map_write = _pm_state.py_files_written
                                # 通知 LLM
                                _pm_hint = f"\n\n[SYSTEM-AUTO-MAP] 已自动创建 {_map_path} 骨架（含测试覆盖+测试策略）。请为已写入的每个源文件调用 update_map 工具填充路由/函数/参数/类型信息（先 read_file 再 update_map）。"
                                if tool_results:
                                    tool_results[-1]["content"] += _pm_hint
                                    if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                                        _ctx.messages[-1]["content"] += _pm_hint
                                log.info(f"  [AUTO-MAP] created {_map_path} with {len(_test_state.src_files_written)} modules")
                            except Exception as _ame:
                                log.warning(f"  [AUTO-MAP] failed to create: {_ame}")
                                # 降级: 仍然提醒 LLM 创建
                                _pm_hint = (
                                    f"\n\n[SYSTEM: 你已写入 {_pm_state.py_files_written} 个Python模块但尚未创建 PROJECT_MAP.md。"
                                    f"\n下一步必须先创建 {_map_path}，包含: API合约表、函数索引、测试覆盖状态。]"
                                )
                                if tool_results:
                                    tool_results[-1]["content"] += _pm_hint
                                    if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                                        _ctx.messages[-1]["content"] += _pm_hint

                    # ── [AUTO-MAP] 每写源文件 → 自动追加测试覆盖行 + 注入提示让 LLM 调用 update_map ──
                    if _pm_state.project_map_written and _pm_state.project_root and _test_state.iter_wrote_src:
                        import os as _os4
                        _map_path2 = _pm_state.project_root + "/PROJECT_MAP.md"
                        if _os4.path.exists(_map_path2):
                            _new_this_iter = [
                                f for f in _test_state.src_files_written[-3:]
                                if f not in _pm_state.map_reminded_files
                            ]
                            _um_files_to_register = []
                            for _new_fn in _new_this_iter:
                                _new_basename = _new_fn.split("/")[-1]
                                _new_relpath = _new_fn.replace(_pm_state.project_root + "/", "")
                                _new_modname = _new_basename.rsplit(".", 1)[0]
                                _test_status = "有" if any(
                                    _new_modname in tf for tf in _test_state.test_files_written
                                ) else "缺"

                                # 只自动追加测试覆盖行（简单确定性操作）
                                try:
                                    _map_content = open(_map_path2, "r", encoding="utf-8").read()
                                    if _new_relpath not in _map_content and _new_basename not in _map_content:
                                        if "## 测试覆盖" in _map_content:
                                            _map_lines2 = _map_content.split("\n")
                                            _in_test_table = False
                                            _insert_pos2 = -1
                                            for _li2, _ll2 in enumerate(_map_lines2):
                                                if "## 测试覆盖" in _ll2:
                                                    _in_test_table = True
                                                elif _in_test_table and _ll2.startswith("|"):
                                                    _insert_pos2 = _li2 + 1
                                                elif _in_test_table and not _ll2.startswith("|") and _ll2.strip():
                                                    _in_test_table = False
                                            if _insert_pos2 > 0:
                                                _map_lines2.insert(_insert_pos2, f"| {_new_basename} | tests/test_{_new_basename} | {_test_status} |")
                                                with open(_map_path2, "w", encoding="utf-8") as _mwf:
                                                    _mwf.write("\n".join(_map_lines2))
                                        _um_files_to_register.append(_new_relpath)
                                        log.info(f"  [AUTO-MAP] appended test coverage for {_new_basename}, pending update_map")
                                except Exception as _mae:
                                    log.warning(f"  [AUTO-MAP] test-coverage append failed for {_new_basename}: {_mae}")

                                _pm_state.map_reminded_files.add(_new_fn)

                            # 注入提示让 LLM 调用 update_map 填充路由/函数/参数信息
                            if _um_files_to_register:
                                _um_file_list = ", ".join(_um_files_to_register)
                                if _pm_state.files_since_map_update >= 2:
                                    # 强制模式: 必须先 update_map
                                    _um_hint = (
                                        f"\n\n[SYSTEM-BLOCK-MAP] ⚠️ 已写入 {_pm_state.files_since_map_update} 个源文件但 PROJECT_MAP.md 仍为空，下次 write_file 将被拦截！"
                                        f"立即对每个已写文件执行: read_file → update_map。"
                                        f"update_map 质量标准(参考级): "
                                        f"file_description(文件一句话用途) + "
                                        f"functions(每个函数: line_no行号/name/params含类型/returns/note用途, 不漏任何函数) + "
                                        f"classes(line_no/name/bases/methods逗号分隔) + "
                                        f"constants(所有模块级常量逗号分隔) + "
                                        f"routes(method/path/handler/params/note) + "
                                        f"deps(imports/db_tables/ext_apis/used_by) + "
                                        f"deps_graph(ASCII依赖图) + core_flow(ASCII流程图) + "
                                        f"config + progress + tests。"
                                        f"剧本/小说加: scenes + characters。"
                                        f"待注册: {_um_file_list}"
                                    )
                                else:
                                    _um_hint = (
                                        f"\n\n[SYSTEM-MAP-UPDATE] 新源文件已写入: {_um_file_list}。"
                                        f"请 read_file 后调用 update_map，填写参考级详情: "
                                        f"file_description + functions(每个函数: line_no/name/params含类型/returns/note, 不可漏) + "
                                        f"classes(line_no/name/bases/methods) + constants(所有常量) + "
                                        f"deps(imports/used_by) + deps_graph(ASCII依赖图) + progress。"
                                    )
                                if tool_results:
                                    tool_results[-1]["content"] += _um_hint
                                    if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                                        _ctx.messages[-1]["content"] += _um_hint

                    # ── [TEST] 强制注入: 本轮写了源文件但没写测试 → 独立 user 消息 ──
                    if _test_state.iter_wrote_src and not _test_state.iter_wrote_test:
                        import os as _os3
                        _all_untested = []
                        # 构建已测试模块集合
                        _tested_mods2 = set()
                        for _tf2 in _test_state.test_files_written:
                            _tfbn2 = _tf2.split("/")[-1].rsplit(".", 1)[0]
                            if _tfbn2.startswith("test_"):
                                _tested_mods2.add(_tfbn2[5:])
                            try:
                                import re as _re2
                                _tfc2 = open(_tf2).read(5000)
                                for _m2 in _re2.findall(r'def test_(\w+)', _tfc2):
                                    _tested_mods2.add(_m2)
                                for _m2 in _re2.findall(r'from\s+\S+\.(\w+)\s+import', _tfc2):
                                    _tested_mods2.add(_m2)
                            except Exception:
                                pass
                        if _pm_state.project_root:
                            _tdir2 = _pm_state.project_root + "/tests"
                            if _os3.path.isdir(_tdir2):
                                for _tdf2 in _os3.listdir(_tdir2):
                                    if _tdf2.startswith("test_") and _tdf2.endswith((".py",".js",".ts",".sh",".go")):
                                        _tested_mods2.add(_tdf2.split("test_",1)[1].rsplit(".",1)[0])

                        for _sf in _test_state.src_files_written:
                            _sfname = _sf.split("/")[-1].rsplit(".", 1)[0]
                            _sfext  = "." + _sf.rsplit(".", 1)[-1] if "." in _sf else ".py"
                            _sfdir  = _sf.rsplit("/", 1)[0]
                            # [FIX 2026-08-31] 三个缺陷:
                            #   1) 只认 test_ 前缀 / .test. 中缀, 不认 Go 原生的 {name}_test.go 后缀
                            #      → Go 项目写了测试也永远判定"无测试", 每轮注入一条假警告带偏 agent
                            #   2) 测试文件自身被当成"待测源文件", 要求给测试写测试
                            #   3) probe/tmp/scratch 这类一次性调研脚本被计入
                            if (_sfname.endswith("_test") or _sfname.startswith("test_")
                                    or ".test" in _sfname or ".spec" in _sfname
                                    or _sfname.endswith("Test")):
                                continue                       # 本身就是测试, 跳过
                            if _sfname.split(".")[0].lower().startswith(("probe", "tmp", "scratch", "_")):
                                continue                       # 一次性调研脚本, 不要求测试
                            _has_test = (
                                _sfname in _tested_mods2
                                or _os3.path.exists(f"{_sfdir}/{_sfname}_test{_sfext}")      # Go / Python 后缀式
                                or _os3.path.exists(f"{_sfdir}/test_{_sfname}{_sfext}")      # Python / shell 前缀式
                                or _os3.path.exists(f"{_sfdir}/{_sfname}.test{_sfext}")      # JS
                                or _os3.path.exists(f"{_sfdir}/{_sfname}.spec{_sfext}")      # TS
                                or _os3.path.exists(f"{_sfdir}/{_sfname.capitalize()}Test.java")  # Java
                                or _os3.path.exists(f"{_pm_state.project_root}/tests/test_{_sfname}{_sfext}")
                                or _os3.path.exists(f"{_pm_state.project_root}/tests/{_sfname}_test{_sfext}")
                            )
                            if not _has_test:
                                _all_untested.append((_sf, _sfext))
                        if _all_untested:
                            _ext_guide = {
                                ".py":  ("tests/test_{name}.py",
                                         "import pytest\ndef test_happy(): ...\ndef test_error(): ..."),
                                ".js":  ("{name}.test.js",
                                         "const assert=require('assert');\nassert.strictEqual(func(), expected);"),
                                ".ts":  ("{name}.spec.ts",
                                         "describe('{name}',()=>{{ it('works',()=>{{ expect(...).toBe(...); }}); }});"),
                                ".sh":  ("tests/test_{name}.sh",
                                         "#!/usr/bin/env bash\nset -euo pipefail\nassert_eq(){{[ \"$1\"=\"$2\" ]||{{echo FAIL;exit 1;}}}}"),
                                ".go":  ("{name}_test.go",
                                         "func TestHappy(t *testing.T){{\n  got:=Func(input)\n  if got!=want{{t.Errorf(...)}}\n}}"),
                            }
                            # [FIX 2026-08-31] 原先模板取 _all_untested[-1] 的扩展名, 文件名取 [0] 的名字,
                            # 两个不同文件拼一起 → 出现 "tests/test_trigram.sh" (shell 模板 + Go 文件名)。
                            # 现统一取同一项。
                            _recent_sf, _recent_ext = _all_untested[0]
                            _tpl_name, _tpl_body = _ext_guide.get(_recent_ext, ("tests/test_{name}.py","import pytest"))
                            _untested_names = [sf.split("/")[-1] for sf,_ in _all_untested[:5]]
                            _untested_list = "\n".join(f"  - {n}" for n in _untested_names)
                            _test_dirs = list({sf.rsplit("/",1)[0] for sf,_ in _all_untested[:3]})
                            # 模板不含 "/" 说明该语言测试与源码同目录 (Go/JS/TS/Java), 不该建 tests/ 根目录
                            if "/" in _tpl_name:
                                _tests_root = (_pm_state.project_root + "/tests") if _pm_state.project_root else _test_dirs[0]
                            else:
                                _tests_root = _recent_sf.rsplit("/", 1)[0]
                            _guard_stats.hit("SYSTEM-TEST")
                            _test_inject = (
                                f"[SYSTEM-TEST] ⚠ 已写 {len(_test_state.src_files_written)} 个源文件，但 {len(_all_untested)} 个没有测试:\n"
                                f"{_untested_list}\n\n"
                                f"下一步必须写测试（否则此提醒每轮重复）:\n"
                                f"  mkdir -p {_tests_root}\n"
                                f"  文件: {_tpl_name.replace('{name}', _recent_sf.split('/')[-1].rsplit('.',1)[0])}\n"
                                f"  模板:\n    {_tpl_body}\n\n"
                                f"写完测试后必须立即执行验证（不能先写下一个文件）。\n"
                                f"所有源文件写完后，最终做端到端验证（Web项目curl路由/文档生成验证产物/数据处理验证输出）。"
                            )
                            # 追加到最后一个 tool result（agent 紧接着就能看到）
                            if tool_results:
                                tool_results[-1]["content"] += "\n\n" + _test_inject
                                if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                                    _ctx.messages[-1]["content"] += "\n\n" + _test_inject
                            # 同时作为独立 user 消息（双重保险）
                            _ctx.messages.append({"role": "user", "content": _test_inject})
                            _ctx.new_msgs.append({"role": "user", "content": _test_inject})
                            log.info(f"  [TEST] injected: {_untested_names[:3]}")

                    # ── [RUN-TEST] 本轮写了测试/验证文件 → 立即执行 ──
                    if _test_state.iter_wrote_test and _pm_state.project_root:
                        _new_tests = [
                            tf for tf in _test_state.test_files_written
                            if tf not in _test_state.tests_run_for
                            and any(tf in str(tr.get("content","")) for tr in tool_results)
                        ]
                        if not _new_tests:
                            _new_tests = [tf for tf in sorted(_test_state.test_files_written) if tf not in _test_state.tests_run_for][-1:]
                        if _new_tests:
                            _test_file = _new_tests[-1]
                            _test_rel = _test_file.replace(_pm_state.project_root + "/", "")
                            _test_ext = "." + _test_file.rsplit(".", 1)[-1] if "." in _test_file else ".py"
                            _test_state.tests_run_for.add(_test_file)

                            # 根据文件扩展名生成对应的执行命令
                            _run_cmds = {
                                ".py":   f"cd {_pm_state.project_root} && python3 -m pytest {_test_rel} -v --tb=short 2>&1 | head -80",
                                ".js":   f"cd {_pm_state.project_root} && node {_test_rel} 2>&1 | head -80 || npx jest {_test_rel} --no-coverage 2>&1 | head -80",
                                ".ts":   f"cd {_pm_state.project_root} && npx jest {_test_rel} --no-coverage 2>&1 | head -80 || npx ts-node {_test_rel} 2>&1 | head -80",
                                ".go":   f"cd {_pm_state.project_root}/{'/'.join(_test_rel.split('/')[:-1]) or '.'} && go test -v -run . 2>&1 | head -80",
                                ".sh":   f"cd {_pm_state.project_root} && bash {_test_rel} 2>&1 | head -80",
                                ".bash": f"cd {_pm_state.project_root} && bash {_test_rel} 2>&1 | head -80",
                                ".rs":   f"cd {_pm_state.project_root} && cargo test 2>&1 | head -80",
                                ".java": f"cd {_pm_state.project_root} && javac {_test_rel} && java -cp . {_test_rel.replace('.java','')} 2>&1 | head -80",
                            }
                            _run_cmd = _run_cmds.get(_test_ext, f"cd {_pm_state.project_root} && python3 -m pytest {_test_rel} -v --tb=short 2>&1 | head -80")

                            _run_inject = (
                                f"[SYSTEM-RUN-TEST] ⚠ 刚写完测试 {_test_rel}，必须立即执行验证:\n"
                                f"  {_run_cmd}\n\n"
                                f"执行后根据结果:\n"
                                f"  - 全部 PASSED → 继续下一步\n"
                                f"  - 有 FAILED/ERROR → 分析原因，修复代码或测试，重跑直到通过\n"
                                f"⚠ 禁止跳过测试执行直接写下一个文件。"
                            )
                            if tool_results:
                                tool_results[-1]["content"] += "\n\n" + _run_inject
                                if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                                    _ctx.messages[-1]["content"] += "\n\n" + _run_inject
                            log.info(f"  [RUN-TEST] injected run for {_test_rel}")
                            _misc_state.system_injected = True  # 下轮跳过enforce

                    # ── [AUTO-MAP-COVERAGE] 写完测试后自动更新 PROJECT_MAP 测试覆盖状态 ──
                    if _test_state.iter_wrote_test and _pm_state.project_map_written and _pm_state.project_root:
                        _cov_map_path = _pm_state.project_root + "/PROJECT_MAP.md"
                        try:
                            import os as _osc, re as _rec
                            if _osc.path.exists(_cov_map_path):
                                _cov_content = open(_cov_map_path, "r", encoding="utf-8").read()
                                if "## 测试覆盖" in _cov_content and "| 缺 |" in _cov_content:
                                    # 重新扫描所有测试文件确定覆盖的模块
                                    _covered = set()
                                    for _ctf in _test_state.test_files_written:
                                        _ctbn = _ctf.split("/")[-1].rsplit(".", 1)[0]
                                        if _ctbn.startswith("test_"):
                                            _covered.add(_ctbn[5:])
                                        try:
                                            _ctc = open(_ctf).read(5000)
                                            for _cm in _rec.findall(r'def test_(\w+)', _ctc):
                                                _covered.add(_cm)
                                            for _cm in _rec.findall(r'from\s+\S+\.(\w+)\s+import', _ctc):
                                                _covered.add(_cm)
                                        except Exception:
                                            pass
                                    # 也扫描磁盘 tests/ 目录
                                    _ctd = _pm_state.project_root + "/tests"
                                    if _osc.path.isdir(_ctd):
                                        for _ctdf in _osc.listdir(_ctd):
                                            if _ctdf.startswith("test_"):
                                                _covered.add(_ctdf.split("test_", 1)[1].rsplit(".", 1)[0])

                                    # 替换: 对每个已覆盖的模块，将 "| 缺 |" 改为 "| 有 |"
                                    _updated = False
                                    for _cmod in _covered:
                                        _old_pat = f"| {_cmod}.py |"
                                        if _old_pat in _cov_content:
                                            # 只替换该行中的 "| 缺 |" 为 "| 有 |"
                                            _lines_c = _cov_content.split("\n")
                                            for _ci, _cl in enumerate(_lines_c):
                                                if _old_pat in _cl and "| 缺 |" in _cl:
                                                    _lines_c[_ci] = _cl.replace("| 缺 |", "| 有 |")
                                                    _updated = True
                                            _cov_content = "\n".join(_lines_c)

                                    if _updated:
                                        with open(_cov_map_path, "w", encoding="utf-8") as _cwf:
                                            _cwf.write(_cov_content)
                                        log.info(f"  [AUTO-MAP-COVERAGE] updated test coverage for: {_covered & set(f.split('/')[-1].rsplit('.',1)[0] for f in _test_state.src_files_written)}")
                        except Exception as _cove:
                            log.warning(f"  [AUTO-MAP-COVERAGE] failed: {_cove}")

                    # ── [FINAL-VALIDATE] 项目收尾: 检测项目类型 → 生成对应最终验证 ──
                    # 触发条件: README已写 OR (>=8个源文件且70%有测试 且迭代>=30 且有入口文件)
                    _has_entry = _pm_state.project_root and any(
                        __import__('os').path.exists(f"{_pm_state.project_root}/{e}")
                        for e in ("run.py", "main.py", "app.py", "manage.py", "server.py", "index.js", "main.go")
                    )
                    _should_final_validate = (
                        _webtest_state.readme_written
                        or (len(_test_state.src_files_written) >= 8
                            and _iteration >= 30
                            and len(_test_state.test_files_written) >= len(_test_state.src_files_written) * 0.7
                            and _has_entry)
                    )
                    if _should_final_validate and not _webtest_state.web_test_injected and _pm_state.project_root:
                        import os as _os5
                        # 检查是否还有大量未测试文件
                        _all_ut = []
                        for _sf2 in _test_state.src_files_written:
                            _sn2 = _sf2.split("/")[-1].rsplit(".", 1)[0]
                            if not any(_sn2 in tf or f"test_{_sn2}" in tf for tf in _test_state.test_files_written):
                                _all_ut.append(_sf2)
                        if len(_all_ut) <= 2:
                            _webtest_state.web_test_injected = True

                            # ── 检测项目类型 ──
                            _all_files_str = " ".join(_test_state.src_files_written).lower()
                            _all_exts = set(f.rsplit(".",1)[-1].lower() for f in _test_state.src_files_written if "." in f)
                            _artifact_exts = set(f.rsplit(".",1)[-1].lower() for f in _misc_state.output_artifacts if "." in f)
                            # 读取入口文件的前几行来检测框架
                            _framework_hints = ""
                            for _ef in ["run.py", "main.py", "app.py", "manage.py", "server.py", "index.js", "app.js", "main.go", "main.rs"]:
                                _ef_path = _os5.path.join(_pm_state.project_root, _ef)
                                if _os5.path.exists(_ef_path):
                                    try:
                                        _framework_hints += open(_ef_path).read(2000)
                                    except Exception:
                                        pass
                            # 也扫描 __init__.py
                            for _root_d, _dirs, _fnames in _os5.walk(_pm_state.project_root):
                                for _fn in _fnames:
                                    if _fn in ("__init__.py", "app.py") and _root_d.count("/") - _pm_state.project_root.count("/") <= 2:
                                        try:
                                            _framework_hints += open(_os5.path.join(_root_d, _fn)).read(1500)
                                        except Exception:
                                            pass
                                break  # 只扫1层

                            _is_web = any(kw in _framework_hints.lower() for kw in
                                ["flask", "fastapi", "django", "express", "koa", "gin", "actix", "rocket",
                                 "http.server", "uvicorn", "gunicorn", "app.run", "createserver", "listen("])
                            _is_docgen = bool(_artifact_exts & {"docx", "pptx", "xlsx", "pdf"}) or \
                                any(kw in _all_files_str for kw in ["docx", "pptx", "xlsx", "openpyxl", "python-docx", "python-pptx"])
                            _is_data = bool(_artifact_exts & {"csv", "json", "xml", "parquet"}) or \
                                any(kw in _all_files_str for kw in ["pandas", "csv", "etl", "pipeline", "transform", "scraper", "crawler"])

                            # ── 生成对应验证步骤 ──
                            _validate_steps = []

                            # 通用: 先跑全量测试
                            if "py" in _all_exts:
                                _validate_steps.append(f"cd {_pm_state.project_root} && python3 -m pytest tests/ -v --tb=short 2>&1 | tail -40")
                            elif "js" in _all_exts or "ts" in _all_exts:
                                _validate_steps.append(f"cd {_pm_state.project_root} && npx jest --no-coverage 2>&1 | tail -40 || npm test 2>&1 | tail -40")
                            elif "go" in _all_exts:
                                _validate_steps.append(f"cd {_pm_state.project_root} && go test ./... -v 2>&1 | tail -40")
                            elif "sh" in _all_exts or "bash" in _all_exts:
                                _validate_steps.append(f"cd {_pm_state.project_root} && bash tests/*.sh 2>&1 | tail -40")

                            if _is_web:
                                # Web 项目: 启动服务 + curl
                                _entry = "run.py"
                                for _candidate in ["run.py", "main.py", "app.py", "manage.py", "server.py"]:
                                    if _os5.path.exists(_os5.path.join(_pm_state.project_root, _candidate)):
                                        _entry = _candidate
                                        break
                                _validate_steps.extend([
                                    f"# 启动服务 (后台, 15秒超时)",
                                    f"cd {_pm_state.project_root} && timeout 15 python3 {_entry} &",
                                    f"sleep 3",
                                    f"# curl 测试每个关键路由 (根据 PROJECT_MAP 中的 API 合约):",
                                    f"curl -sS http://127.0.0.1:5000/ -o /dev/null -w '%{{http_code}}\\n'",
                                    f"curl -sS http://127.0.0.1:5000/login -o /dev/null -w '%{{http_code}}\\n'",
                                    f"# 补充更多路由...",
                                    f"kill %1 2>/dev/null || true",
                                ])

                            if _is_docgen:
                                # 文档生成: 验证输出文件存在且可读
                                _validate_steps.extend([
                                    f"# 文档生成验证:",
                                    f"cd {_pm_state.project_root}",
                                    f"# 运行生成脚本 (如果有入口):",
                                    f"# python3 main.py 或 python3 generate.py",
                                    f"# 验证输出文件:",
                                ])
                                if _artifact_exts & {"docx"}:
                                    _validate_steps.append(
                                        f"python3 -c \"from docx import Document; d=Document('输出文件.docx'); "
                                        f"print(f'段落数: {{len(d.paragraphs)}}, 表格数: {{len(d.tables)}}'); "
                                        f"assert len(d.paragraphs)>0, '文档为空'\""
                                    )
                                if _artifact_exts & {"pptx"}:
                                    _validate_steps.append(
                                        f"python3 -c \"from pptx import Presentation; p=Presentation('输出文件.pptx'); "
                                        f"print(f'幻灯片数: {{len(p.slides)}}'); "
                                        f"assert len(p.slides)>0, '演示文稿为空'\""
                                    )
                                if _artifact_exts & {"xlsx"}:
                                    _validate_steps.append(
                                        f"python3 -c \"import openpyxl; wb=openpyxl.load_workbook('输出文件.xlsx'); "
                                        f"print(f'工作表: {{wb.sheetnames}}, 行数: {{wb.active.max_row}}'); "
                                        f"assert wb.active.max_row>0, '表格为空'\""
                                    )
                                if _artifact_exts & {"pdf"}:
                                    _validate_steps.append(
                                        f"python3 -c \"import subprocess; r=subprocess.run(['pdfinfo','输出文件.pdf'],capture_output=True,text=True); "
                                        f"print(r.stdout); assert 'Pages' in r.stdout, 'PDF无效'\" "
                                        f"|| python3 -c \"import fitz; d=fitz.open('输出文件.pdf'); print(f'页数: {{d.page_count}}'); assert d.page_count>0\""
                                    )
                                _validate_steps.append(
                                    f"# ⚠ 将上面的 '输出文件.xxx' 替换为实际文件路径"
                                )

                            if _is_data:
                                # 数据处理: 验证输出数据
                                _validate_steps.extend([
                                    f"# 数据验证:",
                                    f"cd {_pm_state.project_root}",
                                    f"# 运行数据处理脚本:",
                                    f"# python3 main.py",
                                ])
                                if _artifact_exts & {"csv"}:
                                    _validate_steps.append(
                                        f"python3 -c \"import csv; r=list(csv.reader(open('输出.csv'))); "
                                        f"print(f'行数: {{len(r)}}, 列数: {{len(r[0]) if r else 0}}'); assert len(r)>1, '数据为空'\""
                                    )
                                if _artifact_exts & {"json"}:
                                    _validate_steps.append(
                                        f"python3 -c \"import json; d=json.load(open('输出.json')); "
                                        f"print(f'类型: {{type(d).__name__}}, 条目: {{len(d) if isinstance(d,(list,dict)) else 1}}'); "
                                        f"assert d, '数据为空'\""
                                    )
                                _validate_steps.append(
                                    f"# ⚠ 将上面的 '输出.xxx' 替换为实际文件路径"
                                )

                            if not _is_web and not _is_docgen and not _is_data:
                                # 通用 CLI/库项目: 运行入口验证
                                _validate_steps.extend([
                                    f"# 运行入口验证:",
                                    f"cd {_pm_state.project_root} && python3 run.py --help 2>&1 | head -20 || python3 main.py --help 2>&1 | head -20 || true",
                                ])

                            _steps_text = "\n  ".join(_validate_steps)
                            _project_type = ("Web应用" if _is_web else
                                            "文档生成" if _is_docgen else
                                            "数据处理" if _is_data else
                                            "通用项目")
                            _web_inject = (
                                f"[SYSTEM-FINAL-VALIDATE] ✅ 代码和测试基本完成。检测到项目类型: {_project_type}\n"
                                f"最后一步: 端到端验证。必须按顺序执行:\n"
                                f"  {_steps_text}\n\n"
                                f"执行后根据结果:\n"
                                f"  - 全部通过 → 输出最终总结\n"
                                f"  - 发现问题 → 修复 → 重新验证，直到通过\n\n"
                                f"⚠ 禁止跳过最终验证直接输出总结。"
                            )
                            if tool_results:
                                tool_results[-1]["content"] += "\n\n" + _web_inject
                                if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                                    _ctx.messages[-1]["content"] += "\n\n" + _web_inject
                            _ctx.messages.append({"role": "user", "content": _web_inject})
                            _ctx.new_msgs.append({"role": "user", "content": _web_inject})
                            log.info(f"  [FINAL-VALIDATE] injected {_project_type} validation")

                    # ── [BATCH] 检测连续单文件写入，注入批量写入提醒 ──
                    _write_count_this_iter = sum(
                        1 for _tc2 in tool_call_list
                        if _tc2["function"]["name"] == "write_file"
                    )
                    if _write_count_this_iter == 1 and len(tool_call_list) == 1:
                        _batch_state.consecutive_single_writes += 1
                    else:
                        _batch_state.consecutive_single_writes = 0

                    if _batch_state.consecutive_single_writes >= 3 and not _batch_state.batch_reminder_sent:
                        _batch_state.batch_reminder_sent = True
                        _guard_stats.hit("SYSTEM-BATCH")
                        _batch_inject = (
                            "[SYSTEM-BATCH] ⚠ 检测到连续 3 轮每轮只写 1 个文件，效率极低。\n"
                            "下一轮必须批量写入所有待写文件（一轮调用多个 write_file）。\n"
                            "独立的文件写入必须在同一轮批量完成，不要一个一个写。\n"
                            "示例: 一轮内同时 write_file(a.py) + write_file(b.py) + write_file(c.py)"
                        )
                        if tool_results:
                            tool_results[-1]["content"] += "\n\n" + _batch_inject
                            if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                                _ctx.messages[-1]["content"] += "\n\n" + _batch_inject
                        log.info(f"  [BATCH] injected batch write reminder after {_batch_state.consecutive_single_writes} consecutive single writes")
                        # 重置计数但保留标记，下次连续 5 轮再提醒
                        _batch_state.consecutive_single_writes = 0
                        _batch_state.batch_reminder_sent = False  # 允许重复提醒

                    # ── [READONLY] 连续只读轮检测: N 轮只调只读工具零写入 → 注入推进提醒 (不熔断) ──
                    # 信号源改为按执行结果算的 _iter_write_success (循环体里已算好, 与
                    # [EXEC-GATE] 共用同一个信号, 见 L1778-1779), 只换信号来源, 阈值/提醒
                    # 次数等既有行为不变。
                    _readonly_threshold = int(_AGT.get("readonly_streak_threshold", 10))
                    _readonly_reminder_max = int(_AGT.get("readonly_reminder_max", 3))
                    if _batch_state.readonly_tick(_iter_write_success, _readonly_threshold, _readonly_reminder_max):
                        _guard_stats.hit("SYSTEM-READONLY")
                        _readonly_inject = (
                            f"[SYSTEM-READONLY] 你已经连续 {_readonly_threshold} 轮只在查看和验证，没有做出任何实际修改。\n"
                            f"如果资料已经足够: 现在就用 {_WRITE_TOOL_NAMES_STR} 修改目标文件"
                            "（注意改的是任务要求的那个目录/仓库里的文件，不是 /tmp 下的临时脚本）。\n"
                            "如果确实还缺关键信息: 直接说明还缺什么，不要继续漫无目的地翻找。\n"
                            "如果这个任务本来就只需要查阅和分析、不需要改任何代码"
                            "（例如纯调研/架构梳理/答疑）: 那就直接给出结论，不要为了迎合本提醒而做多余的修改。"
                        )
                        _ctx.messages.append({"role": "user", "content": _readonly_inject})
                        _ctx.new_msgs.append({"role": "user", "content": _readonly_inject})
                        log.info(
                            f"  [READONLY] injected readonly-streak reminder "
                            f"#{_batch_state.readonly_reminder_count}/{_readonly_reminder_max} "
                            f"(threshold={_readonly_threshold})"
                        )

                    # ── [EXEC-GATE] 累计本轮*成功*写类工具调用次数, 供收尾闸 (exec_gate_tick) 判断 ──
                    if _iter_write_success:
                        _batch_state.exec_gate_write_calls += 1

                    # ── [PLAN-GATE] 轨迹触发 (修复轮 #2: 不判文本, 观察行为) ──
                    # 本轮(有工具调用的这一轮, 见 plan_gate_tick docstring)已累计的"带工具调用的
                    # 迭代轮数"达到阈值、且全程从未调过 task_create → 说明它就是在做一个需要多步
                    # 的任务却没有拆解, 注入一次拆解提醒然后永久放行 (plan_gate_tick 内部保证
                    # 最多返回 True 一次, 不重复提醒, 不死锁)。
                    _plan_gate_task_created_now = any(
                        _tc6["function"]["name"] == "task_create" for _tc6 in tool_call_list
                    )
                    if _batch_state.plan_gate_tick(
                        _plan_gate_task_created_now, threshold=int(_AGT.get("plan_gate_tool_iters", 5))
                    ):
                        _guard_stats.hit("SYSTEM-PLAN-GATE")
                        _plan_gate_inject = (
                            f"[SYSTEM-PLAN-GATE] 你已经连续 {_batch_state.plan_gate_tool_call_iters} 轮调用工具还没完成这个任务。\n"
                            "先用 task_create 把剩下的工作拆成 3-8 个具体步骤, 每步要有明确产出物或可验证的完成标志, "
                            "单步一两次工具调用就能完成。拆完按步骤逐个推进, 每完成一步用 task_done 标记。"
                        )
                        _ctx.messages.append({"role": "user", "content": _plan_gate_inject})
                        _ctx.new_msgs.append({"role": "user", "content": _plan_gate_inject})
                        log.info(
                            f"  \033[35m[PLAN-GATE]\033[0m [{_ctx.sid_tag}] 连续 "
                            f"{_batch_state.plan_gate_tool_call_iters} 轮工具调用未见 task_create, "
                            f"注入拆解提醒 (iter={_iteration})"
                        )

                    # 重置本轮标志
                    _test_state.iter_wrote_src = False
                    _test_state.iter_wrote_test = False

                    # [v1.0] 任何工具调用 = 模型有产出 → 清零空回复连击计数
                    _consecutive_empty = 0

                    _ctx.tracer.iteration_end("ITER_TOOL_CALL", f"{len(tool_results)} results")
                    continue

                else:
                    _stripped_text = (full_text or "").strip()
                    if _stripped_text:
                        _ctx.tracer.llm_output(text=full_text, tool_calls=[], tokens_est_out=len(full_text)//CHARS_PER_TOKEN)

                        # [v1.0] PLAN 自动续行: 输出了 [PLAN] 但没调工具, 不退出, 注入续行消息
                        # 解决: LLM 输出大纲说 "下一轮用 spawn_agent" 但 agent loop 已 break 的 bug
                        if "[PLAN]" in _stripped_text and _iteration < _effective_max_iter - 1:
                            log.info(f"  [auto-continue] PLAN detected without tool calls, injecting continuation")
                            _ctx.tracer.iteration_end("ITER_OK", "PLAN auto-continue")
                            _ctx.new_msgs.append({"role": "assistant", "content": full_text})
                            _ctx.messages.append({"role": "assistant", "content": full_text})
                            _ctx.messages.append({
                                "role": "user",
                                "content": (
                                    "[SYSTEM: 你已输出规划。现在立刻开始执行第一步。"
                                    "如果规划中提到 spawn_agent, 现在就调用它。"
                                    "不要重复规划内容, 直接开始执行。]"
                                ),
                            })
                            _consecutive_empty = 0  # [v1.0] 有产出, 清零
                            continue  # 不 break, 继续 agent loop

                        # ── [EXEC-GATE] 收尾闸: 本轮此刻无 tool_call、模型即将给出最终回复。
                        # 若本回合已经有过 ≥N 轮工具调用、却从未调用过写类工具 → 说明规划/验证
                        # 都做了唯独没动手改, 注入一次"最后机会"提示, 不 break, 再走一轮;
                        # exec_gate_tick 内部保证最多触发一次, 不会死锁、也不会硬拦回合结束 ──
                        if _batch_state.exec_gate_tick(
                            _batch_state.plan_gate_tool_call_iters,
                            min_tool_iters=int(_AGT.get("exec_gate_min_tool_iters", 4)),
                        ):
                            _exec_gate_inject = (
                                "[SYSTEM-EXEC-GATE] 你即将结束这一轮，但本回合从未成功调用过任何写文件工具"
                                f"({_WRITE_TOOL_NAMES_STR})，磁盘上没有任何改动。\n"
                                "如果你的计划里还有修改/创建文件这类没做的步骤，现在就做。\n"
                                "如果这个任务本来就不需要改文件（纯查询/分析/答疑），忽略这条，直接给出最终答案。"
                            )
                            log.info(
                                f"  \033[35m[EXEC-GATE]\033[0m [{_ctx.sid_tag}] 连续 "
                                f"{_batch_state.plan_gate_tool_call_iters} 轮工具调用未见写文件, "
                                f"收尾前注入最后机会提醒 (iter={_iteration})"
                            )
                            _ctx.tracer.iteration_end("ITER_OK", "exec-gate reminder injected")
                            _asst_eg = {"role": "assistant", "content": full_text}
                            if _full_reasoning and _full_reasoning.strip():
                                _asst_eg["reasoning_content"] = _full_reasoning
                            _ctx.new_msgs.append(_asst_eg)
                            _ctx.messages.append(_asst_eg)
                            _ctx.messages.append({"role": "user", "content": _exec_gate_inject})
                            _ctx.new_msgs.append({"role": "user", "content": _exec_gate_inject})
                            _consecutive_empty = 0
                            continue  # 不 break, 多给一轮机会

                        _ctx.tracer.iteration_end("ITER_OK")
                        _asst = {"role": "assistant", "content": full_text}
                        # [DeepSeek-compat] 也要 attach reasoning_content
                        if _full_reasoning and _full_reasoning.strip():
                            _asst["reasoning_content"] = _full_reasoning
                        _ctx.new_msgs.append(_asst)
                        break
                    # ── 空回复处理 (包括纯换行 \n\n) ──
                    # [v1.0] 旧逻辑用 _iteration<4 判 give up,
                    # 导致前面正常工作 6 轮后, 第 7 轮一次空回复就直接 "抱歉..." 退场.
                    # 新逻辑:
                    #   1. 用 _consecutive_empty 计数 *连续* 空回复 (有产出就清零)
                    #   2. 上限拉到 _empty_reply_max (默认 10), 永远不主动放弃
                    #   3. 渐进式 nudge: 温和→诊断→强制搜索, 让模型自己换招而不是兜底
                    #   4. 即使最终触顶, 也不发 "抱歉" 字样 —— 输出已做工作摘要
                    _consecutive_empty += 1
                    _empty_reply_max   = 50  # [v1.0] 连续 50 次空回复才认输 (兜底)
                    # [FIX] 区分"只推理不产出"和"完全无响应" — 前者要给更强硬的"立刻执行"指令
                    _had_reasoning = bool(_full_reasoning and _full_reasoning.strip())
                    if _had_reasoning:
                        _last_reasoning_salvage = _full_reasoning  # 触顶放弃时用它兜底, 保证正文非空
                    log.warning(f"  \033[33m[empty-reply] iteration={_iteration} "
                                f"consecutive={_consecutive_empty}/{_empty_reply_max} "
                                f"reasoning_only={_had_reasoning}({len(_full_reasoning)}c)\033[0m")
                    _ctx.tracer.iteration_end("ITER_EMPTY_REPLY",
                                          f"consecutive={_consecutive_empty} reasoning={len(_full_reasoning)}c")
                    _ctx.messages.append({"role": "assistant", "content": "(empty)"})

                    # [FIX] 首轮若只推理不干活, 把 reasoning 摘要落库 (不污染 messages, 只入 new_msgs)
                    # 目的: 避免前端看到"推理完就退出"且 saved 1 msgs 零记录的尴尬。
                    # 只在本次首次空回复 + reasoning 非空时记一次, 后续 nudge 轮不重复记。
                    if _had_reasoning and _consecutive_empty == 1:
                        _rz_preview = _full_reasoning.strip()
                        _rz_snip = _rz_preview[:800] + ("..." if len(_rz_preview) > 800 else "")
                        _ctx.new_msgs.append({
                            "role": "assistant",
                            "content": f"[思考未产出任何工具/内容, 已保留 reasoning 摘要]\n{_rz_snip}",
                        })

                    # 渐进式 nudge: 越深陷越具体, 鼓励模型自助 (搜索 / 重读 / 换工具)
                    if _had_reasoning and _consecutive_empty <= 3:
                        # [FIX] 只推理不产出 — 最硬的"立即执行"指令, 不等 3 轮温和重试
                        _nudge = (
                            "[SYSTEM 硬约束: 上一轮你只输出了推理 (reasoning), 没有调用任何工具, "
                            "也没有给出正文回复, 用户等不到结果。\n"
                            "你现在必须在下一轮做以下其中一件, 不许再只输出推理:\n"
                            "  A. 调用具体工具 (execute_shell / write_file / web_search / read_file ...)\n"
                            "  B. 用 ≥10 字的正文 (非 reasoning) 回答用户\n"
                            "禁止把答案放在 reasoning 里, reasoning 是内部思考, 用户看不到实际内容。]"
                        )
                    elif _consecutive_empty <= 15:
                        # 1-15: 温和重试
                        _nudge = (
                            "[SYSTEM: 你的上一条回复为空, 用户在等待。"
                            "请继续推进当前任务: 要么调一个工具, 要么给最终答复, "
                            "不要静默, 也不要只输出空白。]"
                        )
                    elif _consecutive_empty <= 30:
                        # 16-30: 提示自查 + 给具体可用动作
                        _nudge = (
                            "[SYSTEM: 你已连续多次空回复, 可能卡在思考里出不来。"
                            "立刻执行下面*任意一条*具体动作, 不要再纯思考:\n"
                            "  • 用 execute_shell 跑 'ls -la <相关目录>' 看文件现状\n"
                            "  • 用 read_file 重读最近一次修改的源文件\n"
                            "  • 用 web_search 搜索最后一次工具报的错误信息\n"
                            "  • 如果你认为任务已完成, 直接用一句话总结结果\n"
                            "禁止再输出空白. 必须有 tool_call 或 ≥10 字的文本回复.]"
                        )
                    elif _consecutive_empty <= 49:
                        # 31-49: 强制策略切换 + 倾向搜索
                        _nudge = (
                            "[SYSTEM OVERRIDE: 你已连续 "
                            f"{_consecutive_empty} 次空回复, 当前策略明显失败。"
                            "立刻调用 web_search 查询任务相关的关键词或最近的报错; "
                            "如果搜不到, 直接用 execute_shell 跑诊断命令 "
                            "(env / which go / cat 出问题的文件 等). "
                            "禁止继续静默, 禁止输出 '无法继续' 之类放弃话术。]"
                        )
                    else:
                        # ≥50: 真的不动了, 跳出 — 但不输出 "抱歉", 让模型/用户继续
                        log.error(f"  \033[31m[empty-reply] consecutive={_consecutive_empty} "
                                  f">= max={_empty_reply_max}, breaking iteration loop "
                                  f"(no apology fallback)\033[0m")
                        # 把已完成的工作摘要回传, 而不是兜底道歉;
                        # [FIX 2026-09-04] 若答案被误路由进 reasoning, 取回当正文 (content 通道永不为空)
                        _done_tools = [
                            (m.get("name") or "?") for m in _ctx.messages
                            if m.get("role") == "tool"
                        ]
                        _summary = _salvage_empty_reply(_iteration, _done_tools, _last_reasoning_salvage)
                        if _last_reasoning_salvage.strip():
                            log.warning(f"  \033[33m[empty-reply] 触顶取回 reasoning 尾段当正文 "
                                        f"({len(_last_reasoning_salvage)}c → 正文)\033[0m")
                        yield sse_content(_summary, model)
                        _ctx.new_msgs.append({"role": "assistant", "content": _summary})
                        break

                    _ctx.messages.append({"role": "user", "content": _nudge})
                    continue  # 重试

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
            log.error(f"  [{_ctx.sid_tag}] 回合异常: {_raw_err[:500]}")
            _el = _raw_err.lower()
            if "maximum context length" in _el or "context_length_exceeded" in _el or "too many tokens" in _el:
                _err_msg = ("\n对话历史太长了，这轮没能跑完。我已经在压缩上下文，"
                            "你把刚才那句再发一次就行；要是还不行，说一声我给你开个新会话。")
            elif "timeout" in _el or "timed out" in _el:
                _err_msg = "\n上游响应超时，这轮中断了。再发一次试试。"
            elif "connect" in _el or "connection" in _el:
                _err_msg = "\n连不上模型服务，这轮中断了。稍后再试。"
            elif "rate limit" in _el or "429" in _el:
                _err_msg = "\n上游限流了，稍等一下再发。"
            else:
                _err_msg = "\n这轮出错中断了。具体原因已记进日志，再发一次试试。"
            try:
                yield sse_content(_err_msg, model)
            except GeneratorExit:
                pass
            try:
                _ctx.new_msgs.append({"role": "assistant", "content": _err_msg.strip()})
            except Exception:
                pass

        # AGENT_STREAM_REFACTOR M6a: FINALIZE 段提取为子生成器 (原 L2981-3053)
        async for _ev in _turn_finalize(_ctx, _usage, _batch_state, _guard_stats, model):
            yield _ev

    finally:
        # [v1.0] 安全网: 确保 TRACE_END 一定被写入
        _ctx.tracer.ensure_finished()
        # ── 无论客户端是否断连，都必须保存历史和记忆 ──
        try:
            if _ctx.session_id and _ctx.new_msgs:
                _save_history(_ctx.session_id, _ctx.base_history, _ctx.new_msgs)
                all_msgs = _ctx.base_history + _ctx.new_msgs
                if HAS_MEMORY:
                    # [v1.0] 所有 memory 压缩/更新都放后台线程
                    # 之前: check_and_compact 同步调 httpx.post(timeout=180) 压缩
                    #       → 阻塞 finally 块 → SSE 流无法关闭
                    # [v1.5 FIX] compress 和 rule/auto-learn/smart 分离到两个线程:
                    #   T1 (compress): LLM 压缩, 最慢, 可能 600s 超时, 不阻塞其他
                    #   T2 (fast): rule-memory on_task_end + auto_update_user/tools, 秒级
                    #   T3 (smart): LLM 智能补充, 分钟级, 和 compress 并行
                    # 理由: 以前串行导致 compress 超时 600s 把 rule-memory 也拖 10 分钟。
                    def _bg_compress_worker(_all_msgs):
                        try:
                            mgr = _mem_get(
                                workspace=WORKSPACE, session_id=_ctx.session_id,
                                vllm_url=BACKEND_URL, model_id=MODEL_ID, api_key=API_KEY,
                                context_window=CONTEXT_WINDOW,
                            )
                            mgr.check_and_compact(_all_msgs, _force_async=True)
                        except Exception as _bg_e:
                            log.warning(f"  [memory-compress:{_ctx.sid_tag}] fail: {_bg_e}")

                    def _bg_fast_worker(_all_msgs):
                        try:
                            _auto_update_user(
                                workspace=WORKSPACE, session_id=_ctx.session_id,
                                sessions_dir=SESSIONS_DISK, template_dir=TEMPLATE_DIR,
                                base_dir=BASE, messages=_all_msgs,
                            )
                        except Exception:
                            pass
                        try:
                            _auto_update_tools(
                                workspace=WORKSPACE, session_id=_ctx.session_id,
                                sessions_dir=SESSIONS_DISK, template_dir=TEMPLATE_DIR,
                                base_dir=BASE, messages=_all_msgs,
                            )
                        except Exception:
                            pass
                        try:
                            mgr_fast = _mem_get(
                                workspace=WORKSPACE, session_id=_ctx.session_id,
                                vllm_url=BACKEND_URL, model_id=MODEL_ID,
                                api_key=API_KEY, context_window=CONTEXT_WINDOW,
                            )
                            mgr_fast.on_task_end(_all_msgs)
                        except Exception as _fe:
                            log.warning(f"  [memory-fast:{_ctx.sid_tag}] fail: {_fe}")

                    def _bg_smart_worker(_all_msgs):
                        try:
                            mgr2 = _mem_get(
                                workspace=WORKSPACE, session_id=_ctx.session_id,
                                vllm_url=BACKEND_URL, model_id=MODEL_ID,
                                api_key=API_KEY, context_window=CONTEXT_WINDOW,
                            )
                            _smart_auto_memory(BACKEND_URL, MODEL_ID, API_KEY, _all_msgs, mgr2)
                        except Exception as _sm_e:
                            log.warning(f"  [memory-smart:{_ctx.sid_tag}] fail: {_sm_e}")

                    _snapshot = list(all_msgs)  # 快照避免竞争
                    for _worker in (_bg_compress_worker, _bg_fast_worker, _bg_smart_worker):
                        threading.Thread(
                            target=_worker, args=(_snapshot,), daemon=True,
                        ).start()
                log.info(f"  \033[32m[persist:{_ctx.sid_tag}] saved {len(_ctx.new_msgs)} msgs + memory (bg)\033[0m")

                # [SNAPSHOT 2026-09-03] task 边界自动快照 —— 给 agent 改的项目留可回滚锚点。
                # 复用本回合写入的文件路径推断项目根 (不依赖 _pm_state, 后者对绝对路径项目为空)。
                # 后台线程跑, 失败绝不影响主流程。
                try:
                    _written = list(getattr(_test_state, "src_files_written", [])) + \
                               list(getattr(_test_state, "test_files_written", []))
                    if _written:
                        from lib.snapshot import infer_project_root as _infer_root, snapshot as _do_snap
                        _proj = _infer_root(_written, fallback=(_pm_state.project_root or None))
                        if _proj:
                            _snap_label = f"turn-end iters={_batch_state.plan_gate_tool_call_iters} writes={_batch_state.exec_gate_write_calls}"
                            threading.Thread(
                                target=lambda: _do_snap(WORKSPACE, _proj, _snap_label),
                                daemon=True,
                            ).start()
                except Exception as _snap_exc:
                    log.warning(f"  [snapshot:{_ctx.sid_tag}] 挂载失败 (已忽略): {_snap_exc}")
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
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
