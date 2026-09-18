# plugins/_contract.py — LiteCode 机架 (chassis) 插件契约
#
# 一句话: LiteCode 只是机架, 外部 (/opt/cad, /opt/pptx) 与内部 (plugins/tools/*)
# 通过同一份 ABI 挂进来. 拔环境变量即卸.
#
# 一个合格的 plugin 模块必须暴露:
#   NAME:        str            工具名 (会作为 tool_call 的 name)
#   DESCRIPTION: str            LLM 可读的说明
#   SCHEMA:      dict           JSON-Schema (OpenAI function-calling 兼容)
#   CAPABILITIES: dict[str, Any]   见 CAPABILITY_KEYS
#   async run(args, ctx) -> dict   实际执行体
#
# 可选:
#   VERSION:     str            默认 "0.0.1"
#   AUTHOR:      str
#   HEALTH_CHECK: async () -> bool   给管理页轮询用
#
# ctx (PluginCtx) 提供:
#   ctx["sid"]           会话 id
#   ctx["workspace"]     用户工作区路径
#   ctx["logger"]        logging.Logger
#   ctx["emit_artifact"](kind, payload, meta=None) -> str  产出物落到 artifact_store, 返回 artifact_id
#   ctx["emit_progress"](pct: float, msg: str)             进度回吐 (给 UI SSE 流转发)
#   ctx["config"]        插件本地配置 dict
#
# run() 返回体约定:
#   {"ok": True, "result": "...", "artifacts": [artifact_id, ...]}  正常
#   {"ok": False, "error": "..."}                                    失败
#
# ─────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional


# 必填字段 (单-plugin 模块)
REQUIRED_FIELDS = ("NAME", "DESCRIPTION", "SCHEMA", "run")
# 多-tool 模块可以改为暴露: TOOLS = [{name, description, schema, run, capabilities?}, ...]
MULTI_TOOL_FIELD = "TOOLS"

# 可选 capability 键
CAPABILITY_KEYS = {
    "produces_artifact": bool,   # 是否会 emit_artifact
    "long_running":      bool,   # 是否需要 BG 管理页
    "streaming":         bool,   # run 会不会分段回吐
    "cost":              str,    # "cheap" | "medium" | "heavy" — 给调度用
    "auth_required":     bool,   # 是否需要用户级鉴权
    "safe":              bool,   # False = 破坏性 (rm, kill) — 前端二次确认
}


@dataclass
class PluginSpec:
    """已加载 plugin 的元信息 (供 registry 存表)."""
    name: str
    description: str
    schema: dict
    capabilities: dict
    run: Callable[[dict, dict], Awaitable[dict]]
    source_path: str                     # 磁盘路径, 便于诊断
    origin: str                          # "internal" | "external"
    version: str = "0.0.1"
    author: str = ""
    health_check: Optional[Callable[[], Awaitable[bool]]] = None
    load_error: Optional[str] = None     # 加载失败时的错误信息 (依然入表, 便于管理页显示)

    def to_openai_tool(self) -> dict:
        """转成 OpenAI function-calling 格式, 让 LLM 直接看到."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema or {"type": "object", "properties": {}},
            },
        }


class PluginContractError(Exception):
    """Plugin 未满足契约 (缺字段 / 类型错) 时抛出."""


def validate_module(mod) -> tuple[bool, Optional[str]]:
    """返回 (ok, err_msg). 只做静态检查, 不 import 时执行. 支持单 & 多两种模块.

    单-plugin:  NAME/DESCRIPTION/SCHEMA/run
    多-tool:    TOOLS = [{name, description, schema, run}, ...]
    """
    if hasattr(mod, MULTI_TOOL_FIELD):
        tools = getattr(mod, MULTI_TOOL_FIELD)
        if not isinstance(tools, (list, tuple)) or not tools:
            return False, "TOOLS must be non-empty list"
        for i, t in enumerate(tools):
            if not isinstance(t, dict):
                return False, f"TOOLS[{i}] must be dict"
            for k in ("name", "description", "schema", "run"):
                if k not in t:
                    return False, f"TOOLS[{i}] missing key: {k}"
            if not callable(t["run"]):
                return False, f"TOOLS[{i}].run must be callable"
        return True, None

    for f in REQUIRED_FIELDS:
        if not hasattr(mod, f):
            return False, f"missing required attribute: {f}"
    if not isinstance(getattr(mod, "NAME"), str) or not mod.NAME:
        return False, "NAME must be non-empty str"
    if not callable(getattr(mod, "run")):
        return False, "run must be callable"
    schema = getattr(mod, "SCHEMA")
    if not isinstance(schema, dict):
        return False, "SCHEMA must be dict"
    return True, None


def specs_from_module(mod, source_path: str, origin: str) -> list["PluginSpec"]:
    """从合法模块产出 1 或 N 个 PluginSpec. 调用方负责去重."""
    out: list[PluginSpec] = []
    if hasattr(mod, MULTI_TOOL_FIELD):
        for t in getattr(mod, MULTI_TOOL_FIELD):
            out.append(PluginSpec(
                name=t["name"],
                description=t["description"],
                schema=t["schema"],
                capabilities=dict(t.get("capabilities") or {}),
                run=t["run"],
                source_path=source_path,
                origin=origin,
                version=str(t.get("version", getattr(mod, "VERSION", "0.0.1"))),
                author=str(t.get("author", getattr(mod, "AUTHOR", ""))),
                health_check=t.get("health_check"),
            ))
        return out

    out.append(PluginSpec(
        name=mod.NAME,
        description=mod.DESCRIPTION,
        schema=mod.SCHEMA,
        capabilities=dict(getattr(mod, "CAPABILITIES", {}) or {}),
        run=mod.run,
        source_path=source_path,
        origin=origin,
        version=str(getattr(mod, "VERSION", "0.0.1")),
        author=str(getattr(mod, "AUTHOR", "")),
        health_check=getattr(mod, "HEALTH_CHECK", None),
    ))
    return out
