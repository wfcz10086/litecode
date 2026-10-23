"""查完网页即关 + 桌面版没有 docker.sock 时给中文提示 (2026-10-23 用户反馈)。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import browser_search as bs  # noqa: E402
from plugins.tools import docker_ops  # noqa: E402


def test_browser_search_and_read_always_cleanup(monkeypatch):
    closed = []
    monkeypatch.setattr(bs, "_cleanup_session", closed.append)
    monkeypatch.setattr(bs, "_browser_search", lambda *a, **k: {"ok": True})

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(bs, "_browser_read", boom)

    assert bs.browser_search("q", engine="Bing")["ok"]
    try:
        bs.browser_read("https://example.com")
    except RuntimeError:
        pass
    assert closed[0] == "search-bot-bing"
    assert closed[1].startswith("reader-bot-")   # 每次读页面独立会话, 并发读互不关掉对方


def test_docker_missing_socket_message(monkeypatch):
    monkeypatch.setattr(docker_ops, "DOCKER_SOCK", "/nonexistent/docker.sock")
    r = asyncio.run(docker_ops.docker_ps())
    assert not r["ok"]
    assert "没有可用的 Docker" in r["error"] and "Errno" not in r["error"]
