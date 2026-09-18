"""auth_router.py — 兼容 shim (S1-G).

真实实现已搬到 plugins/auth/core.py. 老代码路径
    from routers.auth_router import require_auth, router, init
不需要改, 全部从这里 re-export.
"""
try:
    from plugins.auth.core import (  # noqa: F401
        router, require_auth, init,
        _issue_token, _check_token, _get_cookie,
    )
except ImportError:  # 罕见: sys.path 缺 litecodeext 时
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from plugins.auth.core import (  # type: ignore  # noqa: F401
        router, require_auth, init,
        _issue_token, _check_token, _get_cookie,
    )
