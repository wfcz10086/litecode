"""orchestrator/merge_mixin.py — 结果合并/详细/摘要 mixin."""
from __future__ import annotations

from typing import Dict

from .types import DAGPlan, OrchestrationResult, StepResult, StepStatus


class _MergeMixin:
    """结果合并与格式化"""

    def _merge_results(self, results: Dict[str, StepResult],
                       plan: DAGPlan) -> str:
        """合并所有步骤结果为最终输出。"""
        successful = [r for r in results.values()
                      if r.status == StepStatus.SUCCESS and r.step_id != "__critic__"]
        if not successful:
            failed = [r for r in results.values() if r.status == StepStatus.FAILED]
            return "[编排全部失败]\n" + "\n".join(
                f"- {r.label}: {r.error}" for r in failed
            )

        # 最后一个成功步骤的输出作为主结果
        main = successful[-1].output

        # 附加 Critic 审查结果
        critic = results.get("__critic__")
        if critic and critic.status == StepStatus.SUCCESS:
            main += f"\n\n---\n### Critic 审查报告\n{critic.output[:2000]}"

        return main

    def _merge_results_detailed(self, results: Dict[str, StepResult]) -> str:
        """详细格式（调试用）。"""
        parts = []
        for r in results.values():
            icon = {"success": "✅", "failed": "❌", "skipped": "⏭️",
                    "cancelled": "🚫"}.get(r.status.value, "❓")
            parts.append(
                f"{icon} [{r.label}] ({r.agent_type}) "
                f"· {r.elapsed:.1f}s · {r.attempts}次\n"
                f"  {r.output[:200].replace(chr(10), ' ')}"
            )
        return "\n".join(parts)

    def _format_summary(self, result: OrchestrationResult) -> str:
        """SSE 末尾摘要。"""
        lines = [
            f"\n\n---\n**DAG 编排完成** · ID: `{result.plan_id}` · "
            f"耗时: {result.total_elapsed:.1f}s"
        ]
        for r in result.steps:
            icon = {"success": "✅", "failed": "❌", "skipped": "⏭️",
                    "cancelled": "🚫", "restored": "♻️"}.get(r.status.value, "❓")
            lines.append(f"- {icon} `{r.label}` ({r.agent_type}) {r.elapsed:.1f}s")
        return "\n".join(lines) + "\n"
