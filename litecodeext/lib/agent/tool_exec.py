"""agent_stream 工具执行段 (拆分大文件, 从主循环提取)。"""
from dataclasses import dataclass
import asyncio
import time
from history_utils import _enhance_tool_error
from history_utils import _fmt_args
from lib import tool_filter as _tf
from lib.agent.spawn_dispatch import _dispatch_spawn_agent
from lib.agent.tool_guards import _guard_tool_call
from lib.agent.tool_guards import _tool_update_map
from lib.agent.tool_names import _WRITE_TOOL_NAMES, _is_action_tool
from lib.config import MAX_ERROR_STREAK
from lib.config import WORKSPACE
from lib.config import log
from lib.sse import sse_meta
from lib.sse import sse_status
from lib.tool_args import parse_tool_args as _parse_tool_args
from tool_dispatch import execute_tool


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


@dataclass
class ToolRunState:
    """_run_tool_calls 与主循环之间往返的跨迭代状态。"""
    effective_timeout: float
    error_streak: int
    iter_write_success: bool


async def _run_tool_calls(_ctx, _st, tool_call_list, tool_results,
                          _batch_state, _misc_state, _pm_state, _test_state, _prefetch, _sse_emit_to_buffer,
                          _subagent_sse_queue, _task_done_result_indices, _tool_loop_guard, _usage, _force_writer_mode, model):
    """逐个执行本批工具调用: 守卫拦截、spawn_agent 分派、普通工具执行与心跳、错误增强、结果入列。
    原 agent_stream 主循环内联段 (7 处 yield 逐帧透传); 跨迭代状态 _effective_timeout /
    error_streak / _iter_write_success 经 _st 读入并在 finally 回写。"""
    _effective_timeout = _st.effective_timeout
    error_streak = _st.error_streak
    _iter_write_success = _st.iter_write_success
    try:
        for tc in tool_call_list:
            fn_name = tc["function"]["name"]
            fn_args, _args_err = _parse_tool_args(tc["function"]["arguments"], fn_name)
            if _args_err:
                tool_results.append({"role": "tool", "tool_call_id": tc["id"], "content": _args_err})
                _ctx.tracer.tool_call(name=fn_name, args={}, result=_args_err, elapsed=0, is_error=True)
                log.warning(f"  [TOOL-ARGS] {fn_name} 参数非法 JSON, 已回给模型重发")
                continue

            if fn_name in _ctx.disabled_tools:
                tool_results.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": _tf.blocked_msg(fn_name)})
                log.warning(f"  [TOOL-DISABLED:{_ctx.sid_tag}] {fn_name}")
                continue

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
                    _tool_task = _prefetch.take(tc["id"], fn_args) or asyncio.ensure_future(
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

            if fn_name == 'windows_desktop':
                from lib.desktop_steps import desktop_step
                yield sse_status('desktop_step', desktop_step(result, fn_args, WORKSPACE, f"{tc['id']}-{time.time_ns()}"), model)

            if isinstance(diff, str) and diff:
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
            if _is_action_tool(fn_name):
                _batch_state.action_tool_calls += 1
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
    finally:
        _st.effective_timeout = _effective_timeout
        _st.error_streak = error_streak
        _st.iter_write_success = _iter_write_success
