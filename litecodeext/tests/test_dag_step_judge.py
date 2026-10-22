"""P0-3 DAG 步骤成功判定 (2026-10-21 外部审查)。全部本地, 不连网。"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from orchestrator import DAGOrchestrator, DAGPlan, StepSpec, StepStatus  # noqa: E402
from orchestrator import step_mixin as SM  # noqa: E402
from orchestrator.execute_mixin import _derive_plan_id  # noqa: E402
from orchestrator.step_judge import judge_step  # noqa: E402
from lib.agent import subagent_evidence as ev  # noqa: E402
from lib.config import CFG  # noqa: E402


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    async def no_backoff(*a, **k):
        return None
    monkeypatch.setattr(SM, "sleep_backoff", no_backoff)


def _orch(run, tmp_path):
    return DAGOrchestrator(run, workspace=tmp_path, checkpoint_dir=tmp_path / "ckpt")


# ═══════════════ P0-3: 不能只凭输出非空判成功 ═══════════════

def test_p03_subdag_partial_failure_is_step_failure(monkeypatch, tmp_path):
    async def fake_subdag(spec, prior, sse, label, orch):
        return f"[子DAG:{spec.dag_name} 部分失败] 有输出但子 DAG 里关键步失败了"
    monkeypatch.setattr(SM, "_run_dag_step", fake_subdag)
    spec = StepSpec(id="s", task="跑子流程", agent_type="dag", dag_name="sub", max_retries=0)
    res = asyncio.run(_orch(None, tmp_path)._execute_step(spec, {}, None))
    assert res.status == StepStatus.FAILED, "子 DAG 部分失败不能当本步成功"


@pytest.mark.parametrize("out", [
    '[tool:execute_shell] done\n\n<tool_json>{"result": "done", "exit_code": 2}</tool_json>',
    '[tool:runner] x\n\n<tool_json>{"returncode": 1, "result": "x", "log": "' + "y" * 1600 + "</tool_json>",   # helpers 截到 1500 字后的 JSON 解析不了
    "[tool] execute_shell → (exit 3)",
    "[tool] execute_shell → None",
])
def test_p03_tool_step_nonzero_exit_or_empty_is_failure(out):
    ok, why = judge_step(StepSpec(id="t", task="", agent_type="tool", tool_name="x"), out)
    assert not ok, why


def test_p03_tool_step_zero_exit_ok():
    out = '[tool:execute_shell] ok\n\n<tool_json>{"result": "ok", "exit_code": 0}</tool_json>'
    assert judge_step(StepSpec(id="t", task="", agent_type="tool", tool_name="x"), out)[0]


def test_p03_agent_step_with_failing_tests_after_last_edit_fails(monkeypatch, tmp_path):
    monkeypatch.setitem(CFG, "agent", {**(CFG.get("agent") or {}), "subagent_evidence": True})
    runs = []

    async def run(task, agent_type, context, sse, parent_sid=None):
        col = ev.start_collector(agent_type, task)        # 与 _run_subagent 一样登记到证据槽
        col.observe("write_file", {"filepath": "/p/a.py"}, "wrote")
        col.observe("execute_shell", {"command": "pytest -q"},
                    "ERROR[TEST_FAIL]: FAILED t\n1 failed, 2 passed in 0.1s" if not runs else "3 passed in 0.1s")
        runs.append(task)
        return "已完成, 全部通过"          # 自述成功, 但最后一次修改后的测试挂了
    spec = StepSpec(id="c", task="改代码", agent_type="coder", max_retries=1)
    res = asyncio.run(_orch(run, tmp_path)._execute_step(spec, {}, None))
    assert len(runs) == 2, "第一次应因测试失败判失败并重试"
    assert res.status == StepStatus.SUCCESS and res.attempts == 2


def test_p03_expect_assertions(tmp_path):
    async def run(task, agent_type, context, sse, parent_sid=None):
        return "生成完毕 total=3"
    spec = StepSpec(id="w", task="写", agent_type="writer", max_retries=0, expect=["re:total=\\d+", "!ERROR"])
    assert asyncio.run(_orch(run, tmp_path)._execute_step(spec, {}, None)).status == StepStatus.SUCCESS
    spec2 = StepSpec(id="w", task="写", agent_type="writer", max_retries=0, expect=["status=ok"])
    r2 = asyncio.run(_orch(run, tmp_path)._execute_step(spec2, {}, None))
    assert r2.status == StepStatus.FAILED and "断言未满足" in r2.error


def test_p03_dag_schema_roundtrip_keeps_expect_and_dag_name():
    from lib.dag_schema import dag_from_dict, dag_to_dict
    d = {"steps": [{"id": "a", "task": "t", "agent_type": "dag", "dag_name": "sub", "expect": ["ok"]}]}
    back = dag_to_dict(dag_from_dict(d))
    assert back["steps"][0]["dag_name"] == "sub" and back["steps"][0]["expect"] == ["ok"]
