"""artifacts — owner-scoped 产出物仓库 (S1-N).

Owner 命名约定:
    timer:{id}                  定时器任务
    bg:{pid}                    后台进程
    plugin:{name}:{job_id}      插件执行 (cad/pptx/patents/imagegen ...)
    session:{sid}:tool:{tcid}   会话内单次 tool 调用

用法:
    from plugins.artifacts import get_store
    store = get_store()
    aid = store.put(owner="plugin:cad_generate:abc", kind="dxf",
                    data=dxf_bytes, meta={"name": "part.dxf"})
    meta = store.meta(aid)
    data = store.blob(aid)
    lst  = store.list_by_owner("plugin:cad_generate:abc")

给 plugin ctx 用的 emit_artifact 助手 (registry.call 会拼进 ctx):
    ctx["emit_artifact"](kind="dxf", data=..., name="part.dxf", meta={...})
    → 内部自动 owner="plugin:{name}:{job_id}", 返回 aid
"""

from __future__ import annotations
import uuid
from typing import Optional, Union, Callable

from .store import ArtifactStore, get_store, ArtifactRecord  # noqa: F401


def build_emit_artifact(owner: str) -> Callable[..., str]:
    """构造一个绑定到指定 owner 的 emit_artifact 闭包.

    ctx["emit_artifact"](kind, data, name="", meta=None) → aid
    """
    def _emit(kind: str,
              data: Union[bytes, str],
              name: str = "",
              meta: Optional[dict] = None) -> str:
        return get_store().put(owner=owner, kind=kind, data=data,
                               name=name, meta=meta)
    return _emit


def new_plugin_owner(plugin_name: str, job_id: Optional[str] = None) -> str:
    """生成 owner 字符串: plugin:{name}:{job_id or uuid12}."""
    jid = job_id or uuid.uuid4().hex[:12]
    return f"plugin:{plugin_name}:{jid}"
