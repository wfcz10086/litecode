"""一轮对话的 SETUP / FINALIZE 阶段: 上下文组装、自动换会话、孤儿消息前缀、收尾持久化。

从 litecode_server.py 原样搬出 (拆分大文件), 函数体未改动; litecode_server 仍 re-export 这些名字。
"""
import json
import time
from history_utils import _auto_rotate_session
from history_utils import _clear_old_tool_results
from history_utils import _extract_first_user_summary
from history_utils import _extract_last_completed_turn
from history_utils import _sanitize_history
from lib.agent.failure_loop import recall_text as _fl_recall
from lib.agent_state import BatchWriteState
from lib.agent_state import TurnContext
from lib.agent_state import UsageCounters
from lib.config import BASE
from lib.config import CFG
from lib.config import HAS_MEMORY_INDEX
from lib.config import SESSIONS_DISK
from lib.config import TEMPLATE_DIR
from lib.config import TRACE_CFG as _TRACE_CFG
from lib.config import WORKSPACE
from lib.config import log
from lib.config import make_tracer
from lib.config import memory_index_get
from lib.guard_stats import GuardStats as _GuardStats
from lib.prompt import _detect_auto_delegation
from lib.prompt import build_request_system_prompt
from lib.session import _interrupt_flags
from lib.session import _interrupt_lock
from lib.session import get_history as _get_history
from lib.skills import auto_load_skills
from lib.sse import sse_content
from lib.sse import sse_done
from lib.sse import sse_error
from lib.sse import sse_status
from lib.sse import sse_stop
from lib.sse import sse_usage
from lib.stats import questions_append as _questions_append
from lib.tool_result_eviction import strip_old_reasoning as _strip_old_reasoning
from lib.turn_context import attach as _turn_attach
from lib.turn_context import materialize as _turn_materialize
from lib.turn_sources import enabled as _turn_ctx_enabled
from lib.turn_sources import prepare as _turn_prepare
from pathlib import Path
from typing import Any
from typing import AsyncGenerator
from typing import Optional
_AGT = CFG.get("agent", {})
try:
    from core.telemetry import new_trace as _tlm_new_trace
except Exception:
    def _tlm_new_trace() -> str: return ""


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
    log.info(f"\033[36m[{_ctx.sid_tag}]\033[0m <<< {' '.join(str(last_reply).split())[:120]} \033[2m[{_usage.elapsed:.1f}s, total={total_msgs} delta={len(_ctx.new_msgs)}]\033[0m")

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
    # 随消息变化的委派指令/匹配技能/记忆召回挂到本轮 user 消息前, system prompt 逐轮字节不变
    # (lib/turn_context.py + lib/turn_sources.py); 历史里存过的上下文按原样拼回
    _hist_anchor, _ctx.turn_context, _ctx.base_history = _turn_prepare(
        user_message, _ctx.session_id, _ctx.base_history)
    # P0-1: 清理历史中的旧 tool result，减少发给 vLLM 的 token 量
    # [FIX] 长对话入口裁剪: keep_recent 从 6 降到 4, 防止 session 重进上下文还是 300+ 条
    _ctx.base_history = _clear_old_tool_results(list(_ctx.base_history), keep_recent=4)
    # P0-1b: 修复 dangling tool_calls + 非法 JSON arguments，防止 vLLM/MiniMax 400
    # [FIX 2026-08-31] 剥离历史里的 reasoning_content。
    # 实测会话 wx-xxxx: 118 条消息带 reasoning_content 共 86,409 t, 比正文
    # (65,620 t) 还多, 占历史 57%。而两道预算闸门只数 content + tool_calls,
    # 对这个字段完全看不见 —— 这是 192,513 t 撑爆 204,800 窗口的主因
    # (估 107,104 + 漏掉的 86,409 = 193,513, 与 vLLM 实报差 0.5%)。
    # 上一轮的思考过程对下一轮没有价值, 只留最近 1 条;
    # 置空而非删键 —— DeepSeek 要求该字段存在 (见下方 reasoning_content 400 兼容分支)。
    # 返回副本, 不改会话缓存里共享的消息 dict (见 lib/tool_result_eviction.py)
    _ctx.base_history = _strip_old_reasoning(_ctx.base_history, keep=1)
    _ctx.base_history = _sanitize_history(_ctx.base_history)
    sys_prompt   = build_request_system_prompt(user_message, session_id=_ctx.session_id, strategist=strategist,
                                               history_anchor=_hist_anchor,
                                               include_dynamic=not _turn_ctx_enabled())
    _effective_user_content = _turn_attach(_effective_user_content, _ctx.turn_context)
    _mlc_done_this_turn = False   # [FIX 2026-08-31] 每回合最多压缩一次
    # [guard-stats] 守卫注入计数 —— 让'某条守卫在空转'可见 (见 lib/guard_stats.py)
    _guard_stats = _GuardStats(workspace=WORKSPACE, session_id=_ctx.session_id or '')

    # ── v1.0: memory_index 历史经验注入 ──
    if HAS_MEMORY_INDEX and not _turn_ctx_enabled():   # 开启时由 turn_sources.memory_index 提供
        try:
            _midx = memory_index_get(WORKSPACE)
            _hist_ctx = _midx.search_as_context(user_message, limit=3, max_chars=1500)
            if _hist_ctx:
                sys_prompt += "\n\n" + _hist_ctx
        except Exception as _me:
            log.warning(f"  [memory_index] search failed: {_me}")
    if not _turn_ctx_enabled():   # 旧路径: 失败经验同样进 system prompt (开启时由 turn_sources 提供)
        _fl_exp = _fl_recall(user_message, _ctx.session_id)
        if _fl_exp:
            sys_prompt += "\n\n" + _fl_exp

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
    _new_sid/_first_user/_last_turn 均为段内局部, 不跨段读写, 保持裸局部;
    _ctx.session_id/sid_tag/base_history 经引用原地改写, 调用者无需接回。
    """
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
        # 切换: _ctx.session_id 改成 new_sid, base_history 用 fresh first_user + last_turn
        # 注意: 不改函数参数 session_id (caller 的), 只改 _ctx.session_id (M5b)
        _ctx.session_id = _new_sid
        _ctx.sid_tag = _new_sid[:8]
        # 重新读 base_history (fresh fork 后的)
        _ctx.base_history = _turn_materialize(_get_history(_ctx.session_id) if _ctx.session_id else [])
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
