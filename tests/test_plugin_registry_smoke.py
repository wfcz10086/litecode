#!/usr/bin/env python3
# S1-K 冒烟: plugin 契约 + registry 扫描 + 内部/外部混挂 + 卸载
import asyncio
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))

from plugins.registry import registry, ENV_PATH  # noqa: E402
from plugins._contract import validate_module, PluginSpec  # noqa: E402


# ── 外部 plugin fixture (自愈: /tmp 被清也能重建) ───────────────
_EXT_DIR = "/tmp/lc_external_plugin_smoke"
os.makedirs(_EXT_DIR, exist_ok=True)
with open(f"{_EXT_DIR}/plugin.py", "w") as _f:
    _f.write(
        'NAME = "hello_external"\n'
        'DESCRIPTION = "外部机架自愈样例"\n'
        'VERSION = "0.0.1-smoke"\n'
        'SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}}\n'
        'async def run(args, ctx):\n'
        '    return {"ok": True, "result": f"echo:{args.get(\'text\',\'\')}"}\n'
    )


def test_validate_module_missing_field():
    class Fake:
        NAME = "x"
        DESCRIPTION = "y"
        SCHEMA = {}
        # no run
    ok, err = validate_module(Fake)
    assert not ok and "run" in err, err
    print("[OK] validate detects missing run")


def test_validate_module_ok():
    class Fake:
        NAME = "x"
        DESCRIPTION = "y"
        SCHEMA = {}
        async def run(args, ctx): return {"ok": True}
    ok, err = validate_module(Fake)
    assert ok, err
    print("[OK] validate accepts spec-compliant module")


def test_internal_scan():
    # 无外部路径, 只扫内部 tools/
    os.environ.pop(ENV_PATH, None)
    n = registry.scan()
    names = registry.names()
    assert n >= 1, f"expected at least 1 internal plugin (web_search family), got {n}"
    # web_search.py 抽出后应该注册进来
    print(f"[OK] internal scan loaded {n} plugins, sample={sorted(names)[:5]}")


def test_external_mount():
    os.environ[ENV_PATH] = "/tmp/lc_external_plugin_smoke"
    n = registry.scan()
    spec = registry.get("hello_external")
    assert spec is not None, f"external plugin not mounted; errors={registry.load_errors()}"
    assert spec.origin == "external"
    assert spec.version == "0.0.1-smoke"
    print(f"[OK] external plugin mounted from LITECODE_PLUGIN_PATH; total plugins={n}")


def test_external_unmount():
    os.environ.pop(ENV_PATH, None)
    registry.scan()
    assert registry.get("hello_external") is None, "external plugin still resident after unmount"
    print("[OK] unset ENV → external plugin disappears")


async def test_call_external():
    os.environ[ENV_PATH] = "/tmp/lc_external_plugin_smoke"
    registry.scan()
    res = await registry.call("hello_external", {"text": "机架 OK"}, {"sid": "smoke"})
    assert res.get("ok") and "机架 OK" in res["result"], res
    print(f"[OK] call() invoke → {res}")


def test_missing_plugin_dir():
    os.environ[ENV_PATH] = "/tmp/definitely_not_exist_xyz"
    n = registry.scan()
    # 不该崩; 只是内部还在
    print(f"[OK] missing external path handled gracefully (loaded {n})")


def test_collision_ignored():
    # 复制 hello 到另一路径, 名字冲突时后者被拒绝
    import shutil
    dupe = "/tmp/lc_dupe_plugin"
    os.makedirs(dupe, exist_ok=True)
    shutil.copy("/tmp/lc_external_plugin_smoke/plugin.py", f"{dupe}/plugin.py")
    os.environ[ENV_PATH] = f"/tmp/lc_external_plugin_smoke:{dupe}"
    registry.scan()
    errs = registry.load_errors()
    assert any("collision" in e for _, e in errs), f"expected collision error, got {errs}"
    print(f"[OK] name collision refused: {errs[-1]}")
    shutil.rmtree(dupe, ignore_errors=True)


if __name__ == "__main__":
    test_validate_module_missing_field()
    test_validate_module_ok()
    test_internal_scan()
    test_external_mount()
    test_external_unmount()
    asyncio.run(test_call_external())
    test_missing_plugin_dir()
    test_collision_ignored()
    print("\n[PASS] S1-K plugin registry smoke")
