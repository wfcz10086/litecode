"""P0-4 DAG checkpoint: plan_id 覆盖全部参数 + 恢复真实输出 (2026-10-21 外部审查)。全部本地, 不连网。"""
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


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    async def no_backoff(*a, **k):
        return None
    monkeypatch.setattr(SM, "sleep_backoff", no_backoff)


def _orch(run, tmp_path):
    return DAGOrchestrator(run, workspace=tmp_path, checkpoint_dir=tmp_path / "ckpt")


# ═══════════════ P0-4: plan_id 覆盖所有影响结果的参数 + 恢复真实输出 ═══════════════

@pytest.mark.parametrize("field,a,b", [
    ("tool_args", {"cmd": "echo 1"}, {"cmd": "echo 2"}),
    ("dag_name", "sub_a", "sub_b"),
    ("context", "用 A 数据", "用 B 数据"),
    ("when", ["success:x"], ["failure:x"]),
    ("expect", ["a"], ["b"]),
])
def test_p04_plan_id_covers_all_result_affecting_params(field, a, b):
    def plan(v):
        return DAGPlan(steps=[StepSpec(id="s", task="T", agent_type="tool", tool_name="execute_shell",
                                       **{field: v})])
    assert _derive_plan_id(plan(a)) != _derive_plan_id(plan(b)), f"{field} 不同却得到同一 plan_id"


def test_p04_plan_id_stable_for_same_plan():
    def plan():
        return DAGPlan(steps=[StepSpec(id="s", task="T", tool_args={"b": 1, "a": [1, 2]})])
    assert _derive_plan_id(plan()) == _derive_plan_id(plan())


def test_p04_plan_id_tracks_subdag_content(tmp_path):
    from lib.dag_schema import save_dag
    save_dag(tmp_path, "sub", {"steps": [{"id": "x", "task": "v1"}]})
    p = DAGPlan(steps=[StepSpec(id="s", task="T", agent_type="dag", dag_name="sub")], auto_critic=False)
    o = _orch(None, tmp_path)
    id1 = o._plan_id(p)
    save_dag(tmp_path, "sub", {"steps": [{"id": "x", "task": "v2"}]})
    assert o._plan_id(p) != id1, "子 DAG 内容变了, 父 DAG 的 checkpoint 不能命中"


def _two_steps():
    return DAGPlan(steps=[
        StepSpec(id="a", task="产出数据", agent_type="analyst", max_retries=0),
        StepSpec(id="b", task="用数据", agent_type="writer", depends_on=["a"], max_retries=0),
    ], auto_critic=False)


def test_p04_restored_step_output_is_real_output(tmp_path):
    seen = {}
    fail_b = {"on": True}

    async def run(task, agent_type, context, sse, parent_sid=None):
        if task == "产出数据":
            seen["a_runs"] = seen.get("a_runs", 0) + 1
            return '{"total": 1964}'
        seen["b_ctx"] = context
        return "[子代理异常] boom" if fail_b["on"] else "合计 1964"

    orch = _orch(run, tmp_path)
    r1 = asyncio.run(orch.execute(_two_steps()))
    assert {s.step_id: s.status for s in r1.steps}["b"] == StepStatus.FAILED
    fail_b["on"] = False
    orch2 = _orch(run, tmp_path)
    r2 = asyncio.run(orch2.execute(_two_steps()))
    a = next(s for s in r2.steps if s.step_id == "a")
    assert seen["a_runs"] == 1, "a 应从 checkpoint 恢复, 不重跑"
    assert a.output == '{"total": 1964}' and "restored" not in a.output
    assert '{"total": 1964}' in seen["b_ctx"] and "restored from checkpoint" not in seen["b_ctx"]
    assert "合计 1964" in r2.final_output


def test_p04_restored_output_feeds_placeholders(tmp_path, monkeypatch):
    """下游工具步 ${step:a:output} / ${step:a:json:total} 拿到的是 a 的真实输出。"""
    got = []

    async def run(task, agent_type, context, sse, parent_sid=None):
        return '{"total": 1964}'

    async def fake_tool(spec, prior, sse, label):
        from orchestrator.helpers import _substitute_step_placeholders
        got.append(_substitute_step_placeholders(spec.tool_args, prior))
        return "[tool:x] ok" if len(got) > 1 else "[tool-error] first run fails"
    monkeypatch.setattr(SM, "_run_tool_step", fake_tool)
    plan = lambda: DAGPlan(steps=[  # noqa: E731
        StepSpec(id="a", task="A", agent_type="analyst", max_retries=0),
        StepSpec(id="t", task="T", agent_type="tool", tool_name="x", depends_on=["a"], max_retries=0,
                 critical=False, tool_args={"raw": "${step:a:output}", "n": "${step:a:json:total}"}),
    ], auto_critic=False)
    asyncio.run(_orch(run, tmp_path).execute(plan()))
    asyncio.run(_orch(run, tmp_path).execute(plan()))
    assert got[1] == {"raw": {"total": 1964}, "n": 1964}, got


def test_p04_old_checkpoint_without_outputs_reruns(tmp_path):
    runs = []

    async def run(task, agent_type, context, sse, parent_sid=None):
        runs.append(task)
        return f"out:{task}"
    plan = _two_steps()
    orch = _orch(run, tmp_path)
    pid = orch._plan_id(plan)
    (tmp_path / "ckpt" / f"{pid}.json").write_text(json.dumps({"plan_id": pid, "completed": ["a", "b"]}))
    res = asyncio.run(_orch(run, tmp_path).execute(plan))
    assert runs == ["产出数据", "用数据"], "旧格式 checkpoint 没有真实输出, 必须重跑而不是给占位文字"
    assert all(s.status == StepStatus.SUCCESS for s in res.steps)


def test_p04_downstream_not_restored_when_upstream_reruns(tmp_path):
    """上游这次重跑 (输出可能变了), 下游即使在 checkpoint 里也要重跑, 不能拿旧结果。"""
    runs = []

    async def run(task, agent_type, context, sse, parent_sid=None):
        runs.append(task)
        return f"out:{task}:{len(runs)}"
    plan = _two_steps()
    orch = _orch(run, tmp_path)
    pid = orch._plan_id(plan)
    (tmp_path / "ckpt" / f"{pid}.json").write_text(json.dumps({
        "plan_id": pid, "version": 2, "completed": ["b"],
        "outputs": {"b": {"output": "旧的 b", "elapsed": 1.0, "attempts": 1, "label": "b"}}}))
    asyncio.run(_orch(run, tmp_path).execute(plan))
    assert runs == ["产出数据", "用数据"]
