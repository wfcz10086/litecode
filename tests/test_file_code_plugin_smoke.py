#!/usr/bin/env python3
# S1-B smoke: plugins/tools/file_code.py 挂上 registry, 8 件套 read/write/find/search 可 dispatch.
import sys, os, pathlib, asyncio, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

os.environ["LITECODE_ARTIFACT_ROOT"] = tempfile.mkdtemp(prefix="lc_fc_test_")

from plugins.registry import registry  # noqa: E402


EXPECTED = ("read_file", "write_file", "patch_file", "apply_blocks",
            "get_tree", "find_files", "search_code", "find_symbol")


def test_registry_has_file_code_8set():
    registry.scan()
    names = registry.names()
    for tool in EXPECTED:
        assert tool in names, f"missing {tool} in registry: {sorted(names)}"
    print(f"[OK] file+code 8-set registered ({len(names)} plugins total)")


def test_openai_schema_shape():
    """所有 8 个都能转成合法 openai_tool."""
    for tool in EXPECTED:
        spec = registry.get(tool)
        assert spec is not None, tool
        oai = spec.to_openai_tool()
        assert oai["type"] == "function"
        fn = oai["function"]
        assert fn["name"] == tool
        assert "properties" in fn["parameters"]
    print(f"[OK] 8/8 openai schema shape OK")


def test_write_then_read_roundtrip():
    """真跑一次 write_file → read_file 回环."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.close()
    payload = "hello S1-B\nline 2\nline 3\n"

    r = asyncio.run(registry.call("write_file",
                                    {"filepath": tmp.name, "content": payload},
                                    {"sid": "_smoke_fc"}))
    assert r.get("ok"), r

    r2 = asyncio.run(registry.call("read_file",
                                    {"filepath": tmp.name},
                                    {"sid": "_smoke_fc"}))
    assert r2.get("ok"), r2
    assert "hello S1-B" in r2["result"], r2
    print(f"[OK] write_file → read_file 回环 OK")
    os.unlink(tmp.name)


def test_find_files_via_registry():
    """跑一次 find_files 找当前测试文件."""
    r = asyncio.run(registry.call("find_files",
                                    {"pattern": "test_file_code*.py",
                                     "path": str(pathlib.Path(__file__).parent)},
                                    {"sid": "_smoke_fc"}))
    assert r.get("ok"), r
    assert "test_file_code" in r["result"], r
    print(f"[OK] find_files 找到自己 → OK")


if __name__ == "__main__":
    test_registry_has_file_code_8set()
    test_openai_schema_shape()
    test_write_then_read_roundtrip()
    test_find_files_via_registry()
    print("\n[PASS] S1-B plugins/tools/file_code.py 冒烟")
