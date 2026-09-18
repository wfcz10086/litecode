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
