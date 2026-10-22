"""orchestrator/step_mixin.py — 单步执行 + when 判定 + failure 决策 + context 构建 + 步完成回调."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

from blackboard import EntryType
from lib.retry import DEFAULT_POLICY, sleep_backoff

from .helpers import _run_tool_step, _run_dag_step, _sse_content, _sse_step_status
from .step_judge import SUBAGENT_FAIL_RE as _SUBAGENT_FAIL_RE, judge_step  # noqa: F401 (旧名保留)
from .types import StepAction, StepResult, StepSpec, StepStatus


async def _run_with_evidence(coro_fn):
    """[P0-3] 在子代理证据槽里跑 LLM 步: _run_subagent 的证据采集器会登记进槽, 跑完取
    "最后一次修改之后的相关验证明确失败" 的原因 (没有采集器/没失败 → None)。"""
    try:
        from lib.agent.subagent_evidence import _SLOT
    except Exception:
        return await coro_fn(), None
    slot: dict = {}
    token = _SLOT.set(slot)
    try:
        out = await coro_fn()
    finally:
        _SLOT.reset(token)
    col = slot.get("collector")
    try:
        return out, (col.explicit_failure() if col is not None else None)
    except Exception:
        return out, None

# 永久性错误: 重跑同样的东西只会同样失败 (权限/路径/工具不存在/用户中断), 不值得带反思重试
_PERMANENT_SIGNALS = (
    "permission denied", "no such file", "command not found",
    "host unreachable", "dns", "name resolution",
    "not registered", "tool_name empty", "dag_name empty",
    "[子代理已中断]", "[子代理中断]", "[子代理已取消", "[子代理嵌套过深]",
)


def _classify_step_error(text: str) -> str:
    """失败输出 → 'permanent' | 'timeout' | 'retryable'。规则匹配, 不耗 token。"""
    t = (text or "").lower()
    if any(s in t for s in _PERMANENT_SIGNALS):
        return "permanent"
    if "超时" in t or "timeout" in t or "timed out" in t:
        return "timeout"
    return "retryable"


class _StepMixin:
    """DAGOrchestrator 单步执行分片."""

    # ─────────────────────────────────────────────────────
    # 步骤执行（带重试）
    # ─────────────────────────────────────────────────────

    async def _execute_step(
        self,
        spec: StepSpec,
        prior_results: Dict[str, StepResult],
        sse_emit: Optional[Callable],
    ) -> StepResult:
        """执行单个步骤，含重试逻辑。"""
        label = spec.label or spec.id
        error_stack: List[str] = []

        if self._pause_check is not None:  # type: ignore[attr-defined]
            try:
                await self._pause_check(spec.id)  # type: ignore[attr-defined]
            except Exception:
                pass  # pause hook 失败不影响主流

        if sse_emit:
            await sse_emit(_sse_step_status(spec.id, label, "running", spec.task[:80]))
            await sse_emit(_sse_step_status(spec.id, label, "running", "", phase="start"))

        # [v1.1] itrace: DAG 步骤开始 (log 到 iteration_full.log)
        try:
            self._tracer.dag_step_begin(spec.id, label, spec.agent_type, spec.task[:200])  # type: ignore[attr-defined]
        except Exception:
            pass

        # [2026-09-05] 步开始回调: 让 job 状态实时登记"运行中"步 + 刷心跳 (解决长步期间全盲)
        _begin_cb = getattr(self, "_step_begin_cb", None)
        if _begin_cb:
            try:
                _r = _begin_cb(spec, label)
                if hasattr(_r, "__await__"):
                    await _r
            except Exception:
                pass

        _step_t0 = time.time()
        output = ""
        fail_reason = ""
        elapsed = 0.0
        attempts_used = 0
        for attempt in range(1, spec.max_retries + 2):  # +1 for initial attempt
            attempts_used = attempt
            # 构建 context: 黑板数据 + 依赖步骤输出
            context = self._build_step_context(spec, prior_results)

            # Self-reflection (P3-f): 重试时把上次失败原因拼进 task
            task_with_reflection = spec.task
            if attempt > 1 and error_stack:
                _refl = (
                    "\n\n## ⚠️ 失败反思 (这是第 {n} 次重试, 不是第 1 次尝试)\n"
                    "上次失败原因:\n{prev}\n\n"
                    "请认真思考:\n"
                    "1. 上次失败的真正根因是什么?\n"
                    "2. 我这次应该改变什么策略 / 工具 / 输入?\n"
                    "3. 不要重复上次的方法.\n"
                ).format(n=attempt, prev="\n".join(f"  - {e}" for e in error_stack[-3:]))
                if spec.retry_strategy:
                    _refl += f"\n用户预设的替代策略:\n{spec.retry_strategy}\n"
                task_with_reflection = spec.task + _refl

            t0 = time.time()
            try:
                # [v1.0.5] 传递 parent_sid 让子代理能检测父中断 + 记录 DAG 进度到日志
                try:
                    import logging as _olog
                    _olog.getLogger("openclaw").info(
                        f"  \033[33m[dag:{spec.id or '?'}] start\033[0m "
                        f"agent={spec.agent_type} label={label[:40]}"
                    )
                except Exception:
                    pass
                task_with_hint = task_with_reflection
                if self._per_step_budget > 0:  # type: ignore[attr-defined]
                    task_with_hint = task_with_reflection + f"\n\n[Token budget hint: 控制输出在 ~{self._per_step_budget} tokens 内]"  # type: ignore[attr-defined]
                # [Round 4 · 2026-07-24] 原生 tool step — 直接 registry.call, 不进 LLM
                if spec.agent_type == "tool":
                    output = await asyncio.wait_for(
                        _run_tool_step(spec, prior_results, sse_emit, label),
                        timeout=spec.timeout,
                    )
                    _ev_fail = None
                elif spec.agent_type == "dag":
                    # [#2 2026-09-05] 子 DAG 步: 跑 spec.dag_name 指向的另一个 DAG (嵌套 orchestrator)
                    output = await asyncio.wait_for(
                        _run_dag_step(spec, prior_results, sse_emit, label, self),
                        timeout=spec.timeout,
                    )
                    _ev_fail = None
                else:
                    output, _ev_fail = await _run_with_evidence(lambda: asyncio.wait_for(
                        self._run(task_with_hint, spec.agent_type, context,  # type: ignore[attr-defined]
                                  self._make_forwarder(spec.id, label, sse_emit),  # type: ignore[attr-defined]
                                  parent_sid=self._parent_sid),  # type: ignore[attr-defined]
                        timeout=spec.timeout,
                    ))
                # [P0-3 2026-10-21] 不再只凭"输出非空且无失败前缀": 工具看退出码/ok, 子 DAG 看明确成功标记,
                # LLM 步看子代理证据 (最后一次修改后的相关测试明确失败 → 失败), 再过步骤断言 expect
                success, fail_reason = judge_step(spec, output, _ev_fail)
                try:
                    import logging as _olog
                    _olog.getLogger("openclaw").info(
                        f"  \033[33m[dag:{spec.id or '?'}] done\033[0m "
                        f"ok={success} len={len(output) if output else 0}"
                    )
                except Exception:
                    pass
            except asyncio.TimeoutError:
                output = f"[超时] {label} 执行超过 {spec.timeout}s"
                success, fail_reason = False, output
            except Exception as e:
                output = f"[异常] {e}"
                success, fail_reason = False, output

            elapsed = time.time() - t0

            if success:
                # 成功：写入黑板
                self.blackboard.put(  # type: ignore[attr-defined]
                    f"step:{spec.id}:output", output[:2000],
                    EntryType.RESULT, source=label,
                )
                if sse_emit:
                    await sse_emit(_sse_step_status(
                        spec.id, label, "success",
                        f"✅ {elapsed:.1f}s · {output[:60].replace(chr(10), ' ')}",
                    ))
                    await sse_emit(_sse_step_status(
                        spec.id, label, "success", "",
                        phase="end", elapsed_ms=int((time.time() - _step_t0) * 1000),
                    ))
                # [v1.1] itrace: DAG 步骤成功
                try:
                    self._tracer.dag_step_end(spec.id, label, "success",  # type: ignore[attr-defined]
                                              time.time() - _step_t0)
                    self._tracer.blackboard_write(f"step:{spec.id}:output", "RESULT", label)  # type: ignore[attr-defined]
                except Exception:
                    pass
                # [v1.8 P36-b] DAG 埋点
                try:
                    from core.telemetry import emit as _emit
                    _emit(
                        event="dag_step_done",
                        fields={
                            "step_id": spec.id, "label": label[:40],
                            "agent_type": spec.agent_type,
                            "status": "success", "attempts": attempt,
                            "output_len": len(output) if output else 0,
                        },
                        jsonl="dag.jsonl",
                        latency_ms=int((time.time() - _step_t0) * 1000),
                    )
                except Exception:
                    pass
                return StepResult(
                    step_id=spec.id, label=label,
                    agent_type=spec.agent_type, task=spec.task,
                    status=StepStatus.SUCCESS, output=output,
                    elapsed=elapsed, attempts=attempt,
                    error_stack=error_stack,
                )

            # 失败：记录错误栈 (判定原因优先, 例如断言/证据失败时输出本身看起来是正常文本)
            error_msg = (fail_reason or output or "")[:300]
            error_stack.append(f"[attempt {attempt}] {error_msg}")

            # [2026-10-19] 在循环内先分类再决定重试: 原来不管什么错都跑满 max_retries+1 次才交给
            # _evaluate_failure 分类 — 超时步每次都吃满 spec.timeout (实测 3×300s≈15 分钟空转),
            # "permission denied"/"not registered" 这类永久错误还带"失败反思"重试, 结果必然一样。
            # 永久错误不重试; 超时最多再试 1 次; 其余照 max_retries。
            _kind = _classify_step_error(fail_reason or output)
            _retry_limit = spec.max_retries
            if _kind == "permanent":
                _retry_limit = 0
            elif _kind == "timeout":
                _retry_limit = min(spec.max_retries, 1)

            if attempt <= _retry_limit:
                # 还有重试机会
                if sse_emit:
                    await sse_emit(_sse_step_status(
                        spec.id, label, "retrying",
                        f"⚠️ 第{attempt}次失败，重试中... ({error_msg[:60]})",
                    ))
                if sse_emit:
                    await sse_emit(_sse_content(
                        f"\n💭 反思第 {attempt} 次失败, 调整策略重试...\n"
                    ))
                # 把错误写入黑板供 Supervisor 决策
                self.blackboard.put_error(  # type: ignore[attr-defined]
                    f"step:{spec.id}:attempt_{attempt}",
                    error_msg, source=label,
                )
                # [#68] 指数退避 + jitter: 1s/2s/4s (±25%), cap 30s
                await sleep_backoff(attempt - 1, DEFAULT_POLICY)
            else:
                if sse_emit:
                    _why = {"permanent": "永久性错误, 不再重试", "timeout": "超时, 不再重试"}.get(
                        _kind, f"{attempt}次尝试全部失败")
                    await sse_emit(_sse_step_status(
                        spec.id, label, "failed",
                        f"❌ {_why}",
                    ))
                    await sse_emit(_sse_step_status(
                        spec.id, label, "failed", "",
                        phase="end", elapsed_ms=int((time.time() - _step_t0) * 1000),
                    ))
                break

        # [v1.1] itrace: DAG 步骤失败
        try:
            self._tracer.dag_step_end(spec.id, label, "failed", time.time() - _step_t0)  # type: ignore[attr-defined]
        except Exception:
            pass
        # [v1.8 P36-b] DAG 埋点（失败）
        try:
            from core.telemetry import emit as _emit
            _emit(
                event="dag_step_failed",
                fields={
                    "step_id": spec.id, "label": label[:40],
                    "agent_type": spec.agent_type,
                    "status": "failed", "attempts": attempts_used,
                    "error_summary": (error_stack[-1] if error_stack else "")[:200],
                },
                jsonl="dag.jsonl",
                latency_ms=int((time.time() - _step_t0) * 1000),
            )
        except Exception:
            pass
        return StepResult(
            step_id=spec.id, label=label,
            agent_type=spec.agent_type, task=spec.task,
            status=StepStatus.FAILED, output=output,
            error=(fail_reason or output or "")[:300], elapsed=elapsed,
            attempts=attempts_used,
            error_stack=error_stack,
        )

    # ─────────────────────────────────────────────────────
    # Context 构建
    # ─────────────────────────────────────────────────────

    # [v1.3] 多条件判断 + 步骤完成回调
    def _evaluate_when(self, spec: StepSpec,
                       results: Dict[str, StepResult]) -> Tuple[bool, str]:
        """评估 spec.when. v2 DSL (2026-05): list/dict/str 任意嵌套, 见 _when_eval.py.
        兼容 v1.3 旧字符串语法 (success:/failure:/contains:/!).
        """
        if not spec.when:
            return True, ""
        try:
            from _when_eval import evaluate_when
        except ImportError:
            from ._when_eval import evaluate_when  # type: ignore[no-redef]  # 包内 fallback
        # 构造 ctx: steps={sid: {status, output, json?, tool_count, duration_ms}}
        ctx_steps = {}
        for sid, r in results.items():
            ctx_steps[sid] = {
                "status": (r.status.value if hasattr(r.status, "value") else str(r.status)).lower(),
                "output": r.output or "",
                "tool_count": getattr(r, "tool_count", 0),
                "duration_ms": int((getattr(r, "elapsed", 0) or 0) * 1000),
            }
        ctx = {"steps": ctx_steps, "env": dict(__import__("os").environ)}
        return evaluate_when(spec.when, ctx)

    async def _fire_step_done(self, result: "StepResult"):
        """步骤完成 (含 SKIPPED/RESTORED) 时回调外部. 异常静默吞掉, 不影响主流."""
        cb = self._step_done_cb  # type: ignore[attr-defined]
        if cb is None:
            return
        try:
            import inspect
            ret = cb(result)
            if inspect.iscoroutine(ret):
                await ret
        except Exception as e:
            try:
                import logging
                logging.getLogger(__name__).warning(
                    f"[orchestrator] step_done_cb error: {e}")
            except Exception:
                pass

    def _build_step_context(self, spec: StepSpec,
                            prior_results: Dict[str, StepResult]) -> str:
        """为步骤构建 context: 黑板 + 依赖输出 + 用户自定义 context。"""
        parts = []

        # 1. 黑板全局上下文
        bb_ctx = self.blackboard.to_context_string(max_chars=3000)  # type: ignore[attr-defined]
        if bb_ctx:
            parts.append(bb_ctx)

        # 2. 依赖步骤的输出
        for dep_id in spec.depends_on:
            dep_result = prior_results.get(dep_id)
            if dep_result and dep_result.status == StepStatus.SUCCESS:
                max_out = 6000 if spec.agent_type in ("coder", "writer") else 2000
                parts.append(
                    f"### 前置步骤 [{dep_result.label}] 输出:\n"
                    f"{dep_result.output[:max_out]}"
                )
            elif dep_result and dep_result.status == StepStatus.FAILED:
                parts.append(
                    f"### ⚠️ 前置步骤 [{dep_result.label}] 失败:\n"
                    f"错误: {dep_result.error[:300]}\n"
                    f"你需要独立完成任务，不依赖该步骤的输出。"
                )

        # 3. 用户自定义 context
        if spec.context:
            parts.append(f"### 额外上下文:\n{spec.context}")

        return "\n\n".join(parts) if parts else ""

    # ─────────────────────────────────────────────────────
    # Supervisor 失败评估
    # ─────────────────────────────────────────────────────

    def _evaluate_failure(self, spec: StepSpec, result: StepResult,
                          all_results: Dict[str, StepResult]) -> StepAction:
        """
        Supervisor 状态机: 评估失败步骤，决定下一步动作。
        基于规则的快速判断，不消耗 LLM token。
        """
        error = result.error.lower()
        _kind = _classify_step_error(error)

        # 永久性错误 → 终止
        if _kind == "permanent":
            return StepAction.ABORT

        # 超时 → 如果非关键步骤，跳过
        if _kind == "timeout":
            return StepAction.ABORT if spec.critical else StepAction.SKIP

        # 已多次重试仍失败 → 终止
        if result.attempts > spec.max_retries:
            return StepAction.ABORT if spec.critical else StepAction.SKIP

        # 默认：继续执行后续步骤
        return StepAction.CONTINUE
