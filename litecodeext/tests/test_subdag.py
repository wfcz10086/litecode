"""子 DAG 步 (agent_type=dag) 的回归用例 (#2)。

背景 (2026-09-05): 新增 agent_type=="dag" + dag_name —— 一个步骤加载并运行另一个已保存
DAG (嵌套 orchestrator, 复用父 run_subagent_fn, 带递归深度上限), 把子 DAG 的 final_output
当本步 output。用于流水线复用/模块化。

单元层验证 schema 校验 + dag_from_dict 传值; 真跑嵌套执行走 e2e (需 orchestrator+后端)。
"""
import sys
import unittest

sys.path.insert(0, "/opt/litecode")
sys.path.insert(0, "/opt/litecode/core")
sys.path.insert(0, "/opt/litecode/core/orchestrator")
sys.path.insert(0, "/opt/litecode/litecodeext")

from lib.dag_schema import validate_dag_json, dag_from_dict  # noqa: E402


class TestSubDag(unittest.TestCase):
    # ── schema: dag 步接受 + dag_name 必填 ──
    def test_accepts_dag_step_with_name(self):
        ok, err = validate_dag_json({"name": "parent", "steps": [
            {"id": "sub", "agent_type": "dag", "task": "跑子流水线", "dag_name": "child_hello"},
        ]})
        self.assertTrue(ok, err)

    def test_rejects_dag_step_without_name(self):
        ok, err = validate_dag_json({"name": "parent", "steps": [
            {"id": "sub", "agent_type": "dag", "task": "x"},
        ]})
        self.assertFalse(ok, "agent_type=dag 缺 dag_name 应被拒")
        self.assertTrue(any("dag_name" in e for e in err), err)

    # ── dag_from_dict 把 dag_name 带进 StepSpec ──
    def test_dag_from_dict_carries_dag_name(self):
        plan = dag_from_dict({"name": "parent", "steps": [
            {"id": "sub", "agent_type": "dag", "task": "跑子流水线", "dag_name": "child_hello"},
            {"id": "after", "agent_type": "writer", "task": "汇总", "depends_on": ["sub"]},
        ]})
        sub = next(s for s in plan.steps if s.id == "sub")
        self.assertEqual(sub.agent_type, "dag")
        self.assertEqual(sub.dag_name, "child_hello")

    # ── dag 步 + 条件 + json 取值 组合 (三个新特性一起用) ──
    def test_dag_with_when_and_json(self):
        ok, err = validate_dag_json({"name": "p", "steps": [
            {"id": "sub", "agent_type": "dag", "task": "跑子DAG出JSON", "dag_name": "child_scan"},
            {"id": "act", "agent_type": "writer", "task": "据子DAG结果处理",
             "when": [{"gt": ["$step.sub.json.count", 0]}], "depends_on": ["sub"]},
        ]})
        self.assertTrue(ok, err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
