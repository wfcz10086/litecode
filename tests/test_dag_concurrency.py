#!/usr/bin/env python3
"""
test_dag_concurrency.py — v1.9 P38-a DAG 同层并发验证

通过时序证明同层独立节点确实并发执行（非串行）。

测试用 mock _run（每个节点 sleep N 秒），断言总耗时 ~max(节点) 而非 sum(节点)。
"""
import asyncio
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "litecodeext"))
sys.path.insert(0, str(ROOT / "litecodeext" / "core"))  # blackboard/itrace 扁平
os.environ["LITECODE_TEST_OFFLINE"] = "1"


class TestDAGConcurrency(unittest.TestCase):

    def setUp(self):
        for m in list(sys.modules.keys()):
            if m == "core" or m.startswith("core."):
                del sys.modules[m]
        from core import orchestrator
        from core.orchestrator import DAGOrchestrator, DAGPlan, StepSpec
        self.orch_mod = orchestrator
        self.DAGOrchestrator = DAGOrchestrator
        self.DAGPlan = DAGPlan
        self.StepSpec = StepSpec

    def test_t1_same_layer_parallel(self):
        """T1: 同层 3 个独立节点，每个 sleep 0.5s — 总耗时应 ~0.5s（并发）非 1.5s（串行）"""
        async def fake_run(task, agent_type, context, sse_emit, parent_sid=None):
            await asyncio.sleep(0.5)
            return f"output-{agent_type}"

        plan = self.DAGPlan(
            steps=[
                self.StepSpec(id="a", task="t1", agent_type="coder", label="A"),
                self.StepSpec(id="b", task="t2", agent_type="coder", label="B"),
                self.StepSpec(id="c", task="t3", agent_type="coder", label="C"),
            ],
            auto_critic=False,
        )
        orch = self.DAGOrchestrator(run_subagent_fn=fake_run)

        async def run_and_time():
            t0 = time.time()
            await orch.execute(plan, sse_emit=None)
            return time.time() - t0

        elapsed = asyncio.run(run_and_time())
        # 串行 = 1.5s, 并发 = ~0.5s
        # 给 0.7s 余量（事件循环开销）
        self.assertLess(elapsed, 1.0,
                        f"3 节点 × 0.5s 应并发完成 < 1s, 实际 {elapsed:.2f}s（疑串行）")
        self.assertGreater(elapsed, 0.4,
                           f"应至少 0.4s（最慢节点耗时）, 实际 {elapsed:.2f}s")

    def test_t2_dependency_serializes_layers(self):
        """T2: A → B → C 链式依赖 — 总耗时应 ~3 × 0.3s = 0.9s（强制串行 3 层）"""
        async def fake_run(task, agent_type, context, sse_emit, parent_sid=None):
            await asyncio.sleep(0.3)
            return f"output-{agent_type}"

        plan = self.DAGPlan(
            steps=[
                self.StepSpec(id="a", task="t1", agent_type="coder", label="A"),
                self.StepSpec(id="b", task="t2", agent_type="coder", label="B",
                              depends_on=["a"]),
                self.StepSpec(id="c", task="t3", agent_type="coder", label="C",
                              depends_on=["b"]),
            ],
            auto_critic=False,
        )
        orch = self.DAGOrchestrator(run_subagent_fn=fake_run)

        async def run_and_time():
            t0 = time.time()
            await orch.execute(plan, sse_emit=None)
            return time.time() - t0

        elapsed = asyncio.run(run_and_time())
        # 链式 3 层 × 0.3s = 0.9s
        self.assertGreater(elapsed, 0.7,
                           f"链式依赖应 >0.7s, 实际 {elapsed:.2f}s")
        self.assertLess(elapsed, 1.5,
                        f"应 <1.5s（不超过期望的 50%），实际 {elapsed:.2f}s")

    def test_t3_dag_layer_telemetry(self):
        """T3: 验证 dag_layer_done 埋点写入 dag.jsonl"""
        import tempfile
        import json
        tmp = Path(tempfile.mkdtemp(prefix="dagperf_"))
        os.environ["OPENCLAW_WORKSPACE"] = str(tmp)
        # 重新加载 telemetry 模块以读新 workspace
        for m in list(sys.modules.keys()):
            if m == "core" or m.startswith("core."):
                del sys.modules[m]
        from core import orchestrator
        from core.orchestrator import DAGOrchestrator, DAGPlan, StepSpec

        async def fake_run(task, agent_type, context, sse_emit, parent_sid=None):
            await asyncio.sleep(0.05)
            return "ok"

        plan = DAGPlan(
            steps=[
                StepSpec(id="x", task="t", agent_type="coder", label="X"),
                StepSpec(id="y", task="t", agent_type="coder", label="Y"),
            ],
            auto_critic=False,
        )
        orch = DAGOrchestrator(run_subagent_fn=fake_run)
        asyncio.run(orch.execute(plan, sse_emit=None))

        dag_jsonl = tmp / "telemetry" / "dag.jsonl"
        self.assertTrue(dag_jsonl.exists(),
                        f"dag.jsonl 未生成: {dag_jsonl}")
        rows = [json.loads(l) for l in dag_jsonl.read_text().splitlines() if l.strip()]
        # 应有 dag_layer_done 事件 + dag_step_done 事件
        events = [r["event"] for r in rows]
        self.assertIn("dag_layer_done", events,
                      f"缺 dag_layer_done 埋点: events={events}")
        # 验证字段
        layer_rows = [r for r in rows if r.get("event") == "dag_layer_done"]
        self.assertGreaterEqual(len(layer_rows), 1)
        r = layer_rows[0]
        self.assertEqual(r["step_count"], 2)
        self.assertTrue(r["concurrent"])  # 2 节点同层 → 并发


if __name__ == "__main__":
    unittest.main(verbosity=2)
