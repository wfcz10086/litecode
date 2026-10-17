"""tests/ 根 conftest — 在线类用例默认排除

约定 (2026-10-17 建立):
  - `@pytest.mark.online` = 需要在线服务 (web_ui 18790 / server 18789)、真实浏览器
    (playwright + chromium)、docker 或真实模型的用例。
  - tests/web/ 下的全部用例 (Playwright E2E + 直打 web_ui API) 自动打上 online。
  - 默认 `python3 -m pytest -q tests/` 离线跑, online 用例被 deselect, 不连任何服务。

显式运行在线用例 (三选一):
  python3 -m pytest -q tests/web --run-online
  LITECODE_RUN_ONLINE=1 python3 -m pytest -q tests/
  python3 -m pytest -q tests/ -m online          # 只跑在线类
在线用例的目标地址用 LITECODE_WEB_URL 指定 (默认 http://localhost:18790 = 生产 web_ui!
建议指向测试实例, 见 tests/web/README.md), 登录密码用 LITECODE_TEST_PWD。
"""
import os
from pathlib import Path

import pytest

_WEB_DIR = Path(__file__).resolve().parent / "web"


def pytest_addoption(parser):
    parser.addoption(
        "--run-online", action="store_true", default=False,
        help="运行 @pytest.mark.online 用例 (需在线服务/浏览器); 默认排除",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "online: 需要在线服务/浏览器/docker/真实模型; 默认排除, --run-online 或 "
        "LITECODE_RUN_ONLINE=1 或 -m online 显式运行",
    )


def _online_enabled(config) -> bool:
    if config.getoption("--run-online", default=False):
        return True
    if os.environ.get("LITECODE_RUN_ONLINE", "").strip() in ("1", "true", "yes"):
        return True
    # 用户显式用 -m 选中 online (如 `-m online`) 时尊重其选择
    mexpr = config.getoption("-m", default="") or ""
    return "online" in mexpr and "not online" not in mexpr


def pytest_collection_modifyitems(config, items):
    for item in items:
        try:
            if Path(str(item.fspath)).resolve().is_relative_to(_WEB_DIR):
                item.add_marker(pytest.mark.online)
        except (OSError, ValueError):
            pass
    if _online_enabled(config):
        return
    keep, dropped = [], []
    for item in items:
        (dropped if item.get_closest_marker("online") else keep).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep
