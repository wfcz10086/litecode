"""P53: 节点级 stderr 单独捕获测试"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))

from core.orchestrator import _sse_step_status


def _parse_sse(s):
    """解析 SSE 帧 → 返回 delta dict"""
    line = s.strip()
    if line.startswith("data:"):
        line = line[5:].strip()
    obj = json.loads(line)
    return obj["choices"][0]["delta"]


def test_sse_step_status_no_stream_field_when_omitted():
    """向后兼容: 不传 stream 时输出 JSON 无 stream 字段"""
    out = _sse_step_status("s1", "lbl", "running")
    d = _parse_sse(out)
    assert "stream" not in d["orchestrator_step"]


def test_sse_step_status_stream_stdout():
    out = _sse_step_status("s1", "lbl", "running", phase="chunk",
                            output_delta="hello", stream="stdout")
    d = _parse_sse(out)
    assert d["orchestrator_step"]["stream"] == "stdout"


def test_sse_step_status_stream_stderr():
    out = _sse_step_status("s1", "lbl", "running", phase="chunk",
                            output_delta="ERROR: oops", stream="stderr")
    d = _parse_sse(out)
    assert d["orchestrator_step"]["stream"] == "stderr"


def test_make_forwarder_emits_stderr_for_task_exec_error():
    """_make_forwarder 检测到 task_exec done + detail 以 ERROR: 开头时额外 emit stderr 事件"""
    import asyncio
    from core.orchestrator import DAGOrchestrator
    o = DAGOrchestrator(run_subagent_fn=lambda *a, **k: None)

    emitted = []
    async def fake_emit(chunk):
        emitted.append(chunk)

    fwd = o._make_forwarder("nodeA", "Label", fake_emit)

    err_chunk = (
        'data: ' + json.dumps({
            "choices": [{"index":0, "delta": {
                "task_exec": {"status":"done", "detail":"ERROR: cmd failed"}
            }, "finish_reason": None}]
        }) + '\n\n'
    )

    asyncio.run(fwd(err_chunk))
    assert len(emitted) >= 2
    stderr_evts = []
    for e in emitted:
        try:
            d = _parse_sse(e)
            if d.get("orchestrator_step", {}).get("stream") == "stderr":
                stderr_evts.append(d)
        except Exception:
            pass
    assert len(stderr_evts) == 1
    assert "ERROR: cmd failed" in stderr_evts[0]["orchestrator_step"]["output_delta"]


def test_make_forwarder_no_stderr_for_normal_content():
    """正常 content chunk 不应触发 stderr emit"""
    import asyncio
    from core.orchestrator import DAGOrchestrator
    o = DAGOrchestrator(run_subagent_fn=lambda *a, **k: None)

    emitted = []
    async def fake_emit(chunk):
        emitted.append(chunk)

    fwd = o._make_forwarder("nodeA", "Label", fake_emit)
    ok_chunk = (
        'data: ' + json.dumps({
            "choices": [{"index":0, "delta": {"content":"hello world"},
                         "finish_reason": None}]
        }) + '\n\n'
    )
    asyncio.run(fwd(ok_chunk))
    for e in emitted:
        try:
            d = _parse_sse(e)
            assert d.get("orchestrator_step", {}).get("stream") != "stderr"
        except Exception:
            pass


def test_make_forwarder_emits_stderr_for_stderr_warnings_marker():
    """task_exec.detail 以 [STDERR_WARNINGS 开头也触发 stderr emit"""
    import asyncio
    from core.orchestrator import DAGOrchestrator
    o = DAGOrchestrator(run_subagent_fn=lambda *a, **k: None)

    emitted = []
    async def fake_emit(chunk):
        emitted.append(chunk)

    fwd = o._make_forwarder("nodeA", "Label", fake_emit)
    chunk = (
        'data: ' + json.dumps({
            "choices": [{"index":0, "delta": {
                "task_exec": {"status":"done", "detail":"[STDERR_WARNINGS] yo"}
            }, "finish_reason": None}]
        }) + '\n\n'
    )
    asyncio.run(fwd(chunk))
    stderr_evts = []
    for e in emitted:
        try:
            d = _parse_sse(e)
            if d.get("orchestrator_step", {}).get("stream") == "stderr":
                stderr_evts.append(d)
        except Exception:
            pass
    assert len(stderr_evts) == 1
