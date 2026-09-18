"""Tests for P44: _sse_step_status phase fields + _make_forwarder dual-emit."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext"))
sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext" / "core"))

from core.orchestrator import _sse_step_status, DAGOrchestrator


def _parse_sse(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("data:"):
        raw = raw[5:].strip()
    return json.loads(raw)


def _delta(raw: str) -> dict:
    return _parse_sse(raw)["choices"][0]["delta"]["orchestrator_step"]


# ── P44-a: _sse_step_status 向后兼容 + 新字段 ────────────────────────────────

def test_sse_step_status_legacy():
    raw = _sse_step_status("s1", "Step1", "running")
    d = _delta(raw)
    assert "phase" not in d
    assert "output_delta" not in d
    assert "elapsed_ms" not in d
    assert d["status"] == "running"


def test_sse_step_status_with_phase():
    raw = _sse_step_status("s1", "Step1", "running", phase="start")
    d = _delta(raw)
    assert d["phase"] == "start"


def test_sse_step_status_with_output_delta():
    raw = _sse_step_status("s1", "Step1", "running", phase="chunk", output_delta="hello")
    d = _delta(raw)
    assert d["phase"] == "chunk"
    assert d["output_delta"] == "hello"


def test_sse_step_status_with_elapsed_ms():
    raw = _sse_step_status("s1", "Step1", "success", phase="end", elapsed_ms=1234)
    d = _delta(raw)
    assert d["phase"] == "end"
    assert d["elapsed_ms"] == 1234


# ── P44-b: _make_forwarder 透传 + 解析增量 ───────────────────────────────────

def _make_orch() -> DAGOrchestrator:
    async def _dummy_run(*a, **kw):
        return "ok"
    return DAGOrchestrator(_dummy_run)


def _chunk(content=None, reasoning=None) -> str:
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning"] = reasoning
    return f'data: {json.dumps({"choices": [{"index": 0, "delta": delta}]})}\n\n'


def test_make_forwarder_passthrough():
    calls = []
    async def fake_emit(c):
        calls.append(c)

    orch = _make_orch()
    fwd = orch._make_forwarder("node1", "Node1", fake_emit)

    asyncio.run(fwd(_chunk(content="hello")))
    assert len(calls) >= 1
    # first call is the raw passthrough
    assert "hello" in calls[0]


def test_make_forwarder_extract_content():
    calls = []
    async def fake_emit(c):
        calls.append(c)

    orch = _make_orch()
    fwd = orch._make_forwarder("node1", "Node1", fake_emit)

    asyncio.run(fwd(_chunk(content="abc")))
    assert len(calls) >= 2
    # second call should be a step-scoped orchestrator_step with output_delta
    second_delta = _delta(calls[1])
    assert second_delta.get("output_delta") == "abc"
    assert second_delta.get("phase") == "chunk"
    assert second_delta.get("step_id") == "node1"


def test_make_forwarder_bad_chunk_silent():
    calls = []
    async def fake_emit(c):
        calls.append(c)

    orch = _make_orch()
    fwd = orch._make_forwarder("node1", "Node1", fake_emit)

    # non-JSON garbage — must not raise, passthrough still happens
    asyncio.run(fwd("not json at all !!!"))
    assert len(calls) >= 1
    assert calls[0] == "not json at all !!!"
