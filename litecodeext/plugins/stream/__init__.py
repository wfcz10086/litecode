"""plugins/stream — SSE 事件格式化 chassis 出口 (S1-E).

真实实现在 lib/sse.py (被 litecode_server / v3 gateway / 多路由 lazy import).
本包提供 chassis 命名, 未来把逻辑真搬进来时老代码路径 (lib.sse) 保持 shim.

暴露:
    sse_content(text)       # assistant content 片段
    sse_reasoning(text)     # 推理/思考 片段
    sse_status(key, value)  # 通用 status delta (thinking 阶段 / tool 阶段)
    sse_meta(event,op,name) # 面板刷新 hint (dag/timer)
    sse_usage(...)          # 用量结算
    sse_stop() / sse_done() # 结束
    sse_error(reason, ...)  # 上游异常事件
    _mkid()                 # helper: 8位事件 id
"""
from __future__ import annotations

try:
    from lib.sse import (  # noqa: F401
        sse_content, sse_reasoning, sse_status, sse_stop, sse_done,
        sse_error, sse_meta, sse_usage, _mkid,
    )
except ImportError:
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from lib.sse import (  # type: ignore  # noqa: F401
        sse_content, sse_reasoning, sse_status, sse_stop, sse_done,
        sse_error, sse_meta, sse_usage, _mkid,
    )
