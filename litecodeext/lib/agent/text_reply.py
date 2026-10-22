"""agent_stream 主循环"本轮没调工具"分支: 正文收尾与空回复治理 (拆分大文件, 从主循环提取)。"""
from dataclasses import dataclass
from lib.config import CHARS_PER_TOKEN
from lib.config import log
from lib.sse import sse_content
from lib.agent.tool_names import _WRITE_TOOL_NAMES_STR
from lib.config import CFG
from lib.agent.failure_loop import mark as _fl_mark
from lib.agent import readback_gate as _readback
_AGT = CFG.get("agent", {})


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


@dataclass
class TextReplyState:
    """_handle_text_reply 的跨迭代计数与出口动作 ("continue" / "break")。"""
    consecutive_empty: int
    last_reasoning_salvage: str
    action: str = ""


async def _handle_text_reply(_ctx, _batch_state, _st, full_text, _full_reasoning,
                             _iteration, _effective_max_iter, model):
    """本轮模型没有调工具: 正文回复走 PLAN 续行/EXEC-GATE/正常收尾, 空回复走渐进 nudge。
    原 agent_stream 主循环 else 分支; 原先跳回主循环的 continue/break 改为写 _st.action 后 return,
    跨迭代计数 _consecutive_empty / _last_reasoning_salvage 经 _st 读入并在 finally 回写。"""
    _consecutive_empty = _st.consecutive_empty
    _last_reasoning_salvage = _st.last_reasoning_salvage
    try:
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
                _st.action = "continue"
                return  # 不 break, 继续 agent loop

            # ── [READBACK] 收尾前回读闸: Windows 界面改过文件却没回读真值 → 拦一次, 要求先核对再总结
            # (真机案例: 文件 D5 实际 1964, 模型按截图汇报 4163 "已验证")。每回合最多一次, 第二次放行。
            _readback_inject = _readback.check(_ctx)
            if _readback_inject:
                log.info(f"  \033[35m[READBACK]\033[0m [{_ctx.sid_tag}] 界面改动后未回读, "
                         f"收尾前注入回读提醒 (iter={_iteration})")
                _ctx.tracer.iteration_end("ITER_OK", "readback reminder injected")
                _asst_rb = {"role": "assistant", "content": full_text}
                if _full_reasoning and _full_reasoning.strip():
                    _asst_rb["reasoning_content"] = _full_reasoning
                _ctx.new_msgs.append(_asst_rb)
                _ctx.messages.append(_asst_rb)
                _ctx.messages.append({"role": "user", "content": _readback_inject})
                _ctx.new_msgs.append({"role": "user", "content": _readback_inject})
                _consecutive_empty = 0
                _st.action = "continue"
                return  # 不 break, 多给一轮回读机会

            # ── [SYSTEM-CONTINUE] 远程操控回合里以"让我…/下一步…/Let me…"收尾却没调工具 → 续跑 (每回合 ≤2 次);
            #    [SYSTEM-CHECKLIST] GUI 任务收尾时用户原话要求的关闭/保存/读回没有执行痕迹 → 提醒补做 (每回合 ≤1 次)
            from lib.agent import remote_continue as _remote_continue
            _continue_inject = _remote_continue.check(_ctx, full_text)
            if _continue_inject:
                log.info(f"  \033[35m[CONTINUE]\033[0m [{_ctx.sid_tag}] 远程操控回合收尾拦截: "
                         f"{_continue_inject[:18]} (iter={_iteration})")
                _ctx.tracer.iteration_end("ITER_OK", "remote continue injected")
                _asst_rc = {"role": "assistant", "content": full_text}
                if _full_reasoning and _full_reasoning.strip():
                    _asst_rc["reasoning_content"] = _full_reasoning
                _ctx.new_msgs.append(_asst_rc)
                _ctx.messages.append(_asst_rc)
                _ctx.messages.append({"role": "user", "content": _continue_inject})
                _ctx.new_msgs.append({"role": "user", "content": _continue_inject})
                _consecutive_empty = 0
                _st.action = "continue"
                return

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
                _st.action = "continue"
                return  # 不 break, 多给一轮机会

            _ctx.tracer.iteration_end("ITER_OK")
            _asst = {"role": "assistant", "content": full_text}
            # [DeepSeek-compat] 也要 attach reasoning_content
            if _full_reasoning and _full_reasoning.strip():
                _asst["reasoning_content"] = _full_reasoning
            _ctx.new_msgs.append(_asst)
            _st.action = "break"
            return
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
            if len(_full_reasoning) >= 4000:
                # 想了一大段自己收尾却没出正文/工具 (没触发截断): 下一轮同样关思考直接动手 (见 llm_call)
                # 实测 GLM 物理 SVG: 连续两次 1.2 万字空想 (2026-10-21)
                _ctx.no_think_next = True
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
            _fl_mark(_ctx, "empty_reply", detail=f"连续 {_consecutive_empty} 次空回复")
            if _last_reasoning_salvage.strip():
                log.warning(f"  \033[33m[empty-reply] 触顶取回 reasoning 尾段当正文 "
                            f"({len(_last_reasoning_salvage)}c → 正文)\033[0m")
            yield sse_content(_summary, model)
            _ctx.new_msgs.append({"role": "assistant", "content": _summary})
            _st.action = "break"
            return

        _ctx.messages.append({"role": "user", "content": _nudge})
        _st.action = "continue"
        return  # 重试
    finally:
        _st.consecutive_empty = _consecutive_empty
        _st.last_reasoning_salvage = _last_reasoning_salvage
