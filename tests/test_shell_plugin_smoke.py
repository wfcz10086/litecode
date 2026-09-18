#!/usr/bin/env python3
# S1-C smoke: plugins/tools/shell.py 挂上 registry, 4 件套可通过 registry.call 走一次真实 dispatch.
import sys, os, pathlib, asyncio, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

os.environ["LITECODE_ARTIFACT_ROOT"] = tempfile.mkdtemp(prefix="lc_shell_test_")

from plugins.registry import registry  # noqa: E402


def test_registry_has_shell_4set():
    registry.scan()
    names = registry.names()
    for tool in ("execute_shell", "list_bg", "kill_bg", "tail_log"):
        assert tool in names, f"missing {tool} in registry: {sorted(names)}"
    assert not any("shell.py" in p and "contract" in e
                    for p, e in registry.load_errors()), registry.load_errors()
    print(f"[OK] shell 4-set registered under registry ({len(names)} plugins total)")


def test_openai_schema_shape():
    spec = registry.get("execute_shell")
    assert spec is not None
    tool = spec.to_openai_tool()
    assert tool["type"] == "function"
    fn = tool["function"]
    assert fn["name"] == "execute_shell"
    assert "command" in fn["parameters"]["properties"]
    assert "command" in fn["parameters"]["required"]
    print("[OK] execute_shell openai schema shape OK")


def test_list_bg_via_registry():
    """跑一次 list_bg — 空 session 应返回 'No background processes'."""
    r = asyncio.run(registry.call("list_bg", {}, {"sid": "_smoke_shell_probe"}))
    assert r.get("ok"), r
    assert "No background processes" in r["result"], r
    print("[OK] list_bg via registry → empty session message")


def test_tail_log_via_registry():
    """跑一次 tail_log 读自己这个测试文件的尾部."""
    r = asyncio.run(registry.call("tail_log",
                                    {"path": __file__, "lines": 3},
                                    {"sid": "_smoke_shell_probe"}))
    assert r.get("ok"), r
    assert "tail -n 3" in r["result"], r
    print("[OK] tail_log via registry → returned file tail")


def test_kill_bg_no_pid_error():
    """kill_bg 无 pid 应返回 ERROR 提示 (不是崩溃)."""
    r = asyncio.run(registry.call("kill_bg", {}, {"sid": "_smoke_shell_probe"}))
    assert r.get("ok"), r
    assert "ERROR" in r["result"] and "pid" in r["result"], r
    print("[OK] kill_bg 无 pid → ERROR 提示")


if __name__ == "__main__":
    test_registry_has_shell_4set()
    test_openai_schema_shape()
    test_list_bg_via_registry()
    test_tail_log_via_registry()
    test_kill_bg_no_pid_error()
    print("\n[PASS] S1-C plugins/tools/shell.py 冒烟")
