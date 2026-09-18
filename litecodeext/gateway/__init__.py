"""gateway/ — OpenAI 兼容层 + 会话 / 内存 / 模型 / 可观测性 路由 (从 litecode_server.py 抽出).

用法:
    from fastapi import FastAPI
    from gateway import mount

    app = FastAPI()
    mount(app)

所有 handler 里 module-level state 都通过 `_deps._srv()` lazy 拿 litecode_server 引用,
保证 model_switch 热更新后仍拿到最新值. TOKEN / _sessions / _bg_procs 等既存在 lib.session /
tool_dispatch 也依旧直接从原模块导入 (行为与拆前一致).
"""
from __future__ import annotations

from fastapi import FastAPI

from . import memory as _memory
from . import model_mgmt as _model_mgmt
from . import observability as _observability
from . import openai_compat as _openai_compat
from . import sessions as _sessions


def mount(app: FastAPI) -> None:
    """把网关所有 router 挂到 app. 幂等."""
    app.include_router(_openai_compat.router)
    app.include_router(_sessions.router)
    app.include_router(_memory.router)
    app.include_router(_model_mgmt.router)
    app.include_router(_observability.router)


__all__ = ["mount"]
