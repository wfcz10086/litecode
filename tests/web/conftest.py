"""
v1.5 P28 / v1.9 P39 — Web E2E 测试 fixture
==========================================
使用前:
  pip install pytest-playwright
  playwright install chromium

跑:
  pytest tests/web -v          # 默认 headless（CI）
  pytest tests/web -v --headed # 本地观察
"""
import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SNAPSHOTS_DIR = Path(__file__).resolve().parent / "snapshots"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _server_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    """快速 TCP 探测 server 是否在线"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


@pytest.fixture(scope="session")
def base_url():
    """Web UI 基址，可通过 LITECODE_WEB_URL env 覆盖"""
    return os.environ.get("LITECODE_WEB_URL", "http://localhost:18790")


@pytest.fixture(scope="session")
def base_host_port():
    """从 base_url 提取 host/port"""
    url = os.environ.get("LITECODE_WEB_URL", "http://localhost:18790")
    # http://host:port
    body = url.split("://", 1)[-1]
    host, _, port = body.partition(":")
    return host, int(port or 80)


@pytest.fixture(scope="session")
def server_alive(base_host_port):
    """Session-scoped: 探测 server 是否在线"""
    host, port = base_host_port
    return _server_reachable(host, port)


@pytest.fixture(scope="session")
def server_required(server_alive, base_url):
    """需要 server 的测试用此 fixture，server 不在则 skip"""
    if not server_alive:
        pytest.skip(f"Web server not reachable at {base_url} — start with `python3 litecodeext/web_ui.py` first")
    return base_url


@pytest.fixture
def auth_header():
    """Bearer token (legacy fixture，主要用于 API 直接请求测试)"""
    return {"Authorization": "Bearer CHANGE_ME_PASSWORD"}


@pytest.fixture
def auth_password():
    """Web UI 登录密码（默认 testpwd123，与 test_route_auth 一致）"""
    return os.environ.get("LITECODE_TEST_PWD", "testpwd123")


@pytest.fixture
def snapshots_dir():
    """截图基线目录"""
    return SNAPSHOTS_DIR


# ── 自动跳过缺 playwright 或 chromium 的环境 ───────────────────
def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                browser.close()
                return True
            except Exception:
                return False
    except ImportError:
        return False


PLAYWRIGHT_OK = _playwright_available()


@pytest.fixture
def playwright_required():
    """需要 playwright + chromium 的测试用此 fixture，缺则 skip"""
    if not PLAYWRIGHT_OK:
        pytest.skip("playwright/chromium 未安装或无法启动 — 跳过 E2E 测试")
