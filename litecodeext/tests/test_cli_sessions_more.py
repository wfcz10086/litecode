"""litecli sessions 高级操作离线单测: rename/files/file/share/shares/unshare/sync/attach。

打桩 requests.Session.request (common._req 的底层), 用 CliRunner 调 litecli.cli, 不连任何服务。
attach 用假 SSE 流验证: 正文拼接、思考流、心跳/坏帧容错、[DONE] 即停 (之后的帧不再读)。
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
from lib.cli import cmd_sessions  # noqa: E402

BASE = "http://fake:1"


class FakeResp:
    def __init__(self, status=200, body=None, lines=None, text=None):
        self.status_code = status
        self._body = body
        self._lines = lines or []
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.content = self.text.encode()
        self.headers = {}
        self.consumed = []
        self.closed = False

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body

    def iter_lines(self, decode_unicode=False):
        for ln in self._lines:
            self.consumed.append(ln)
            yield ln.encode() if isinstance(ln, str) else ln

    def close(self):
        self.closed = True


class Server:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, json=None, params=None, **kw):
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
        self.calls.append((method, path, json, params, kw))
        r = self.routes.get((method, path))
        if callable(r):
            return r(json, params)
        return r or FakeResp(404, {"detail": "Not Found"})


@pytest.fixture
def run(monkeypatch):
    monkeypatch.delenv("LITECLI_OUTPUT", raising=False)

    def _run(routes, args, input=None):
        srv = Server(routes)
        monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: srv(*a, **k))
        res = CliRunner().invoke(litecli.cli, ["--url", BASE, *args], input=input)
        monkeypatch.delenv("LITECLI_OUTPUT", raising=False)
        return res, srv
    return _run


def test_rename(run):
    routes = {("PATCH", "/api/sessions/s1"): lambda j, p: FakeResp(200, {"ok": True, "name": j["name"]})}
    res, srv = run(routes, ["sessions", "rename", "s1", "周报", "整理"])
    assert res.exit_code == 0 and "周报 整理" in res.output
    assert srv.calls[0][:3] == ("PATCH", "/api/sessions/s1", {"name": "周报 整理"})
    res, _ = run({("PATCH", "/api/sessions/s1"): FakeResp(403, {"detail": "not owner"})},
                 ["sessions", "rename", "s1", "x"])
    assert res.exit_code == 1 and "403" in res.output and "not owner" in res.output


def test_files_and_file(run):
    files = {"sid": "s1", "files": [{"name": "AGENT.md", "size": 12, "mtime": 0, "editable": True},
                                    {"name": "memory/n.md", "size": 3, "mtime": 0, "editable": True}]}
    res, _ = run({("GET", "/api/sessions/s1/files"): FakeResp(200, files)}, ["sessions", "files", "s1"])
    assert res.exit_code == 0 and "AGENT.md" in res.output and "memory/n.md" in res.output
    res, _ = run({("GET", "/api/sessions/s1/files"): FakeResp(200, files)}, ["--json", "sessions", "files", "s1"])
    assert json.loads(res.output) == files
    res, _ = run({("GET", "/api/sessions/s1/files"): FakeResp(200, {"sid": "s1", "files": []})},
                 ["sessions", "files", "s1"])
    assert "（无）" in res.output

    one = {"name": "memory/n.md", "content": "# 笔记\n内容", "size": 9}
    res, srv = run({("GET", "/api/sessions/s1/files/memory/n.md"): FakeResp(200, one)},
                   ["sessions", "file", "s1", "memory/n.md"])
    assert res.exit_code == 0 and "# 笔记\n内容" in res.output
    res, _ = run({("GET", "/api/sessions/s1/files/memory/n.md"): FakeResp(200, one)},
                 ["--json", "sessions", "file", "s1", "memory/n.md"])
    assert json.loads(res.output)["size"] == 9
    res, _ = run({}, ["sessions", "file", "s1", "nope.md"])
    assert res.exit_code == 1 and "404" in res.output


def test_share_shares_unshare(run):
    rec = {"ok": True, "token": "TOK123456789", "sid": "s1", "created": 0, "expires": 1792165406,
           "read_only": True, "created_by": "admin"}
    res, srv = run({("POST", "/api/sessions/s1/share"): FakeResp(200, rec)}, ["sessions", "share", "s1"])
    assert res.exit_code == 0 and f"{BASE}/share/TOK123456789" in res.output
    assert srv.calls[0][2] == {"ttl_sec": 7 * 86400, "read_only": True}
    res, srv = run({("POST", "/api/sessions/s1/share"): FakeResp(200, dict(rec))},
                   ["--json", "sessions", "share", "s1", "--ttl", "3600"])
    assert json.loads(res.output)["url"] == f"{BASE}/share/TOK123456789"
    assert srv.calls[0][2]["ttl_sec"] == 3600

    lst = {"sid": "s1", "shares": [{k: v for k, v in rec.items() if k != "ok"}]}
    res, _ = run({("GET", "/api/sessions/s1/shares"): FakeResp(200, lst)}, ["sessions", "shares", "s1"])
    assert "TOK123456789" in res.output and f"{BASE}/share/TOK123456789" in res.output and "只读" in res.output
    res, _ = run({("GET", "/api/sessions/s1/shares"): FakeResp(200, json.loads(json.dumps(lst)))},
                 ["--json", "sessions", "shares", "s1"])
    assert json.loads(res.output)["shares"][0]["url"].endswith("/share/TOK123456789") and "/api/" not in json.loads(res.output)["shares"][0]["url"]
    res, _ = run({("GET", "/api/sessions/s1/shares"): FakeResp(200, {"sid": "s1", "shares": []})},
                 ["sessions", "shares", "s1"])
    assert "（无）" in res.output

    res, srv = run({("DELETE", "/api/sessions/s1/share/TOK123456789"): FakeResp(200, {"ok": True})},
                   ["sessions", "unshare", "s1", "TOK123456789"])
    assert res.exit_code == 0 and "已撤销" in res.output
    res, _ = run({("DELETE", "/api/sessions/s1/share/BAD"): FakeResp(200, {"ok": False})},
                 ["sessions", "unshare", "s1", "BAD"])
    assert res.exit_code == 1 and "撤销失败" in res.output
    res, _ = run({("DELETE", "/api/sessions/s1/share/OTHER"): FakeResp(403, {"detail": "token does not belong to sid"})},
                 ["sessions", "unshare", "s1", "OTHER"])
    assert res.exit_code == 1 and "does not belong" in res.output


def test_sync(run):
    body = {"synced": True, "messages": [{}, {}], "last_assistant": "最新回复", "still_running": False}
    res, _ = run({("GET", "/api/sessions/s1/sync"): FakeResp(200, body)}, ["sessions", "sync", "s1"])
    assert res.exit_code == 0 and "已同步新回复" in res.output and "msgs=2" in res.output and "最新回复" in res.output
    res, _ = run({("GET", "/api/sessions/s1/sync"): FakeResp(200, body)}, ["--json", "sessions", "sync", "s1"])
    assert json.loads(res.output)["synced"] is True
    res, _ = run({("GET", "/api/sessions/s1/sync"): FakeResp(200, dict(body, synced=False, error="boom"))},
                 ["sessions", "sync", "s1"])
    assert res.exit_code == 1 and "boom" in res.output


def _frame(**delta):
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def test_attach_streams_until_done(run):
    lines = [": keep-alive", "", _frame(reasoning="想一想"), _frame(content="你好"), "data: {坏帧",
             _frame(task_exec={"status": "executing", "detail": "bash ls", "key": "k"}),
             _frame(content="，世界"), "data: [DONE]", _frame(content="不该出现")]
    stream = FakeResp(200, lines=lines)
    routes = {("GET", "/api/chat/s1/run"): FakeResp(200, {"running": True, "frames": 7}),
              ("GET", "/api/chat/s1/stream"): stream}
    res, srv = run(routes, ["sessions", "attach", "s1", "--start", "3"])
    assert res.exit_code == 0, res.output
    assert "你好" in res.output and "，世界" in res.output and "不该出现" not in res.output
    assert "想一想" in res.output and "bash ls" in res.output and "本轮结束 (5 字符)" in res.output
    assert stream.consumed[-1] == "data: [DONE]" and stream.closed     # 读到 [DONE] 即停并关闭连接
    call = srv.calls[1]
    assert call[1] == "/api/chat/s1/stream" and call[3] == {"start": 3} and call[4].get("stream") is True

    stream2 = FakeResp(200, lines=[_frame(reasoning="想"), _frame(content="ok")])   # 无 [DONE] 流结束
    routes[("GET", "/api/chat/s1/stream")] = stream2
    res, _ = run(routes, ["sessions", "attach", "s1", "--hide-reasoning"])
    assert "ok" in res.output and "想" not in res.output.split("attach")[-1] and "流已结束" in res.output


def test_attach_not_running_and_race(run):
    res, srv = run({("GET", "/api/chat/s1/run"): FakeResp(200, {"running": False})}, ["sessions", "attach", "s1"])
    assert res.exit_code == 0 and "没有进行中的轮次" in res.output and len(srv.calls) == 1   # 不连 stream
    res, _ = run({("GET", "/api/chat/s1/run"): FakeResp(200, {"running": False, "frames": 12})},
                 ["sessions", "attach", "s1"])
    assert "12 帧" in res.output
    # 查询时在跑, 连 stream 时刚好结束 → 404 no turn: 友好提示
    res, _ = run({("GET", "/api/chat/s1/run"): FakeResp(200, {"running": True, "frames": 1}),
                  ("GET", "/api/chat/s1/stream"): FakeResp(404, {"detail": "no turn"})},
                 ["sessions", "attach", "s1"])
    assert res.exit_code == 0 and "刚刚结束" in res.output
    res, _ = run({("GET", "/api/chat/s1/run"): FakeResp(200, {"running": True}),
                  ("GET", "/api/chat/s1/stream"): FakeResp(500, text="oops")},
                 ["sessions", "attach", "s1"])
    assert res.exit_code == 1 and "HTTP 500" in res.output


def test_print_sse_concat_content():
    lines = [b": keep-alive", _frame(reasoning="r").encode(), _frame(content="你好").encode(),
             _frame(task_exec={"status": "executing", "detail": "t", "key": "k"}).encode(),
             _frame(content="，世界").encode(), b"data: [DONE]", _frame(content="x").encode()]
    out = cmd_sessions._print_sse(iter(lines))
    assert out == {"content": "你好，世界", "done": True, "errors": []}


def test_print_sse_error_frame():
    out = cmd_sessions._print_sse(['data: {"error": "HTTP 502"}', _frame(content="a"), "data: [DONE]"])
    assert out == {"content": "a", "done": True, "errors": ["HTTP 502"]}


def test_new_commands_have_chinese_help():
    for sub in ("rename", "files", "file", "share", "shares", "unshare", "sync", "attach"):
        res = CliRunner().invoke(litecli.cli, ["sessions", sub, "--help"])
        assert res.exit_code == 0
        assert any("一" <= ch <= "鿿" for ch in res.output), sub
