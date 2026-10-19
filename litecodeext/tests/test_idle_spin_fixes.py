"""工具空转排查 (2026-10-19) 的回归用例: 代码审查 + 30 天生产日志里实际发生过的空转。"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import tools.bg as BG  # noqa: E402
from core.executor_v4 import (_exec_sync, classify_error, extract_host, extract_scheme,  # noqa: E402
                              is_network_cmd, probe_host)
from core.tool_dispatch import _infer_timeout  # noqa: E402


def test_unredirected_background_does_not_hold_foreground():
    t = time.time()
    r = _exec_sync("sleep 30 & echo started", 10, Path("/tmp"))
    assert r["output"] == "started" and time.time() - t < 3       # 原来卡满超时再连服务一起杀
    subprocess.run(["pkill", "-f", "^sleep 30$"])


def test_timeout_returns_partial_output_and_stdin_closed():
    r = _exec_sync("echo step1-done; sleep 5", 1, Path("/tmp"))
    assert r["output"].startswith("TIMEOUT") and "step1-done" in r["output"]
    t = time.time()
    r = _exec_sync("cat; echo after-cat", 10, Path("/tmp"))          # 读 stdin 的命令不再干等
    assert "after-cat" in r["output"] and time.time() - t < 3


def test_timeout_inference_reaches_executor_rules():
    assert _infer_timeout("sleep 45; tail run.log") == 75             # 原来上层恒给 30, 下层规则失效
    assert _infer_timeout("pytest -q") == 120
    assert _infer_timeout("ls") == 30


def test_service_detection_only_on_executed_command():
    for c in ("pip install uvicorn fastapi", "grep -r uvicorn .", "cat uvicorn.log", "pip install jupyter"):
        assert _infer_timeout(c) is not None, c
    for c in ("uvicorn app:app --port 8000", "cd x && nohup python3 -m http.server 8000",
              "python3 -m uvicorn a:b", "node app.js"):
        assert _infer_timeout(c) is None, c


def test_bg_exit_marker_via_trap():
    for cmd, code in (("set -e; false; echo no", 1), ("echo a; exit 3", 3), ("true", 0)):
        out = subprocess.run(BG._as_bash(cmd, mark_exit=True), shell=True, capture_output=True, text=True).stdout
        assert out.strip().splitlines()[-1] == f"[BG-EXIT code={code}]", cmd


def test_signal_kill_is_failure_and_side_effect_cmds_not_retried():
    assert classify_error("partial", -11, "python3 render.py") == "failed"
    assert classify_error("", -15, "pkill -f render") == "ok"
    assert is_network_cmd("curl -s http://x/api") and is_network_cmd("pip install x")
    for c in ("curl -X POST http://x/api", "curl -d a=1 http://x", "ssh host ./migrate.sh"):
        assert not is_network_cmd(c), c


def test_probe_uses_real_port_and_short_negative_cache(monkeypatch):
    import socket
    import core.executor_v4 as E
    cmd = "curl -s http://your-host-a.example:54321/api/images/657"
    assert extract_host(cmd) == "your-host-a.example:54321" and extract_scheme(cmd) == "http"
    seen = []

    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(addr, timeout=3):
        seen.append(addr)
        if addr[1] == 54321:
            return _C()
        raise OSError("refused")
    monkeypatch.setattr(socket, "create_connection", fake)
    E._NET_PROBE_CACHE.clear()
    assert probe_host("your-host-a.example:54321", scheme="http") is True and seen[-1] == ("your-host-a.example", 54321)
    assert probe_host("example.org", scheme="http") is False and seen[-1] == ("example.org", 80)
    ok, ts = E._NET_PROBE_CACHE["example.org:80"]
    E._NET_PROBE_CACHE["example.org:80"] = (ok, ts - 31)              # 失败结果 30s 后重新探
    probe_host("example.org", scheme="http")
    assert seen.count(("example.org", 80)) == 2


def test_subagent_cache_only_for_pure_web_roles():
    from lib.agent.subagent import _cacheable
    for role in ("tester", "coder", "explorer", "shell", "operator", "vision"):
        assert not _cacheable(role), role                              # 原来 tester 旧失败结果缓存 1 小时


def test_loop_detector_needs_same_result_and_full_signature():
    from core.itrace import Tracer
    tr = Tracer.__new__(Tracer)
    tr._tool_history = []
    import hashlib
    import json

    def push(name, args, result):
        sig = f"{name}:{hashlib.md5(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()}"
        h = hashlib.md5(result[:500].encode()).hexdigest()[:8]
        tr._tool_history.append({"sig": sig, "name": name, "result_hash": h, "is_error": False, "_result_head": ""})
        return tr._detect_loop(name, args, result, False, h)
    head = "#" * 300
    assert not any(push("write_file", {"content": head + str(i), "filepath": f"f{i}.py"}, "ok") for i in range(3))
    tr._tool_history = []
    res = [push("execute_shell", {"command": "curl -s x/status"}, f"progress {i}") for i in range(3)]
    assert res[-1] is False                                            # 结果在变 = 在推进
    tr._tool_history = []
    res = [push("execute_shell", {"command": "curl -s x/status"}, "same") for i in range(3)]
    assert res[-1] is True
    tr._tool_history = []
    assert not any(push("agent_control", {"action": "wait", "id": "a1"}, "running") for _ in range(5))


def test_video_pipeline_prints_fixed_end_line():
    src = (ROOT / "skills" / "douyin-explainer-video" / "scripts" / "run.py").read_text(encoding="utf-8")
    assert 'print("RUN-END ok"' in src and "RUN-END fail step=" in src


def test_transport_does_not_resend_after_partial_stream():
    src = (ROOT / "lib" / "transport.py").read_text(encoding="utf-8")
    assert src.count("if _yielded:") >= 2 and "_yielded = True" in src


def test_web_search_engines_use_separate_pages():
    src = (ROOT / "core" / "browser_search.py").read_text(encoding="utf-8")
    assert 'session = f"search-bot-{engine.lower()}"' in src
    assert src.count("timeout=50.0") >= 2
