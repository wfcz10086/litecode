"""空转排查 (2026-10-19) 第二批回归: 浏览器服务拉起 / 失败经验过期 / DAG 重试分类 /
comfyui 轮询 / agent_control wait 超时进展 / 后台进程早死诊断。全部本地, 不连网。"""
import asyncio
import json
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from core import browser_search as BS  # noqa: E402
from lib import failure_memory as fm  # noqa: E402
from lib.agent import subagent_registry as SR  # noqa: E402
from orchestrator import DAGOrchestrator, StepSpec, StepStatus  # noqa: E402
from orchestrator import step_mixin as SM  # noqa: E402
from orchestrator import helpers as OH  # noqa: E402
from plugins.tools import imagegen as IG  # noqa: E402
import tools.bg as BG  # noqa: E402


# ── 1. browser_server 拉起: cwd=脚本目录, node 起不来回退 python, 失败记忆 60s ──
class _FakeProc:
    def __init__(self, code):
        self.returncode = code

    def poll(self):
        return self.returncode


def _browser_env(monkeypatch, tmp_path, node_dies=True, py_ok=True):
    node_dir, py_dir = tmp_path / "skills_node", tmp_path / "skills_py"
    cands = [(["node", str(node_dir / "browser_server.js")], f"node:{node_dir}", str(node_dir)),
             ([sys.executable, str(py_dir / "browser_server.py")], f"python:{py_dir}", str(py_dir))]
    node_dir.mkdir(), py_dir.mkdir()
    launched = []

    def popen(cmd, **kw):
        launched.append((cmd, kw.get("cwd")))
        is_node = cmd[0] == "node"
        return _FakeProc(1 if (is_node and node_dies) or (not is_node and not py_ok) else None)

    def get(url, timeout=None):
        if launched and launched[-1][0][0] != "node" and py_ok:
            return types.SimpleNamespace(status_code=200)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(BS, "_resolve_browser_server_candidates", lambda: cands)
    monkeypatch.setattr(BS, "subprocess", types.SimpleNamespace(Popen=popen, DEVNULL=-3))
    monkeypatch.setattr(BS.httpx, "get", get)
    monkeypatch.setattr(BS, "_BROWSER_FAIL_UNTIL", 0.0)
    monkeypatch.setattr(BS.time, "sleep", lambda s: None)
    return launched


def test_browser_node_dies_falls_back_to_python_with_own_cwd(monkeypatch, tmp_path):
    launched = _browser_env(monkeypatch, tmp_path)
    assert BS._ensure_browser_server(timeout=5) is True
    assert [c[0][0] for c in launched] == ["node", sys.executable]
    assert launched[0][1].endswith("skills_node") and launched[1][1].endswith("skills_py")  # 不再写死 /opt/browser_node


def test_browser_start_failure_is_memoized(monkeypatch, tmp_path):
    launched = _browser_env(monkeypatch, tmp_path, py_ok=False)
    assert BS._ensure_browser_server(timeout=5) is False
    n = len(launched)
    t = time.time()
    assert BS._ensure_browser_server(timeout=5) is False
    assert len(launched) == n and time.time() - t < 1          # 60s 内不重新 spawn、不干等
    assert "启动失败过" in BS._BROWSER_LAST_ERR and "立即退出" in BS._BROWSER_LAST_ERR


def test_browser_candidates_use_script_dir():
    for cmd, desc, cwd in BS._resolve_browser_server_candidates():
        assert Path(cmd[-1]).parent == Path(cwd), (cmd, cwd)


# ── 2. 失败经验: 过期 / 去重取最新 / 成功清除 ──
def test_failure_memory_ttl_newest_and_resolve(tmp_path):
    task = "帮我把 config 解析模块重构成异步版本并补上单元测试"
    assert fm.record_failure(tmp_path, "max_iter", task, "拆步骤", extra={"detail": "old-error"})
    assert fm.record_failure(tmp_path, "max_iter", task, "拆步骤", extra={"detail": "new-error"}) is False
    rows = [json.loads(x) for x in fm._path(tmp_path).read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["detail"] == "new-error" and rows[0]["count"] == 2
    assert fm.find_similar(tmp_path, "", task)
    # 过期条目不再召回
    rows[0]["ts"] = time.time() - 8 * 86400
    fm._path(tmp_path).write_text(json.dumps(rows[0], ensure_ascii=False) + "\n")
    assert fm.find_similar(tmp_path, "", task) == []
    assert fm.find_similar(tmp_path, "", task, max_age_days=None)
    # 同一任务后来成功 → 清掉
    assert fm.resolve_similar(tmp_path, task, 0.85) == 1
    assert fm.find_similar(tmp_path, "", task, max_age_days=None) == []


def test_failure_loop_success_turn_resolves(tmp_path, monkeypatch):
    from lib.agent import failure_loop as fl
    monkeypatch.setattr(fl, "store_dir", lambda sid: tmp_path)
    monkeypatch.setattr(fl, "cfg", lambda: dict(fl._DEFAULTS))
    task = "把 docs 目录下的全部 markdown 文档整理成一份索引"
    fm.record_failure(tmp_path, "max_iter", fl.task_text(task), "拆步骤")
    ctx = types.SimpleNamespace(failure=None, new_msgs=[{"role": "assistant", "content": "整理好了"}],
                                session_id=None, sid_tag="t")
    assert fl.record_turn(ctx, task) is False
    assert fm.find_similar(tmp_path, "", fl.task_text(task)) == []


# ── 3. DAG 步重试: 循环内分类 ──
def _run_step(spec, outputs, monkeypatch):
    calls = []

    async def run(task, agent_type, context, sse, parent_sid=None):
        calls.append(task)
        return outputs[min(len(calls), len(outputs)) - 1]

    async def no_backoff(*a, **k):
        return None
    monkeypatch.setattr(SM, "sleep_backoff", no_backoff)
    orch = DAGOrchestrator(run)
    res = asyncio.run(orch._execute_step(spec, {}, None))
    return res, calls


def test_dag_permanent_error_not_retried(monkeypatch):
    res, calls = _run_step(StepSpec(id="a", task="A", max_retries=2),
                           ["[子代理异常] permission denied: /root/x"], monkeypatch)
    assert len(calls) == 1 and res.status == StepStatus.FAILED and res.attempts == 1


def test_dag_subagent_timeout_is_failure_and_retried_once(monkeypatch):
    res, calls = _run_step(StepSpec(id="a", task="A", max_retries=3),
                           ["[子代理超时] coder 运行超过 300s"], monkeypatch)
    assert res.status == StepStatus.FAILED and len(calls) == 2      # 原来判成功; 修后最多重试 1 次


def test_dag_ordinary_failure_still_uses_max_retries(monkeypatch):
    res, calls = _run_step(StepSpec(id="a", task="A", max_retries=2),
                           ["[子代理错误栈] 连续 3 次错误", "[子代理错误栈] 又错", "ok done"], monkeypatch)
    assert res.status == StepStatus.SUCCESS and len(calls) == 3 and res.attempts == 3


def test_tool_step_error_string_is_failure(monkeypatch):
    async def call(name, args, ctx):
        return "ERROR: file not found"
    reg = types.SimpleNamespace(get=lambda n: object(), names=lambda: ["t"], call=call)
    fake = types.ModuleType("plugins.registry")
    fake.registry, fake.scan_once_if_empty = reg, (lambda: None)
    monkeypatch.setitem(sys.modules, "plugins.registry", fake)
    out = asyncio.run(OH._run_tool_step(StepSpec(id="t", task="", agent_type="tool", tool_name="t"), {}, None, "t"))
    assert out.startswith("[tool-error]")


def test_evaluate_failure_not_registered_aborts():
    from orchestrator import StepAction, StepResult
    orch = DAGOrchestrator(lambda *a, **k: None)
    spec = StepSpec(id="t", task="", critical=False)
    r = StepResult(step_id="t", label="t", agent_type="tool", task="", status=StepStatus.FAILED,
                   output="", error="[tool-error] tool 'x' not registered. available: []", attempts=1)
    assert orch._evaluate_failure(spec, r, {}) == StepAction.ABORT


# ── 4. comfyui 轮询: 单请求超时 10s, 4xx 快速失败 ──
def _comfy(monkeypatch, tmp_path, history_handler, **cfg):
    tpl = tmp_path / "wf.json"
    tpl.write_text('{"1": {"inputs": {"text": "{{PROMPT}}"}}}')
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p1"})
        return history_handler(req)
    real = httpx.AsyncClient
    monkeypatch.setattr(IG.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    c = {"endpoint": "http://comfy.local", "workflow_template": str(tpl), "poll_interval_sec": 0.01}
    c.update(cfg)
    return seen, asyncio.run(IG._run_comfy("cat", c, {}))


def test_comfy_404_after_grace_fails_fast(monkeypatch, tmp_path):
    t = time.time()
    with pytest.raises(RuntimeError, match="404"):
        _comfy(monkeypatch, tmp_path, lambda r: httpx.Response(404, text="nope"),
               not_found_grace_sec=0.05, timeout_sec=60)
    assert time.time() - t < 5


def test_comfy_400_fails_immediately(monkeypatch, tmp_path):
    t = time.time()
    with pytest.raises(RuntimeError, match="400"):
        _comfy(monkeypatch, tmp_path, lambda r: httpx.Response(400), timeout_sec=180)
    assert time.time() - t < 3


def test_comfy_hung_get_bounded_by_deadline(monkeypatch, tmp_path):
    timeouts = []

    def hist(req):
        timeouts.append(req.extensions.get("timeout", {}).get("read"))
        raise httpx.ReadTimeout("hung", request=req)
    t = time.time()
    with pytest.raises(TimeoutError):
        _comfy(monkeypatch, tmp_path, hist, timeout_sec=0.3)
    assert time.time() - t < 3
    assert timeouts and all(x is not None and x <= 10 for x in timeouts)


# ── 5. agent_control wait 超时带最近进展 ──
def test_wait_timeout_reports_last_activity(monkeypatch):
    rec = SR.AgentRecord(id="sa-test01", sid="s-wait", agent_type="coder", task="修复构建", background=True)
    rec.iteration, rec.max_iter, rec.last_tool = 7, 30, "execute_shell(make)"
    rec.last_tool_at = rec.last_progress = time.time() - 200
    rec.last_output = "工具结果: make: *** [all] Error 2"
    monkeypatch.setitem(SR._RECORDS, rec.id, rec)
    monkeypatch.setattr(SR, "_interrupted", lambda sid: False)
    out = asyncio.run(SR._wait("s-wait", rec.id, 0))
    assert "距上次进展" in out and "execute_shell(make)" in out and "Error 2" in out
    assert "无进展" in out and "stop" in out


def test_tick_records_progress_snippet(monkeypatch):
    rec = SR.AgentRecord(id="sa-test02", sid="s", agent_type="coder", task="x")
    rec.last_progress = 0
    tok = SR._CURRENT.set(rec)
    try:
        SR.tick([{"role": "user", "content": "go"}, {"role": "tool", "content": "pytest: 3 failed"}], 4, 30, "coder")
    finally:
        SR._CURRENT.reset(tok)
    assert rec.iteration == 5 and "3 failed" in rec.last_output and time.time() - rec.last_progress < 5


# ── 6. 后台进程早死: 不重跑命令; 健康检查超时但进程活着 → 仍在启动 ──
def _fast_asyncio(monkeypatch):
    real_sleep = asyncio.sleep
    shim = types.SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio) if not k.startswith("__")})
    shim.sleep = lambda s, *a, **k: real_sleep(min(s, 0.05))
    monkeypatch.setattr(BG, "asyncio", shim)


def test_bg_died_early_does_not_rerun_command(monkeypatch, tmp_path):
    marker = tmp_path / "ran.txt"
    out = asyncio.run(BG._exec_bg(f"echo once >> {marker}; exit 3",
                                  health_url="http://127.0.0.1:9/health", cwd=tmp_path))
    assert "died after" in out and "日志为空" in out
    time.sleep(0.3)
    assert marker.read_text().count("once") == 1                   # 原来会在 WORKSPACE 再跑一遍


def test_bg_health_timeout_alive_says_still_starting(monkeypatch, tmp_path):
    _fast_asyncio(monkeypatch)
    out = asyncio.run(BG._exec_bg("echo booting; sleep 20", health_url="http://127.0.0.1:9/health", cwd=tmp_path))
    pid = out.split("pid=")[1].split(":")[0].split()[0]
    try:
        assert "still starting" in out and "不是失败" in out and "main.py" not in out
        assert "log_file=" in out and "booting" in out
    finally:
        import os
        import signal
        try:
            os.killpg(int(pid), signal.SIGKILL)
        except Exception:
            pass
