"""HTTP API routers — 从 web_ui.py 单文件拆出来, 按功能分组.

每个 router 模块导出:
- router: APIRouter 实例
- init(state): 可选, 由 web_ui.py 启动时调用注入共享 state

web_ui.py 主文件只保留 app 初始化 + 共享 state + include_router.

插拔: 新增一组接口 = 在 routers/ 下加一个 <名>_router.py, 导出 router (可选 ORDER, 默认 500);
web_ui 启动时 discover() 自动按 (ORDER, 文件名) 升序 include, 不用再改 web_ui.py。
auth_router 在 CORS 之后单独先注册, 不参与自动发现; static_router 用 ORDER=999 兜底在最后。

仓外插件 (LITECODE_PLUGIN_PATH 下的 plugin.py, 契约见 plugins/_contract.md) 可选导出
ROUTER / ROUTER_ORDER / ROUTER_PUBLIC, 由 discover() 一并收集排序; 模块对象复用
plugins.registry 已导入的那一份。插件 ORDER 钳到 <= PLUGIN_MAX_ORDER, 保证 static 兜底最后;
ROUTER_PUBLIC 不为 True 时统一挂登录鉴权依赖。单个插件出错只记日志跳过, 不影响启动。
"""
import importlib
import logging
import os
import pkgutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, WebSocketException
from starlette.requests import HTTPConnection

DEFAULT_ORDER = 500
PLUGIN_MAX_ORDER = 998   # static_router 的 999 兜底必须最后
_SKIP = {"auth_router"}

log = logging.getLogger("routers")


def _builtin_routers():
    found = []
    for info in pkgutil.iter_modules(__path__):
        if not info.name.endswith("_router") or info.name in _SKIP:
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        router = getattr(module, "router", None)
        if router is None:
            raise RuntimeError(f"routers/{info.name}.py 没有导出 router")
        found.append((getattr(module, "ORDER", DEFAULT_ORDER), info.name, router))
    return found


def plugin_auth(conn: HTTPConnection):
    """插件路由的统一登录依赖: 复用 plugins.auth.core 的 cookie 校验 (与 require_auth 同源)。
    HTTP 未登录 → 401; WebSocket 未登录 → 1008 关闭。"""
    from plugins.auth import core as auth
    if auth._check_token(auth._get_cookie(conn)):
        return
    if conn.scope.get("type") == "websocket":
        raise WebSocketException(code=1008, reason="Unauthorized")
    raise HTTPException(status_code=401, detail="Unauthorized")


def _plugin_name(path: str) -> str:
    p = Path(path)
    return p.parent.name if p.name == "plugin.py" else f"{p.parent.parent.name}/{p.stem}"


def discover_plugin_routers():
    """收集仓外插件导出的 ROUTER → [(order, "plugin:<名>", router)]。
    未设置 LITECODE_PLUGIN_PATH 时直接返回空, 不触发插件扫描。任何单个插件出错都只记日志跳过。"""
    if not os.environ.get("LITECODE_PLUGIN_PATH", "").strip():
        return []
    try:
        from plugins.registry import registry
        modules = registry.external_modules()
    except Exception:
        log.exception("plugin router discovery unavailable")
        return []

    found = []
    for path, mod in modules:
        try:
            router = getattr(mod, "ROUTER", None)
            if router is None:
                continue
            if not isinstance(router, APIRouter):
                log.warning("plugin %s: ROUTER 不是 fastapi.APIRouter (%s), 跳过", path, type(router).__name__)
                continue
            order = getattr(mod, "ROUTER_ORDER", DEFAULT_ORDER)
            if isinstance(order, bool) or not isinstance(order, int):
                log.warning("plugin %s: ROUTER_ORDER 非 int (%r), 用默认 %d", path, order, DEFAULT_ORDER)
                order = DEFAULT_ORDER
            if order > PLUGIN_MAX_ORDER:
                log.warning("plugin %s: ROUTER_ORDER=%d 钳到 %d (static 兜底必须最后)", path, order, PLUGIN_MAX_ORDER)
                order = PLUGIN_MAX_ORDER
            if getattr(mod, "ROUTER_PUBLIC", False) is not True:
                # 不改插件自己的 router 对象: 包一层带鉴权依赖的 router
                wrapped = APIRouter(dependencies=[Depends(plugin_auth)])
                wrapped.include_router(router)
                router = wrapped
            found.append((order, f"plugin:{_plugin_name(path)}", router))
        except Exception:
            log.exception("plugin %s: ROUTER 挂载失败, 跳过", path)
    return found


def discover(include_plugins: bool = True):
    """返回待 include 的 APIRouter 列表 (已按注册顺序排好)。"""
    found = _builtin_routers()
    if include_plugins:
        found += discover_plugin_routers()
    return [router for _, _, router in sorted(found, key=lambda x: (x[0], x[1]))]
