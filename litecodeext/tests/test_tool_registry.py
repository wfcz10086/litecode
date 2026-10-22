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
# 2026-10-18: execute_shell 加可选 cwd (子代理工作树默认 cwd)
# 2026-10-19: Windows 段落导航与选择快捷键
# 2026-10-19: 快捷键白名单补 shift/ctrl+h/ctrl+shift+u 等 (生产日志里被拒过)
# 2026-10-19: windows_desktop 描述: shell 关闭时 vscode/ctrl+shift+esc/命令行前台输入受限, type_secret 固定 unicode, elevated 需管理员授权别名
TOOL_DEFS_SHA256 = "f88adcc7d3f86af15e623acecee3dbccd9936d8a3e589374a9d7f504f5638f21"   # 2026-10-22: windows_desktop 加 args (launch_app 给内置程序传参; 引导用 launch_app 以普通权限打开程序); 2026-10-22b: 末尾追加 kb_search (内置知识库, 按需组 kb; enable_tools 组说明多一行); 2026-10-22: 新增 stock_quote (实时行情, 按需组 market); 2026-10-22: create_skill 描述改为草稿进技能面板审核 (WORKSPACE/skills/_drafts), activate 仍需安全扫描, 描述更短; 2026-10-22: text_to_speech 加 local 引擎 (纯 CPU, 默认先用); 2026-10-22: 末尾追加 text_to_speech / manage_models (按需组 voice / settings, 不进常驻列表; 常驻只多 enable_tools 描述里两行组说明); 2026-10-22: 新增 stock_quote (实时行情, 按需组 market); 2026-10-21g: 降固定前缀 — 末尾追加 enable_tools (低频工具组按需暴露, lib/tool_groups.py; 实际每轮发送的是核心+已激活组), spawn_agent 描述与 batch_items 说明精简 (语义不变); 2026-10-21f: windows_desktop 加 ui_tree/click_element (UIA 控件树) + browser/control_type 参数, 描述写闭环与 focus_failed; windows_system 传输上限 50MB→1GB; 2026-10-21e: read_file/tail_log 分段 + ssh_write_file local_path/resume (大文件); 2026-10-21d: save_memory 加 scope/action (记忆全局档案); 2026-10-21c: load_skill 加 section 参数 (长技能按 ## 章节分段续读, 描述说明读源文件全文); 2026-10-21b: vision_ocr 加 paths 多图; 2026-10-21: ssh_exec 加 sudo 选项; 2026-10-20: 末尾追加 github_search / github_repo / package_search (82 个); web_search 加 category (SearXNG it/packages/repos/q&a/science)

READ_ONLY_SET = {
    "read_file", "get_tree", "find_files", "search_code", "find_symbol", "locate_symbol",
    "web_fetch", "web_search", "stock_quote", "kb_search", "github_search_issues", "stackoverflow_search", "search_code_error",
    "tail_log", "list_skills", "git_status", "git_diff", "git_log",
    "list_bg", "list_timers", "snapshot_list", "task_list", "ssh_list_hosts",
    "github_search", "github_repo", "package_search",   # 2026-10-20
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
    assert read_only_names() == READ_ONLY_SET
    assert READ_ONLY == READ_ONLY_SET


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
