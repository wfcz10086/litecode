"""litecli dag job 系命令离线单测: job/log/artifacts/abort/breakpoints/break/resume + jobs -n。

打桩 requests.Session.request (common._req 的底层), 用 CliRunner 调 litecli.cli, 不连任何服务。
"""
import json
import sys
from pathlib import Path

import pytest
import requests
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
import litecli  # noqa: E402

JOB = {
    "name": "weekly", "started": 1792165406.36, "status": "done", "step_count": 3, "elapsed": 12.5,
    "breakpoints": ["write"], "paused_at": None, "final_output": "周报已生成",
    "agents": [
        {"step_id": "fetch", "agent_type": "researcher", "success": True, "elapsed": 3.2, "error": "",
         "output_preview": "抓到 10 条"},
        {"step_id": "gate", "agent_type": "analyst", "success": False, "elapsed": 0, "error": "条件不满足: x"},
        {"step_id": "write", "agent_type": "writer", "success": False, "elapsed": 1.0, "error": "超时"},
    ],
}
LOG = {"job_id": "j1", "name": "weekly", "status": "done",
       "events": [{"t": 1, "event": "step_done", "step_id": "fetch"}],
       "text": "DAG: weekly | job j1 | status=done\n  +   0.0s  ✓ fetch 完成"}
ARTS = {"job_id": "j1", "artifacts": [
    {"stage": "write", "kind": "file", "path": "/tmp/litecode_workspace/out/r.md", "name": "r.md", "size": 5},
    {"stage": "fetch", "kind": "file", "path": "/tmp/litecode_workspace/a/r.md", "name": "r.md", "size": 3},
    {"stage": "fetch", "kind": "url", "url": "https://example.com/x", "name": "x"},
]}


class FakeResp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code = status
        self._body = body
        self.content = content if body is None else json.dumps(body).encode()
        self.text = self.content.decode("utf-8", "replace")
        self.headers = {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class Server:
    """路由表: (METHOD, path) -> FakeResp; 记录所有调用."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, sess, method, url, json=None, params=None, **kw):
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path
        self.calls.append((method, path, json, params))
        r = self.routes.get((method, path))
        return r(json, params) if callable(r) else (r or FakeResp(404, {"error": "job not found"}))


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.delenv("LITECLI_OUTPUT", raising=False)   # 全局 --json 会写 os.environ, 用例间隔离

    def _run(routes, args, input=None):
        srv = Server(routes)
        monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: srv(self, *a, **k))
        res = CliRunner().invoke(litecli.cli, args, input=input)
        monkeypatch.delenv("LITECLI_OUTPUT", raising=False)
        return res, srv
    return _run


def test_job_detail_human(run):
    res, srv = run({("GET", "/api/dags/jobs/j1"): FakeResp(200, JOB)}, ["dag", "job", "j1"])
    assert res.exit_code == 0, res.output
    out = res.output
    assert "weekly" in out and "done" in out and "步数: 3" in out
    assert "✓ fetch" in out and "⏭ gate" in out and "✗ write" in out and "超时" in out
    assert "断点: write" in out and "周报已生成" in out
    assert srv.calls[0][:2] == ("GET", "/api/dags/jobs/j1")


def test_job_unfinished_step_marks(run):
    job = dict(JOB, status="aborted", agents=[{"step_id": "a", "agent_type": "analyst", "success": False,
                                                "error": ""}])
    res, _ = run({("GET", "/api/dags/jobs/j1"): FakeResp(200, job)}, ["dag", "job", "j1"])
    assert "■ a" in res.output
    job["status"] = "running"
    res, _ = run({("GET", "/api/dags/jobs/j1"): FakeResp(200, job)}, ["dag", "job", "j1"])
    assert "▶ a" in res.output


def test_job_json_and_404(run):
    res, _ = run({("GET", "/api/dags/jobs/j1"): FakeResp(200, JOB)}, ["--json", "dag", "job", "j1"])
    assert res.exit_code == 0 and json.loads(res.output)["final_output"] == "周报已生成"
    res, _ = run({}, ["dag", "job", "nope"])
    assert res.exit_code == 1 and "HTTP 404" in res.output and "job not found" in res.output


def test_log(run):
    routes = {("GET", "/api/dags/jobs/j1/log"): FakeResp(200, LOG)}
    res, _ = run(routes, ["dag", "log", "j1"])
    assert res.exit_code == 0 and "✓ fetch 完成" in res.output and "（无）" not in res.output
    res, _ = run(routes, ["--json", "dag", "log", "j1"])
    assert json.loads(res.output)["events"][0]["step_id"] == "fetch"
    empty = dict(LOG, events=[], text="DAG: weekly | job j1 | status=running")
    res, _ = run({("GET", "/api/dags/jobs/j1/log"): FakeResp(200, empty)}, ["dag", "log", "j1"])
    assert "（无）" in res.output
    res, _ = run({}, ["dag", "log", "nope"])
    assert res.exit_code == 1


def test_artifacts_list_json_empty(run):
    routes = {("GET", "/api/dags/jobs/j1/artifacts"): FakeResp(200, ARTS)}
    res, _ = run(routes, ["dag", "artifacts", "j1"])
    assert res.exit_code == 0
    assert "/tmp/litecode_workspace/out/r.md" in res.output and "https://example.com/x" in res.output
    res, _ = run(routes, ["--json", "dag", "artifacts", "j1"])
    assert len(json.loads(res.output)["artifacts"]) == 3
    res, _ = run({("GET", "/api/dags/jobs/j1/artifacts"): FakeResp(200, {"job_id": "j1", "artifacts": []})},
                 ["dag", "artifacts", "j1"])
    assert "（无）" in res.output


def test_artifacts_download(run, tmp_path):
    def media(_json, params):
        if params["path"].endswith("out/r.md"):
            return FakeResp(200, content=b"hello")
        return FakeResp(403, {"detail": "forbidden"})
    routes = {("GET", "/api/dags/jobs/j1/artifacts"): FakeResp(200, ARTS), ("GET", "/api/media"): media}
    res, srv = run(routes, ["dag", "artifacts", "j1", "--download", str(tmp_path / "dl")])
    assert (tmp_path / "dl" / "r.md").read_bytes() == b"hello"
    assert not (tmp_path / "dl" / "fetch_r.md").exists()          # 403 的那个没落盘
    assert res.exit_code == 1 and "HTTP 403" in res.output and "失败 1 个" in res.output
    assert [c for c in srv.calls if c[1] == "/api/media"].__len__() == 2   # url 类产物不下载

    def media_ok(_json, params):
        return FakeResp(200, content=params["path"].encode())
    routes[("GET", "/api/media")] = media_ok
    res, _ = run(routes, ["--json", "dag", "artifacts", "j1", "--download", str(tmp_path / "dl2")])
    assert res.exit_code == 0
    d = json.loads(res.output)
    assert sorted(Path(p).name for p in d["downloaded"]) == ["fetch_r.md", "r.md"]   # 同名加 stage 前缀


def test_abort_confirm_and_yes(run):
    routes = {("POST", "/api/dags/jobs/j1/abort"): FakeResp(200, {"ok": True, "status": "aborting"})}
    res, srv = run(routes, ["dag", "abort", "j1"], input="n\n")
    assert res.exit_code == 1 and "确认中止" in res.output and not srv.calls    # 拒绝 → 不发请求
    res, srv = run(routes, ["dag", "abort", "j1"], input="y\n")
    assert res.exit_code == 0 and srv.calls[0][:2] == ("POST", "/api/dags/jobs/j1/abort")
    res, srv = run(routes, ["dag", "abort", "j1", "-y"])
    assert res.exit_code == 0 and "已发送中止" in res.output and "确认" not in res.output
    res, _ = run({("POST", "/api/dags/jobs/j1/abort"): FakeResp(200, {"ok": False, "already": "done"})},
                 ["dag", "abort", "j1", "-y"])
    assert res.exit_code == 0 and "不在运行" in res.output and "done" in res.output
    res, _ = run({}, ["dag", "abort", "nope", "-y"])
    assert res.exit_code == 1 and "404" in res.output


def test_breakpoints_break_resume(run):
    bp = {"breakpoints": ["b", "c"], "paused_at": "b"}
    res, _ = run({("GET", "/api/dags/jobs/j1/breakpoints"): FakeResp(200, bp)}, ["dag", "breakpoints", "j1"])
    assert res.exit_code == 0 and "暂停于: b" in res.output and "● c" in res.output
    res, _ = run({("GET", "/api/dags/jobs/j1/breakpoints"): FakeResp(200, bp)},
                 ["--json", "dag", "breakpoints", "j1"])
    assert json.loads(res.output) == bp
    res, _ = run({("GET", "/api/dags/jobs/j1/breakpoints"): FakeResp(200, {"breakpoints": [], "paused_at": None})},
                 ["dag", "breakpoints", "j1"])
    assert "（无）" in res.output

    path = "/api/dags/jobs/j1/breakpoints/c"
    res, srv = run({("POST", path): FakeResp(200, {"ok": True, "breakpoints": ["c"], "paused_at": None})},
                   ["dag", "break", "j1", "c"])
    assert res.exit_code == 0 and srv.calls[0][2] == {"set": True} and "已设置" in res.output
    res, srv = run({("POST", path): FakeResp(200, {"ok": True, "breakpoints": [], "paused_at": None})},
                   ["dag", "break", "j1", "c", "--clear"])
    assert srv.calls[0][2] == {"set": False} and "已取消" in res.output

    res, srv = run({("POST", "/api/dags/jobs/j1/resume/b"): FakeResp(200, {"ok": True})},
                   ["dag", "resume", "j1", "b"])
    assert res.exit_code == 0 and "已继续" in res.output
    res, _ = run({}, ["dag", "resume", "nope", "b"])
    assert res.exit_code == 1


def test_jobs_limit_and_default_unchanged(run):
    jobs = {"jobs": [{"job_id": f"j{i}", "name": "n", "status": "done", "started": 0} for i in range(5)]}
    res, _ = run({("GET", "/api/dags/jobs"): FakeResp(200, jobs)}, ["dag", "jobs"])
    assert res.output.count("  j") == 5
    res, _ = run({("GET", "/api/dags/jobs"): FakeResp(200, jobs)}, ["dag", "jobs", "-n", "2"])
    assert res.output.count("  j") == 2
    res, _ = run({("GET", "/api/dags/jobs"): FakeResp(200, jobs)}, ["--json", "dag", "jobs", "--limit", "3"])
    assert len(json.loads(res.output)["jobs"]) == 3


def test_new_commands_have_chinese_help(run):
    for sub in ("job", "log", "artifacts", "abort", "breakpoints", "break", "resume"):
        res = CliRunner().invoke(litecli.cli, ["dag", sub, "--help"])
        assert res.exit_code == 0
        assert any("一" <= ch <= "鿿" for ch in res.output), sub
