"""plugins/auth — 集中鉴权 (S1-G).

对外暴露:
    require_auth(request)        # 未授权抛 401
    init(cfg)                    # 由 web_ui.py 启动时调用
    router (FastAPI APIRouter)   # /api/login, /api/logout, /api/auth/status

老代码约定 `from routers.auth_router import require_auth` — 该 shim 保留
并全部指向本模块, 迁移零改动.
"""

from .core import (  # noqa: F401
    router,
    require_auth,
    init,
    _issue_token,
    _check_token,
    _get_cookie,
)
