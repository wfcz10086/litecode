"""Tests for DAG breakpoint support (P46)."""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext"))
sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext" / "core"))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_mock_run_fn():
    async def _run(task, agent_type, context, sse_emit, parent_sid=None):
        return "mock output"
    return _run


def _make_simple_plan():
    from orchestrator import DAGPlan, StepSpec
    return DAGPlan(steps=[
        StepSpec(id="n1", task="task one", agent_type="coder", label="Node 1"),
    ])


# ── Case 1: pause_check_fn=None → _execute_step 正常通过，不调 pause ──────────

class TestPauseCheckNone:
    def test_execute_step_no_pause_called(self, tmp_path):
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec
        called = []

        orch = DAGOrchestrator(
            run_subagent_fn=_make_mock_run_fn(),
            workspace=tmp_path,
        )
        assert orch._pause_check is None

        plan = _make_simple_plan()

        async def go():
            return await orch.execute(plan, sse_emit=None)

        result = run(go())
        assert result is not None
        assert len(called) == 0


# ── Case 2: pause_check_fn 设置但 node_id 不在断点 set → 立即返回 ────────────

class TestPauseCheckNoBreakpoint:
    def test_immediate_return_when_not_in_breakpoints(self, tmp_path):
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec

        call_log = []

        async def pause_fn(node_id):
            call_log.append(node_id)

        orch = DAGOrchestrator(
            run_subagent_fn=_make_mock_run_fn(),
            workspace=tmp_path,
            pause_check_fn=pause_fn,
        )

        plan = _make_simple_plan()

        async def go():
            return await orch.execute(plan, sse_emit=None)

        result = run(go())
        assert result is not None
        assert "n1" in call_log


# ── Case 3: pause_check_fn await 真 Event，resume 后能继续 ───────────────────

class TestPauseCheckWithAwait:
    def test_waits_until_event_set(self, tmp_path):
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec

        breakpoints = {"n1"}
        resume_events = {}
        paused_at = [None]

        async def pause_fn(node_id):
            if node_id not in breakpoints:
                return
            ev = resume_events.setdefault(node_id, asyncio.Event())
            paused_at[0] = node_id
            await ev.wait()
            ev.clear()
            paused_at[0] = None

        orch = DAGOrchestrator(
            run_subagent_fn=_make_mock_run_fn(),
            workspace=tmp_path,
            pause_check_fn=pause_fn,
        )
        plan = _make_simple_plan()

        async def go():
            ev = resume_events.setdefault("n1", asyncio.Event())
            asyncio.get_event_loop().call_later(0.05, ev.set)
            return await asyncio.wait_for(orch.execute(plan, sse_emit=None), timeout=2.0)

        result = run(go())
        assert result is not None
        assert paused_at[0] is None


# ── Case 4: paused_at 状态在暂停时是 node_id，恢复后回 None ──────────────────

class TestPausedAtStateTransition:
    def test_paused_at_lifecycle(self):
        job = {
            "breakpoints": {"n1"},
            "resume_events": {},
            "paused_at": None,
        }

        async def _pause_check(node_id):
            if node_id not in job["breakpoints"]:
                return
            ev = job["resume_events"].setdefault(node_id, asyncio.Event())
            job["paused_at"] = node_id
            try:
                await ev.wait()
            finally:
                ev.clear()
                if job.get("paused_at") == node_id:
                    job["paused_at"] = None

        async def go():
            ev = job["resume_events"].setdefault("n1", asyncio.Event())
            states = []

            async def check_state():
                await asyncio.sleep(0.01)
                states.append(job["paused_at"])
                ev.set()

            await asyncio.gather(
                _pause_check("n1"),
                check_state(),
            )
            return states

        states = run(go())
        assert states == ["n1"]
        assert job["paused_at"] is None


# ── Case 5: 重入测试 — 同节点两次断点，Event.clear 后能再次 wait ──────────────

class TestReentrantBreakpoint:
    def test_same_node_twice(self):
        job = {
            "breakpoints": {"n1"},
            "resume_events": {},
            "paused_at": None,
        }

        async def _pause_check(node_id):
            if node_id not in job["breakpoints"]:
                return
            ev = job["resume_events"].setdefault(node_id, asyncio.Event())
            job["paused_at"] = node_id
            try:
                await ev.wait()
            finally:
                ev.clear()
                if job.get("paused_at") == node_id:
                    job["paused_at"] = None

        async def go():
            hit_count = [0]

            async def do_pause_and_resume():
                ev = job["resume_events"].setdefault("n1", asyncio.Event())
                asyncio.get_event_loop().call_later(0.02, ev.set)
                await _pause_check("n1")
                hit_count[0] += 1

                asyncio.get_event_loop().call_later(0.02, ev.set)
                await _pause_check("n1")
                hit_count[0] += 1

            await asyncio.wait_for(do_pause_and_resume(), timeout=1.0)
            return hit_count[0]

        count = run(go())
        assert count == 2


# ── Case 6 (REST): pause_check_fn 异常被吞不影响主流 ─────────────────────────

class TestPauseHookExceptionIgnored:
    def test_exception_in_pause_hook_does_not_abort(self, tmp_path):
        from orchestrator import DAGOrchestrator

        async def bad_pause_fn(node_id):
            raise RuntimeError("intentional failure")

        orch = DAGOrchestrator(
            run_subagent_fn=_make_mock_run_fn(),
            workspace=tmp_path,
            pause_check_fn=bad_pause_fn,
        )
        plan = _make_simple_plan()

        async def go():
            return await orch.execute(plan, sse_emit=None)

        result = run(go())
        assert result is not None
        assert result.success
