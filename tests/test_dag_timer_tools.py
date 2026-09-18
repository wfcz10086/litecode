"""Tests for DAG/Timer conversation tools (P43-a + P43-b)."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext"))


# ── helpers ──────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_dag_ws(tmp_path: Path, name: str, nodes=None, edges=None) -> Path:
    """Create a minimal DAG JSON in tmp_path/dags/{name}.json for test setup."""
    from lib.dag_schema import save_dag
    steps = []
    for n in (nodes or [{"id": "n1", "agent_type": "coder", "task": "do stuff"}]):
        steps.append({
            "id": n.get("id", "n1"),
            "agent_type": n.get("agent_type", "coder"),
            "label": n.get("label", n.get("id", "n1")),
            "task": n.get("task", "task"),
            "depends_on": [],
        })
    body = {"name": name, "steps": steps}
    save_dag(tmp_path, name, body)
    return tmp_path


def _mock_cfg(tmp_path: Path):
    return {"paths": {"workspace_base": str(tmp_path)}}


# ── P43-a: DAG tools ─────────────────────────────────────────────────────────

class TestUpdateDag:
    def test_success(self, tmp_path):
        _make_dag_ws(tmp_path, "mydag")
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, diff = run(_execute_tool_impl("update_dag", {
                "name": "mydag",
                "nodes": [
                    {"id": "a", "agent_type": "coder", "task": "new task A"},
                    {"id": "b", "agent_type": "writer", "task": "new task B"},
                ],
                "edges": [{"from": "a", "to": "b"}],
            }))
        assert "已更新" in result
        assert "mydag" in result
        assert diff is None
        from lib.dag_schema import load_dag
        dag = load_dag(tmp_path, "mydag")
        assert len(dag["steps"]) == 2
        assert dag["steps"][1]["depends_on"] == ["a"]

    def test_not_exist_error(self, tmp_path):
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("update_dag", {
                "name": "no_such_dag",
                "nodes": [{"id": "n1", "agent_type": "coder", "task": "x"}],
            }))
        assert "ERROR" in result
        assert "不存在" in result

    def test_missing_nodes_error(self, tmp_path):
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("update_dag", {"name": "mydag"}))
        assert "ERROR" in result


class TestDeleteDag:
    def test_success(self, tmp_path):
        _make_dag_ws(tmp_path, "todelete")
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("delete_dag", {"name": "todelete"}))
        assert "已删除" in result
        assert not (tmp_path / "dags" / "todelete.json").exists()

    def test_not_exist_error(self, tmp_path):
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("delete_dag", {"name": "ghost"}))
        assert "ERROR" in result
        assert "不存在" in result

    def test_missing_name_error(self, tmp_path):
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("delete_dag", {}))
        assert "ERROR" in result


class TestPatchDagNode:
    def test_success_task(self, tmp_path):
        _make_dag_ws(tmp_path, "patchme", nodes=[{"id": "n1", "agent_type": "coder", "task": "old"}])
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("patch_dag_node", {
                "name": "patchme", "node_id": "n1", "task": "new task"
            }))
        assert "已更新" in result
        from lib.dag_schema import load_dag
        dag = load_dag(tmp_path, "patchme")
        assert dag["steps"][0]["task"] == "new task"
        assert dag["steps"][0]["id"] == "n1"  # id unchanged

    def test_whitelist_rejects_id(self, tmp_path):
        _make_dag_ws(tmp_path, "patchme2", nodes=[{"id": "n1", "agent_type": "coder", "task": "t"}])
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("patch_dag_node", {
                "name": "patchme2", "node_id": "n1", "id": "hacked"
            }))
        assert "ERROR" in result
        assert "不允许" in result

    def test_whitelist_rejects_depends_on(self, tmp_path):
        _make_dag_ws(tmp_path, "patchme3", nodes=[{"id": "n1", "agent_type": "coder", "task": "t"}])
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("patch_dag_node", {
                "name": "patchme3", "node_id": "n1", "depends_on": ["x"]
            }))
        assert "ERROR" in result

    def test_node_not_found_error(self, tmp_path):
        _make_dag_ws(tmp_path, "patchme4")
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("patch_dag_node", {
                "name": "patchme4", "node_id": "ghost", "task": "x"
            }))
        assert "ERROR" in result
        assert "找不到" in result


class TestRunDag:
    def test_success(self, tmp_path):
        """run_dag loads DAG, enqueues asyncio task, returns job_id."""
        _make_dag_ws(tmp_path, "mydag")
        mock_orch = MagicMock()
        mock_result = MagicMock()
        mock_result.agents = []
        mock_result.final_output = "done"
        mock_orch.run = AsyncMock(return_value=mock_result)
        MockOrchClass = MagicMock(return_value=mock_orch)

        fake_multi_agent = MagicMock()
        fake_multi_agent.MultiAgentOrchestrator = MockOrchClass
        fake_multi_agent.AgentMode = MagicMock()
        fake_multi_agent.AgentMode.DAG = "dag"
        fake_multi_agent.AgentSpec = MagicMock(side_effect=lambda **kw: MagicMock())

        fake_subagent = MagicMock()
        fake_subagent._run_subagent = AsyncMock()

        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            with patch.dict(sys.modules, {
                "multi_agent": fake_multi_agent,
                "lib.agent.subagent": fake_subagent,
            }):
                result, _ = run(_execute_tool_impl("run_dag", {"name": "mydag"}))
        assert "已启动" in result
        assert "mydag" in result

    def test_not_found_error(self, tmp_path):
        from core.tool_dispatch import _execute_tool_impl
        with patch("lib.config.CFG", _mock_cfg(tmp_path)):
            result, _ = run(_execute_tool_impl("run_dag", {"name": "ghost"}))
        assert "ERROR" in result
        assert "不存在" in result

    def test_missing_name_error(self):
        from core.tool_dispatch import _execute_tool_impl
        result, _ = run(_execute_tool_impl("run_dag", {}))
        assert "ERROR" in result


# ── P43-b: Timer tools ───────────────────────────────────────────────────────

def _mock_timer_mgr(timers=None):
    """Return a mock TimerManager with the given timer list."""
    mgr = MagicMock()
    _timers = timers if timers is not None else [
        {"id": "t1", "name": "daily", "type": "cron", "schedule": "0 9 * * *",
         "enabled": True, "last_run": None, "history": [],
         "action": {"type": "shell", "content": "echo hi", "target": ""}},
    ]
    mgr.list_timers.return_value = _timers
    mgr.update_timer.side_effect = lambda tid, data: (
        next((t for t in _timers if t["id"] == tid), None)
    )
    mgr.delete_timer.side_effect = lambda tid: any(t["id"] == tid for t in _timers)
    mgr.run_now = AsyncMock(return_value={"ok": True, "result": "exit=0"})
    return mgr


class TestListTimers:
    def test_success(self):
        mgr = _mock_timer_mgr()
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("list_timers", {}))
        assert "daily" in result
        assert "t1" in result

    def test_enabled_only_filters(self):
        timers = [
            {"id": "t1", "name": "on", "type": "cron", "schedule": "* * * * *",
             "enabled": True, "last_run": None, "history": []},
            {"id": "t2", "name": "off", "type": "cron", "schedule": "* * * * *",
             "enabled": False, "last_run": None, "history": []},
        ]
        mgr = _mock_timer_mgr(timers)
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("list_timers", {"enabled_only": True}))
        assert "on" in result
        assert "off" not in result

    def test_empty_returns_message(self):
        mgr = _mock_timer_mgr([])
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("list_timers", {}))
        assert "暂无" in result


class TestUpdateTimer:
    def test_success(self):
        mgr = _mock_timer_mgr()
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("update_timer", {"id": "t1", "enabled": False}))
        assert "已更新" in result
        assert "t1" in result

    def test_not_found_error(self):
        mgr = _mock_timer_mgr()
        mgr.update_timer.return_value = None
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("update_timer", {"id": "ghost", "enabled": False}))
        assert "ERROR" in result
        assert "不存在" in result

    def test_missing_id_error(self):
        mgr = _mock_timer_mgr()
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("update_timer", {"enabled": False}))
        assert "ERROR" in result

    def test_no_fields_error(self):
        mgr = _mock_timer_mgr()
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("update_timer", {"id": "t1"}))
        assert "ERROR" in result


class TestDeleteTimer:
    def test_success(self):
        mgr = _mock_timer_mgr()
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("delete_timer", {"id": "t1"}))
        assert "已删除" in result

    def test_not_found_error(self):
        mgr = _mock_timer_mgr()
        mgr.delete_timer.return_value = False
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("delete_timer", {"id": "ghost"}))
        assert "ERROR" in result

    def test_missing_id_error(self):
        mgr = _mock_timer_mgr()
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("delete_timer", {}))
        assert "ERROR" in result


class TestRunTimerNow:
    def test_success(self):
        mgr = _mock_timer_mgr()
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("run_timer_now", {"id": "t1"}))
        assert "已触发" in result
        assert "t1" in result

    def test_not_found_error(self):
        mgr = _mock_timer_mgr()
        mgr.run_now = AsyncMock(return_value={"ok": False, "error": "not found"})
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("run_timer_now", {"id": "ghost"}))
        assert "ERROR" in result

    def test_missing_id_error(self):
        mgr = _mock_timer_mgr()
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("run_timer_now", {}))
        assert "ERROR" in result


class TestGetTimerHistory:
    def test_success(self):
        timers = [{
            "id": "t1", "name": "daily", "type": "cron", "schedule": "0 9 * * *",
            "enabled": True, "last_run": None,
            "history": [
                {"ts": time.time() - 3600, "status": "ok", "duration_ms": 120, "result": "ok"},
                {"ts": time.time() - 7200, "status": "ok", "duration_ms": 98, "result": "done"},
            ],
        }]
        mgr = _mock_timer_mgr(timers)
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("get_timer_history", {"id": "t1"}))
        assert "daily" in result
        assert "status=ok" in result

    def test_empty_history(self):
        timers = [{"id": "t1", "name": "daily", "type": "cron", "schedule": "",
                   "enabled": True, "last_run": None, "history": []}]
        mgr = _mock_timer_mgr(timers)
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("get_timer_history", {"id": "t1"}))
        assert "暂无" in result

    def test_not_found_error(self):
        mgr = _mock_timer_mgr([])
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("get_timer_history", {"id": "ghost"}))
        assert "ERROR" in result

    def test_missing_id_error(self):
        mgr = _mock_timer_mgr()
        from core.tool_dispatch import _execute_tool_impl
        with patch("web_ui._timer_manager", return_value=mgr):
            result, _ = run(_execute_tool_impl("get_timer_history", {}))
        assert "ERROR" in result
