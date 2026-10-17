# Web E2E (P28)

骨架结构:
- conftest.py: pytest 全局 fixture
- pages/: Page Object Model (每页一个类)
- flows/: 端到端流程 (每个 .py 一个流程)
- snapshots/: 截图基线
- traces/: playwright trace.zip

使用:
  pip install pytest-playwright
  playwright install chromium
  pytest tests/web -v --headed   # 本地观察
  pytest tests/web -v             # CI headless

## 默认排除 / 显式运行 (2026-10-17)

本目录全部用例在 tests/conftest.py 中自动打上 `@pytest.mark.online`,
`python3 -m pytest -q tests/` 默认 deselect 它们 (离线、不连任何服务、不起浏览器)。
显式运行:

  LITECODE_WEB_URL=http://127.0.0.1:38001 LITECODE_TEST_PWD=<密码> \
      python3 -m pytest -q tests/web --run-online
  # 或 LITECODE_RUN_ONLINE=1 python3 -m pytest -q tests/   /   pytest tests/ -m online

LITECODE_WEB_URL 默认 http://localhost:18790 (生产 web_ui), 建议指向测试实例;
密码不匹配时 API 类用例会直接报 HTTPError 403 而不是 skip。
