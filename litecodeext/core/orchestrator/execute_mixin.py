"""orchestrator/execute_mixin.py — DAG 主执行循环 + 流式包装."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import AsyncGenerator, Callable, Dict, List, Optional

from .helpers import _sse_content, _sse_step_status
from .types import (
    DAGPlan,
    OrchestrationResult,
    StepAction,
    StepResult,
    StepSpec,
    StepStatus,
)


def _derive_plan_id(plan: DAGPlan) -> str:
    """从 DAG 结构 hash 派生 plan_id, 保证同一 DAG 每次 execute 拿到相同 id, checkpoint 才能命中."""
    try:
        canon = [
            (s.id, s.agent_type or "", s.task or "", tuple(s.depends_on or []),
             getattr(s, "tool_name", "") or "")
            for s in plan.steps
        ]
        blob = json.dumps(canon, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return "plan_" + hashlib.sha1(blob).hexdigest()[:16]
    except Exception:
        return f"plan_{int(time.time())}_{uuid.uuid4().hex[:6]}"


class _ExecuteMixin:
    """DAG 主入口分片: execute + execute_streaming."""

    async def execute(
        self,
        plan: DAGPlan,
        sse_emit: Optional[Callable] = None,
    ) -> OrchestrationResult:
        """执行 DAG 计划。"""
        plan_id = _derive_plan_id(plan)
        t0 = time.time()

        # 尝试从 checkpoint 恢复
        completed = self._load_checkpoint(plan_id)  # type: ignore[attr-defined]

        # 构建依赖图
        layers = self._topological_sort(plan.steps)  # type: ignore[attr-defined]

        self._remaining_budget = plan.total_token_budget  # type: ignore[attr-defined]
        self._used_budget = 0  # type: ignore[attr-defined]

        results: Dict[str, StepResult] = {}
        aborted = False

        for layer_idx, layer in enumerate(layers):
            if aborted:
                for spec in layer:
                    results[spec.id] = StepResult(
                        step_id=spec.id, label=spec.label or spec.id,
                        agent_type=spec.agent_type, task=spec.task,
                        status=StepStatus.CANCELLED,
                    )
                continue

            # 跳过已完成的步骤（从 checkpoint 恢复）
            pending = [s for s in layer if s.id not in completed]
            skipped = [s for s in layer if s.id in completed]
            for s in skipped:
                results[s.id] = StepResult(
                    step_id=s.id, label=s.label or s.id,
                    agent_type=s.agent_type, task=s.task,
                    status=StepStatus.SUCCESS, output="[restored from checkpoint]",
                )
                if sse_emit:
                    await sse_emit(_sse_step_status(
                        s.id, s.label or s.id, "restored", "从快照恢复"))
                await self._fire_step_done(results[s.id])  # type: ignore[attr-defined]

            # [v1.3] 多条件判断: 入层前过滤 when, 不满足 → 标 SKIPPED 不执行
            _to_run = []
            for s in pending:
                ok, reason = self._evaluate_when(s, results)  # type: ignore[attr-defined]
                if ok:
                    _to_run.append(s)
                else:
                    results[s.id] = StepResult(
                        step_id=s.id, label=s.label or s.id,
                        agent_type=s.agent_type, task=s.task,
                        status=StepStatus.SKIPPED,
                        error=f"[条件不满足] {reason}",
                    )
                    if sse_emit:
                        await sse_emit(_sse_step_status(
                            s.id, s.label or s.id, "skipped", reason))
                    await self._fire_step_done(results[s.id])  # type: ignore[attr-defined]
            pending = _to_run

            if not pending:
                continue

            if len(pending) > 1:
                pending = sorted(pending, key=lambda s: len(s.task or ""))

            self._per_step_budget = 0  # type: ignore[attr-defined]
            if self._remaining_budget > 0:  # type: ignore[attr-defined]
                self._per_step_budget = max(self._remaining_budget // max(len(pending), 1), 1000)  # type: ignore[attr-defined]
                if sse_emit:
                    await sse_emit(_sse_content(
                        f"  [预算] 同层 {len(pending)} 步, 每步 {self._per_step_budget} tokens (剩 {self._remaining_budget})\n"  # type: ignore[attr-defined]
                    ))

            if sse_emit:
                await sse_emit(_sse_content(
                    f"\n⚡ 执行层 {layer_idx+1}/{len(layers)}: "
                    f"{len(pending)} 步骤"
                    f"{' (并行)' if len(pending) > 1 else ''}\n"
                ))

            # 同层并行执行
            _layer_t0 = time.time()
            if len(pending) == 1:
                r = await self._execute_step(pending[0], results, sse_emit)  # type: ignore[attr-defined]
                results[pending[0].id] = r
                await self._fire_step_done(r)  # type: ignore[attr-defined]
            else:
                # [P0-#9] Semaphore 节流: 大 DAG (100+ 步同层) 一起 gather 会打爆 LLM
                # 上游, subagent, tool 池. 由 plan.max_parallel_steps 控制 (default 4).
                _max_par = int(getattr(plan, "max_parallel_steps", 0) or 4)
                _sem = asyncio.Semaphore(max(_max_par, 1))
                async def _bounded_exec(spec):
                    async with _sem:
                        return await self._execute_step(spec, results, sse_emit)  # type: ignore[attr-defined]
                tasks = [_bounded_exec(spec) for spec in pending]
                layer_results = await asyncio.gather(*tasks, return_exceptions=True)
                for spec, r in zip(pending, layer_results):
                    if isinstance(r, Exception):
                        results[spec.id] = StepResult(
                            step_id=spec.id, label=spec.label or spec.id,
                            agent_type=spec.agent_type, task=spec.task,
                            status=StepStatus.FAILED, error=str(r),
                        )
                    else:
                        results[spec.id] = r
                    await self._fire_step_done(results[spec.id])  # type: ignore[attr-defined]
            # [v1.9 P38-a] DAG 层级并发性能埋点
            try:
                from core.telemetry import emit as _emit
                _layer_elapsed_ms = int((time.time() - _layer_t0) * 1000)
                _success_count = sum(
                    1 for s in pending
                    if results.get(s.id) and results[s.id].status == StepStatus.SUCCESS
                )
                _emit(event="dag_layer_done",
                      fields={
                          "layer_idx": layer_idx,
                          "layer_total": len(layers),
                          "step_count": len(pending),
                          "concurrent": len(pending) > 1,
                          "success_count": _success_count,
                          "step_ids": [s.id for s in pending],
                      },
                      jsonl="dag.jsonl",
                      latency_ms=_layer_elapsed_ms)
            except Exception:
                pass

            if self._remaining_budget > 0:  # type: ignore[attr-defined]
                layer_tokens = 0
                for spec in pending:
                    r = results.get(spec.id)
                    if r and r.output:
                        layer_tokens += len(r.output) // 4 + len(spec.task) // 4
                self._used_budget += layer_tokens  # type: ignore[attr-defined]
                self._remaining_budget = max(self._remaining_budget - layer_tokens, 0)  # type: ignore[attr-defined]

            # 检查关键步骤失败
            for spec in pending:
                r = results[spec.id]
                if r.status == StepStatus.FAILED and spec.critical:
                    action = self._evaluate_failure(spec, r, results)  # type: ignore[attr-defined]
                    if action == StepAction.ABORT:
                        aborted = True
                        if sse_emit:
                            await sse_emit(_sse_content(
                                f"\n🛑 关键步骤 [{r.label}] 失败且不可恢复，终止编排\n"
                            ))
                        break

            # 保存 checkpoint
            done_ids = {sid for sid, r in results.items()
                        if r.status == StepStatus.SUCCESS}
            self._save_checkpoint(plan_id, done_ids)  # type: ignore[attr-defined]

        # ── Critic 审查 + [v1.1] 回炉环 ──
        # 如果 critic 发现 [CRITICAL: ...], 找到提到的 step_id 重跑 (max 2 次)
        if plan.auto_critic and not aborted:
            _critic_max_retry = 2
            _critic_attempt = 0
            while _critic_attempt < _critic_max_retry:
                critic_result = await self._run_critic(plan, results, sse_emit)  # type: ignore[attr-defined]
                if critic_result:
                    results["__critic__"] = critic_result
                # 从 critic 输出里抽取 [CRITICAL: step_id X 有 Y 问题]
                replay_ids = self._extract_critic_failed_steps(  # type: ignore[attr-defined]
                    critic_result.output if critic_result else "", plan
                )
                if not replay_ids:
                    break  # critic 通过或只是 warning, 退出循环
                _critic_attempt += 1
                if sse_emit:
                    await sse_emit(_sse_content(
                        f"\n🔁 Critic 发现严重问题, 回炉重跑 {replay_ids} "
                        f"(round {_critic_attempt}/{_critic_max_retry})\n"
                    ))
                # 重跑这些 step (保留原 StepSpec, 把旧 result 清掉)
                id_to_spec = {s.id: s for s in plan.steps}
                for sid in replay_ids:
                    spec = id_to_spec.get(sid)
                    if not spec:
                        continue
                    # 把 critic 的建议注入 context 让 writer 知道要改什么
                    critic_hint = (critic_result.output[:1500]
                                   if critic_result else "")
                    spec_retry = StepSpec(
                        id=spec.id, task=spec.task, agent_type=spec.agent_type,
                        label=spec.label, depends_on=spec.depends_on,
                        timeout=spec.timeout, max_retries=0,  # 单次, 不再嵌套重试
                        context=(spec.context + "\n\n## Critic 上轮发现的问题\n" + critic_hint),
                        critical=spec.critical, retry_strategy="critic_replay",
                    )
                    r2 = await self._execute_step(spec_retry, results, sse_emit)  # type: ignore[attr-defined]
                    results[sid] = r2

        # ── 合并最终输出 ──
        final = self._merge_results(results, plan)  # type: ignore[attr-defined]
        elapsed = time.time() - t0

        return OrchestrationResult(
            plan_id=plan_id,
            steps=list(results.values()),
            final_output=final,
            total_elapsed=elapsed,
            success=not aborted and any(
                r.status == StepStatus.SUCCESS for r in results.values()
            ),
            blackboard_snapshot=self.blackboard.snapshot(),  # type: ignore[attr-defined]
            tokens_used=self._used_budget,  # type: ignore[attr-defined]
            tokens_remaining=self._remaining_budget,  # type: ignore[attr-defined]
        )

    async def execute_streaming(
        self,
        plan: DAGPlan,
    ) -> AsyncGenerator[str, None]:
        """流式版本，直接 yield SSE 字符串。"""
        buffer: List[str] = []

        async def _emit(chunk: str):
            buffer.append(chunk)

        holder: List[OrchestrationResult] = []

        async def _run():
            r = await self.execute(plan, _emit)
            holder.append(r)

        task = asyncio.create_task(_run())

        while not task.done() or buffer:
            if buffer:
                yield buffer.pop(0)
            else:
                await asyncio.sleep(0.05)

        try:
            await task
        except Exception as e:
            yield _sse_content(f"\n[Orchestrator Error] {e}")

        if holder:
            yield _sse_content(self._format_summary(holder[0]))  # type: ignore[attr-defined]
