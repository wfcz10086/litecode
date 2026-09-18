"""plugins/thinking — 统一推理参数适配器 (S1-H).

真实实现留在 lib/thinking_adapter.py (被多处 lazy import), 本包只提供:
  - 稳定的 chassis 命名: `from plugins.thinking import inject_params, ...`
  - Reference 用途: 未来把逻辑真搬进来时, 老代码路径 (lib.thinking_adapter)
    仍作为 shim 存在, 迁移期零改动.

暴露公共 API:
    ThinkingConfig                          # dataclass
    init(model_cfg)                         # 由 lib.config 启动时调用
    get()                                   # 返回当前 ThinkingConfig | None
    inject_params(payload, backend_type)    # 给 request payload 塞 thinking flag
    inject_for_compress(payload, backend)   # compress 阶段专用变体
    extract_content(msg)                    # 从 assistant msg 拆出正文/thinking
    update(**kwargs)                        # 热更新 (thinking / budget)
"""
from __future__ import annotations

try:
    from lib.thinking_adapter import (  # noqa: F401
        ThinkingConfig,
        init, get, update,
        inject_params, inject_for_compress,
        extract_content,
    )
except ImportError:
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from lib.thinking_adapter import (  # type: ignore  # noqa: F401
        ThinkingConfig,
        init, get, update,
        inject_params, inject_for_compress,
        extract_content,
    )
