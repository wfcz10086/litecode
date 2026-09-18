"""HTTP API routers — 从 web_ui.py 单文件拆出来, 按功能分组.

每个 router 模块导出:
- router: APIRouter 实例
- init(state): 可选, 由 web_ui.py 启动时调用注入共享 state

web_ui.py 主文件只保留 app 初始化 + 共享 state + include_router.
"""
