"""HTTP API routers — 从 web_ui.py 单文件拆出来, 按功能分组.

每个 router 模块导出:
- router: APIRouter 实例
- init(state): 可选, 由 web_ui.py 启动时调用注入共享 state

web_ui.py 主文件只保留 app 初始化 + 共享 state + include_router.

插拔: 新增一组接口 = 在 routers/ 下加一个 <名>_router.py, 导出 router (可选 ORDER, 默认 500);
web_ui 启动时 discover() 自动按 (ORDER, 文件名) 升序 include, 不用再改 web_ui.py。
auth_router 在 CORS 之后单独先注册, 不参与自动发现; static_router 用 ORDER=999 兜底在最后。
"""
import importlib
import pkgutil

DEFAULT_ORDER = 500
_SKIP = {"auth_router"}


def discover():
    """返回待 include 的 APIRouter 列表 (已按注册顺序排好)。"""
    found = []
    for info in pkgutil.iter_modules(__path__):
        if not info.name.endswith("_router") or info.name in _SKIP:
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        router = getattr(module, "router", None)
        if router is None:
            raise RuntimeError(f"routers/{info.name}.py 没有导出 router")
        found.append((getattr(module, "ORDER", DEFAULT_ORDER), info.name, router))
    return [router for _, _, router in sorted(found, key=lambda x: (x[0], x[1]))]
