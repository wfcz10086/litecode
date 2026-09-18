"""orchestrator/templates.py — 预定义 DAGPlan 模板 (升级版)."""
from __future__ import annotations

from .types import DAGPlan, StepSpec


class PlanTemplates:
    """开箱即用的 DAG 计划模板。"""

    @staticmethod
    def code_with_review(task: str, language: str = "Python") -> DAGPlan:
        """代码生成 → Critic 审查 → 测试验证"""
        return DAGPlan(
            steps=[
                StepSpec(
                    id="code", task=f"用{language}实现:\n{task}",
                    agent_type="coder", label="代码生成",
                    max_retries=2,
                ),
                StepSpec(
                    id="test", task="对上述代码写单元测试并运行",
                    agent_type="tester", label="测试验证",
                    depends_on=["code"], max_retries=1,
                ),
            ],
            auto_critic=True,
            critic_checklist=(
                "1. 代码是否能正确编译/运行\n"
                "2. 测试是否覆盖主要场景\n"
                "3. 是否有未处理的异常\n"
                "4. 变量命名是否清晰\n"
            ),
        )

    @staticmethod
    def fullstack_project(requirements: str) -> DAGPlan:
        """全栈项目: 设计 → 前端‖后端 → 集成测试 → 审查"""
        return DAGPlan(
            steps=[
                StepSpec(
                    id="design", task=f"根据需求设计技术方案:\n{requirements}",
                    agent_type="analyst", label="架构设计",
                    timeout=120, max_retries=1,
                ),
                StepSpec(
                    id="backend", task="根据设计方案实现后端 API",
                    agent_type="coder", label="后端开发",
                    depends_on=["design"], timeout=300,
                ),
                StepSpec(
                    id="frontend", task="根据设计方案实现前端页面",
                    agent_type="coder", label="前端开发",
                    depends_on=["design"], timeout=300,
                ),
                StepSpec(
                    id="integrate", task="集成前后端，运行端到端测试",
                    agent_type="tester", label="集成测试",
                    depends_on=["backend", "frontend"], timeout=180,
                ),
            ],
            auto_critic=True,
        )

    @staticmethod
    def research_and_implement(topic: str, code_task: str) -> DAGPlan:
        """调研‖编码框架 → 完善代码 → 测试"""
        return DAGPlan(
            steps=[
                StepSpec(
                    id="research", task=f"搜索 {topic} 的最新信息和最佳实践",
                    agent_type="researcher", label="调研",
                    timeout=120, critical=False,
                ),
                StepSpec(
                    id="scaffold", task=f"搭建 {code_task} 的代码框架",
                    agent_type="coder", label="代码框架",
                    timeout=180,
                ),
                StepSpec(
                    id="implement", task="根据调研结果完善代码实现",
                    agent_type="coder", label="完善实现",
                    depends_on=["research", "scaffold"], timeout=300,
                ),
                StepSpec(
                    id="verify", task="运行代码验证功能",
                    agent_type="tester", label="验证",
                    depends_on=["implement"],
                ),
            ],
            auto_critic=True,
        )

    @staticmethod
    def debug_workflow(codebase: str, bug: str) -> DAGPlan:
        """定位 → 修复 → 回归测试"""
        return DAGPlan(
            steps=[
                StepSpec(
                    id="locate", task=f"在 {codebase} 中定位 bug: {bug}",
                    agent_type="explorer", label="定位",
                    timeout=120,
                ),
                StepSpec(
                    id="fix", task=f"修复 bug: {bug}",
                    agent_type="coder", label="修复",
                    depends_on=["locate"], max_retries=2,
                ),
                StepSpec(
                    id="regression", task="运行回归测试",
                    agent_type="tester", label="回归测试",
                    depends_on=["fix"],
                ),
            ],
            auto_critic=True,
            critic_checklist=(
                "1. 修复是否针对根因而非表面症状\n"
                "2. 是否引入新的问题\n"
                "3. 测试是否覆盖了原始 bug 场景\n"
            ),
        )
