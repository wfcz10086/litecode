#!/usr/bin/env python3
"""
test_telemetry.py — P36-c 统一埋点入口白盒测试

验证 core/telemetry.py 的 emit() 函数:
1. ts 基础字段存在 + 是 float
2. trace_id 为 8 字符 hex
3. session_id 透传
4. 业务字段保留
5. trace_id ContextVar 跨调用传播
"""
import json
import sys
import tempfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "litecodeext"))


def setup_temp_workspace() -> Path:
    """临时工作区，避免污染真实 telemetry"""
    tmp = Path(tempfile.mkdtemp(prefix="tlm_test_"))
    os.environ["OPENCLAW_WORKSPACE"] = str(tmp)
    return tmp


def reload_telemetry():
    """重新载入 telemetry 模块（让 _WORKSPACE 重读 env）"""
    import importlib
    # 清掉 core 包及子模块的缓存（Python import 系统对包属性有缓存）
    for mod_name in list(sys.modules.keys()):
        if mod_name == "core" or mod_name.startswith("core."):
            del sys.modules[mod_name]
    from core import telemetry
    return telemetry


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_t1_basic_emit():
    """T1: 基础 emit — ts/trace_id/session_id/event 5 元组存在"""
    tmp = setup_temp_workspace()
    tlm = reload_telemetry()
    ok = tlm.emit("test_event", {"foo": "bar"}, jsonl="t1.jsonl", sid="sid-001")
    assert ok, "emit 应返回 True"
    rows = read_jsonl(tmp / "telemetry" / "t1.jsonl")
    assert len(rows) == 1
    r = rows[0]
    assert isinstance(r.get("ts"), float)
    assert isinstance(r.get("trace_id"), str) and len(r["trace_id"]) == 8
    assert r.get("session_id") == "sid-001"
    assert r.get("event") == "test_event"
    assert r.get("foo") == "bar"
    print(f"  ✅ T1 basic emit (trace={r['trace_id']}, sid={r['session_id']})")


def test_t2_trace_id_propagation():
    """T2: trace_id ContextVar 跨多次 emit 应保持一致"""
    tmp = setup_temp_workspace()
    tlm = reload_telemetry()
    tid = tlm.new_trace()
    assert len(tid) == 8
    tlm.emit("e1", {"k": "v1"}, jsonl="t2.jsonl")
    tlm.emit("e2", {"k": "v2"}, jsonl="t2.jsonl")
    rows = read_jsonl(tmp / "telemetry" / "t2.jsonl")
    assert len(rows) == 2
    assert rows[0]["trace_id"] == tid
    assert rows[1]["trace_id"] == tid
    print(f"  ✅ T2 trace_id propagates (tid={tid})")


def test_t3_business_fields_preserved():
    """T3: 业务字段保留 + 不被基础字段覆盖"""
    tmp = setup_temp_workspace()
    tlm = reload_telemetry()
    biz = {
        "tool_name": "read_file",
        "duration_ms": 42,
        "args_keys": ["filepath", "limit"],
        "ts": 999.99,  # 故意伪造，应被基础字段覆盖
    }
    tlm.emit("tool_call", biz, jsonl="t3.jsonl", sid="sid-x", latency_ms=42)
    rows = read_jsonl(tmp / "telemetry" / "t3.jsonl")
    assert len(rows) == 1
    r = rows[0]
    assert r["tool_name"] == "read_file"
    assert r["args_keys"] == ["filepath", "limit"]
    assert r["latency_ms"] == 42
    # ts 应是真实时间戳，不是业务字段里的 999.99
    assert r["ts"] != 999.99
    print(f"  ✅ T3 business fields preserved + ts not overwritten")


def test_t4_failure_silent():
    """T4: 失败静默 — 异常 fields 不抛出（可能写部分）"""
    tmp = setup_temp_workspace()
    tlm = reload_telemetry()
    # fields 不是 dict — 应静默处理（不抛）
    ok = tlm.emit("bad_event", "not a dict", jsonl="t4.jsonl")
    # emit 应处理: 仍然写入基础 5 元组（不抛）
    assert isinstance(ok, bool)
    rows = read_jsonl(tmp / "telemetry" / "t4.jsonl")
    # 即使 fields 不合法，基础字段也应有
    if rows:
        assert "ts" in rows[0]
        assert "trace_id" in rows[0]
    print(f"  ✅ T4 failure silent (rows={len(rows)})")


def main():
    import traceback
    tests = [
        ("T1 basic emit", test_t1_basic_emit),
        ("T2 trace_id propagation", test_t2_trace_id_propagation),
        ("T3 business fields preserved", test_t3_business_fields_preserved),
        ("T4 failure silent", test_t4_failure_silent),
    ]
    print(f"=== P36-c 统一埋点入口白盒测试 ({len(tests)} cases) ===\n")
    pass_count = 0
    for name, fn in tests:
        try:
            fn()
            pass_count += 1
        except AssertionError as e:
            tb = traceback.format_exc().splitlines()[-3:-1]
            print(f"  ❌ {name}: AssertionError: {e}")
            for ln in tb: print(f"     {ln}")
        except Exception as e:
            print(f"  ❌ {name}: {type(e).__name__}: {e}")
    print(f"\nResults: {pass_count}/{len(tests)} PASS")
    sys.exit(0 if pass_count == len(tests) else 1)


if __name__ == "__main__":
    main()
