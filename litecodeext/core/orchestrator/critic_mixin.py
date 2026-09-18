"""orchestrator/critic_mixin.py — Critic 审查 mixin (含 CRITICAL 抽取 + LLM 审查调用)."""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Dict, Optional

from blackboard import EntryType

from .helpers import _sse_step_status
from .types import DAGPlan, StepResult, StepStatus


class _CriticMixin:
    """Critic Agent 分片: 抽取 CRITICAL 标记 + 强制审查执行."""

    def _extract_critic_failed_steps(self, critic_output: str,
                                     plan: DAGPlan) -> list:
        """
        [v1.1] 从 critic 输出里找 [CRITICAL: ...] 标记, 提取受影响的 step_id.
        返回需要重跑的 step_id 列表 (去重, 按原 plan 顺序).
        Critic 输出格式示例:
          [CRITICAL: step:backend 未定义 parse() 函数]
          [CRITICAL: design 方案缺失数据库层]
        """
        import re as _re
        if "[CRITICAL:" not in critic_output:
            return []
        known_ids = {s.id for s in plan.steps}
        # 从 critic 段提取所有候选 token
        critical_blocks = _re.findall(
            r'\[CRITICAL:([^\]]+)\]', critic_output
        )
        found = []
        for block in critical_blocks:
            # 找 "step:xxx" 或 直接匹配 step_id 出现
            m = _re.search(r'step[:\s]([a-zA-Z_][\w\-]*)', block)
            if m and m.group(1) in known_ids:
                if m.group(1) not in found:
                    found.append(m.group(1))
                continue
            # 退路: 看 block 里是否直接出现某个 step_id
            for sid in known_ids:
                if sid in block and sid not in found:
                    found.append(sid)
                    break
        # 保底: 如果 critic 明确报 CRITICAL 但没具体到 step_id,
        # 回跑最后一个 success 的 writer/coder/analyst step (最可能出锅的)
        if not found and critical_blocks:
            for spec in reversed(plan.steps):
                if spec.agent_type in ("writer", "coder", "analyst"):
                    found.append(spec.id)
                    break
        return found

    async def _run_critic(
        self,
        plan: DAGPlan,
        results: Dict[str, StepResult],
        sse_emit: Optional[Callable],
    ) -> Optional[StepResult]:
        """
        强制审查节点。检查所有成功步骤的输出，找错误、遗漏、安全隐患。
        """
        successful = [r for r in results.values()
                      if r.status == StepStatus.SUCCESS and r.step_id != "__critic__"]
        if not successful:
            return None

        if sse_emit:
            await sse_emit(_sse_step_status(
                "__critic__", "Critic 审查", "running",
                f"审查 {len(successful)} 个步骤的输出",
            ))
        # [v1.1] itrace: Critic 开始
        try:
            self._tracer.critic_event("start", issues=[])  # type: ignore[attr-defined]
        except Exception:
            pass

        # 构建审查任务
        review_parts = []
        for r in successful:
            review_parts.append(
                f"### [{r.label}] ({r.agent_type})\n"
                f"任务: {r.task[:200]}\n"
                f"输出:\n{r.output[:1500]}\n"
            )

        checklist = plan.critic_checklist or (
            "1. 变量/函数是否定义后才使用\n"
            "2. API 调用参数是否正确\n"
            "3. 文件路径是否一致（不同步骤间）\n"
            "4. 是否有明显的安全漏洞（SQL注入/XSS/硬编码密码）\n"
            "5. 依赖是否已安装\n"
            "6. 端口/配置是否冲突\n"
            "7. 边界条件是否处理\n"
        )

        critic_task = (
            f"你是代码审查专家（Critic Agent）。审查以下步骤的输出：\n\n"
            f"{''.join(review_parts)}\n\n"
            f"## 审查清单:\n{checklist}\n\n"
            f"输出格式:\n"
            f"- ✅ 通过: 简要说明\n"
            f"- ❌ 问题: 具体描述 + 修复建议\n"
            f"- ⚠️ 建议: 优化建议\n\n"
            f"如果发现严重问题，在最后一行写: [CRITICAL: 问题描述]"
        )

        t0 = time.time()
        try:
            output = await asyncio.wait_for(
                self._run(critic_task, "critic", "", None,  # type: ignore[attr-defined]
                          parent_sid=self._parent_sid),  # type: ignore[attr-defined]  # [v1.0.5]
                timeout=120,
            )
            success = True
        except Exception as e:
            output = f"Critic 审查失败: {e}"
            success = False

        elapsed = time.time() - t0

        if sse_emit:
            status = "success" if success else "failed"
            icon = "✅" if success else "❌"
            await sse_emit(_sse_step_status(
                "__critic__", "Critic 审查", status,
                f"{icon} {elapsed:.1f}s",
            ))

        # 如果 Critic 发现严重问题，写入黑板
        critical_hit = success and "[CRITICAL:" in output
        if critical_hit:
            self.blackboard.put(  # type: ignore[attr-defined]
                "critic:critical_issues", output,
                EntryType.ERROR, source="critic",
            )

        # [v1.1] itrace: Critic 结束
        try:
            _issues = ([output[:300]] if critical_hit else [])
            self._tracer.critic_event("end", issues=_issues)  # type: ignore[attr-defined]
        except Exception:
            pass

        return StepResult(
            step_id="__critic__", label="Critic 审查",
            agent_type="critic", task="代码审查",
            status=StepStatus.SUCCESS if success else StepStatus.FAILED,
            output=output, elapsed=elapsed,
        )
