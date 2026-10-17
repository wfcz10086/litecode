"""统一工具注册表: 合并规则 / 冲突 / 能力派生 / DAG 桥接, 以及 TOOL_DEFS 字节不变。"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import lib.tool_registry as tr  # noqa: E402
from lib.tool_registry import build, read_only_names  # noqa: E402

# 统一注册表引入前 TOOL_DEFS 的序列化指纹 (74 个工具)。变了说明发给模型的前缀变了。
# 2026-10-16: ssh_exec / ssh_save_host 加 key_file / alias / jump (密钥登录 + 跳板); 其余 ssh_* / docker_remote_* 同步支持 key_file / alias / jump
# 2026-10-16: 新增 windows_desktop 初始化、截图及微信登录状态工具。
# 2026-10-17: windows_desktop 增 launch_app/window_state/compress 及新快捷键; 新增 windows_system 工具 (系统运维 + 文件传输 + 受控 run_powershell)。
# 2026-10-17: windows_desktop 加 double_click 与选择、格式快捷键。
# 2026-10-17: windows_system 加 inspect_file 回读核对; windows_desktop 描述加回读要求。
# 2026-10-17: spawn_agent 加 operator 角色 + alias 字段 (顶层与 parallel/pipeline/dag 每项)。
# 2026-10-17: 命令行开关边界。
TOOL_DEFS_SHA256 = "64ff3c78a2b14427122f1ca1b4d1ba8042621ba74b018913cf49b38cff2e6e8f"   # 2026-10-18: execute_shell 加可选 cwd (子代理工作树默认 cwd)

READ_ONLY_21 = {
    "read_file", "get_tree", "find_files", "search_code", "find_symbol", "locate_symbol",
    "web_fetch", "web_search", "github_search_issues", "stackoverflow_search", "search_code_error",
    "tail_log", "list_skills", "git_status", "git_diff", "git_log",
    "list_bg", "list_timers", "snapshot_list", "task_list", "ssh_list_hosts",
}


def _def(name, desc="d", params=None):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": params or {"type": "object"}}}


def test_tool_defs_bytes_unchanged():
    from lib.tools.defs import TOOL_DEFS
    s = json.dumps(TOOL_DEFS, ensure_ascii=False)
    assert hashlib.sha256(s.encode()).hexdigest() == TOOL_DEFS_SHA256


def test_read_only_derived_from_capabilities():
    from lib.tool_prefetch import READ_ONLY
    assert read_only_names() == READ_ONLY_21
    assert READ_ONLY == READ_ONLY_21


def test_merge_agent_schema_wins_and_caps_overlay():
    idx = build(
        [_def("web_search", "agent desc", {"type": "object", "properties": {"q": {}}})],
        ["web_search"],
        [{"name": "web_search", "description": "plugin desc", "schema": {"type": "object"},
          "capabilities": {"cost": "cheap", "read_only": False}},
         {"name": "image_gen", "description": "img", "schema": {"x": 1}, "capabilities": {"cost": "heavy"}}],
        capabilities={"web_search": {"read_only": True}},
    )
    ws = idx.entries["web_search"]
    assert ws.description == "agent desc" and "q" in ws.parameters["properties"]
    assert ws.sources == {"agent", "handler", "plugin"}
    assert ws.capabilities == {"cost": "cheap", "read_only": True}
    assert idx.entries["image_gen"].sources == {"plugin"} and idx.entries["image_gen"].parameters == {"x": 1}
    assert idx.names(read_only=True) == ["web_search"]
    assert idx.conflicts == []


def test_conflicts_only_within_one_source():
    idx = build([_def("a"), _def("a")], [], [{"name": "p", "schema": {}}, {"name": "p", "schema": {}}],
                capabilities={})
    assert idx.conflicts == ["a", "p"]


def test_dag_bridge_names():
    idx = build([_def("git_status"), _def("web_fetch"), _def("spawn_agent"), _def("execute_only_def")],
                ["git_status", "web_fetch", "spawn_agent"],
                [{"name": "web_fetch", "schema": {}}],
                capabilities={"spawn_agent": {"dag": False}})
    assert idx.dag_bridge_names() == ["git_status"]


def test_real_registry_has_no_conflicts():
    idx = tr.unified(refresh=True)
    assert idx.conflicts == []
    assert "git_status" in idx.dag_bridge_names()
    assert "spawn_agent" not in idx.dag_bridge_names()
    assert set(read_only_names()) <= set(idx.entries)


def test_plugin_registry_bridges_agent_tools(monkeypatch):
    # 别的测试可能已从另一份代码树导入过 plugins.*, 这里强制用本树的
    for k in [k for k in sys.modules if k == "plugins" or k.startswith("plugins.")]:
        monkeypatch.delitem(sys.modules, k)
    monkeypatch.syspath_prepend(str(ROOT))
    from plugins.registry import Registry

    calls = []

    def fake_runner(name):
        async def _run(args, ctx):
            calls.append((name, args))
            return {"ok": True, "result": f"ran {name}"}
        return _run

    fake_idx = build([_def("git_status")], ["git_status"], [], capabilities={})
    monkeypatch.setattr(tr, "unified", lambda refresh=False: fake_idx)
    monkeypatch.setattr(tr, "agent_runner", fake_runner)

    reg = Registry()
    monkeypatch.setattr(reg, "scan", lambda: 0)
    assert "git_status" in reg.names()
    assert reg.list() == []            # list() 仍只列真插件
    assert reg.get("git_status").origin == "agent"
    ret = asyncio.run(reg.call("git_status", {"x": 1}, {"job_id": "j"}))
    assert ret == {"ok": True, "result": "ran git_status"} and calls == [("git_status", {"x": 1})]


def test_agent_runner_maps_errors(monkeypatch):
    import types
    fake = types.ModuleType("core.tool_dispatch")

    async def execute_tool(name, args, sid):
        return ("ERROR: boom", None) if args.get("fail") else ("fine", "art-1")
    fake.execute_tool = execute_tool
    monkeypatch.setitem(sys.modules, "core.tool_dispatch", fake)
    run = tr.agent_runner("t")
    assert asyncio.run(run({"fail": 1}, {})) == {"ok": False, "error": "ERROR: boom"}
    assert asyncio.run(run({}, {"sid": "s"})) == {"ok": True, "result": "fine", "artifact": "art-1"}
