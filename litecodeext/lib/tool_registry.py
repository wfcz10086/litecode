"""tool_registry.py — 工具统一视图: 名称 → schema / 执行来源 / 能力。

LiteCode 有两套工具注册:
  主 agent  lib/tools/defs (TOOL_DEFS, 发给模型) + core/tools/handlers (@register, execute_tool 分派)
  DAG 工具步  plugins/tools/*.py 的 TOOLS (plugins.registry 扫描, run(args, ctx))
两边互不相通 (见 plugins/tools/vision.py)。这里只做汇总与查询, 不改任何一边:
TOOL_DEFS 的内容与顺序不动 (那是每轮发给模型的前缀, 动了就打掉 KV 缓存)。

合并规则 (同名即同一个工具, 不算冲突):
  schema       主 agent 的 TOOL_DEFS 为准; 只在插件里的工具用插件自己的 schema
  capabilities 插件声明 < lib/tool_capabilities.py 旁挂表 (后者覆盖前者)
  冲突          只有"同一来源里出现两次同名"才算, 由 conflicts 列出
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .tool_capabilities import CAPABILITIES


@dataclass
class ToolEntry:
    name: str
    description: str = ""
    parameters: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    sources: set = field(default_factory=set)   # {"agent", "handler", "plugin"}


@dataclass
class ToolIndex:
    entries: dict[str, ToolEntry]
    conflicts: list[str]

    def names(self, **caps: Any) -> list[str]:
        """按能力筛选, 如 names(read_only=True)。"""
        return [n for n, e in self.entries.items()
                if all(e.capabilities.get(k) == v for k, v in caps.items())]

    def dag_bridge_names(self) -> list[str]:
        """主 agent 有 handler、插件里没有、且允许进 DAG 的工具 —— 需要桥接给 DAG 工具步。"""
        return [n for n, e in self.entries.items()
                if "handler" in e.sources and "plugin" not in e.sources
                and e.capabilities.get("dag", True)]


def _dupes(names: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for n in names:
        if n in seen and n not in out:
            out.append(n)
        seen.add(n)
    return out


def build(tool_defs: list, handler_names: Iterable[str], plugin_tools: Iterable[dict],
          capabilities: dict | None = None) -> ToolIndex:
    """纯函数。plugin_tools: [{name, description, schema, capabilities}]。"""
    caps_table = CAPABILITIES if capabilities is None else capabilities
    plugin_tools = list(plugin_tools)
    entries: dict[str, ToolEntry] = {}

    agent_names = []
    for d in tool_defs:
        fn = d.get("function") or {}
        n = fn.get("name", "")
        agent_names.append(n)
        entries.setdefault(n, ToolEntry(n, fn.get("description", ""), fn.get("parameters") or {}))
        entries[n].sources.add("agent")

    for n in handler_names:
        entries.setdefault(n, ToolEntry(n)).sources.add("handler")

    for p in plugin_tools:
        n = p["name"]
        e = entries.setdefault(n, ToolEntry(n, p.get("description", ""), p.get("schema") or {}))
        e.sources.add("plugin")
        e.capabilities = {**(p.get("capabilities") or {}), **e.capabilities}

    for n, e in entries.items():
        e.capabilities = {**e.capabilities, **caps_table.get(n, {})}

    conflicts = _dupes(agent_names) + _dupes(p["name"] for p in plugin_tools)
    return ToolIndex(entries, conflicts)


def read_only_names() -> frozenset:
    """并发预取白名单 (lib/tool_prefetch.py)。只看旁挂表, 不触发重模块导入。"""
    return frozenset(n for n, c in CAPABILITIES.items() if c.get("read_only"))


# ── 运行时: 从真实模块汇总 ──────────────────────────────────────

_cached: ToolIndex | None = None


def _plugin_tools() -> list[dict]:
    try:
        from plugins.registry import registry
    except ImportError:
        return []
    if not registry.list():   # 不用 names(): 它含桥接项, 会反过来调 unified()
        registry.scan()
    return [{"name": s.name, "description": s.description, "schema": s.schema,
             "capabilities": s.capabilities} for s in registry.list()]


def unified(refresh: bool = False) -> ToolIndex:
    global _cached
    if _cached is None or refresh:
        from .tools.defs import TOOL_DEFS
        try:
            from core.tools.handlers import REGISTRY
        except ImportError:
            from tools.handlers import REGISTRY  # type: ignore
        _cached = build(TOOL_DEFS, REGISTRY.keys(), _plugin_tools())
    return _cached


def agent_runner(name: str):
    """DAG 契约的 run(args, ctx) → 转发给主 agent 的 execute_tool。"""
    async def _run(args: dict, ctx: dict) -> dict:
        try:
            from core.tool_dispatch import execute_tool
        except ImportError:
            from tool_dispatch import execute_tool  # type: ignore
        ret = await execute_tool(name, args or {}, (ctx or {}).get("sid") or "")
        result, extra = ret if isinstance(ret, tuple) else (ret, None)
        if isinstance(result, str) and result.startswith("ERROR"):
            return {"ok": False, "error": result}
        out: dict[str, Any] = {"ok": True, "result": result}
        if extra:
            out["artifact"] = extra
        return out
    _run.__name__ = f"run_{name}"
    return _run
