"""tool.web_search — thin wrapper over legacy search config from config.json.

V0: returns the built URL(s) only; the real fetch happens in the legacy tool
layer or in a follow-up 'tool.http_fetch' node (out of V4 scope).
"""
from __future__ import annotations

from ...config_bridge import load_raw_config
from ..registry import node_registry
from ..spec import NodeContext


@node_registry.register(
    "tool.web_search",
    inputs={"query": "str"},
    outputs={"urls": "list", "engines": "list"},
    description="Build search URLs for the given query using engines from config.json search.*.",
)
async def tool_web_search(ctx: NodeContext) -> dict:
    query = ctx.inputs.get("query") or ""
    if not query:
        raise ValueError("tool.web_search requires input 'query'")
    cfg = load_raw_config()
    mode = ctx.config.get("mode") or cfg.get("search", {}).get("default_mode", "international")
    engines = cfg.get("search", {}).get(mode, [])
    urls: list[str] = []
    names: list[str] = []
    from urllib.parse import quote_plus
    for e in engines:
        tmpl = e.get("url_template", "")
        if tmpl:
            urls.append(tmpl.format(q=quote_plus(str(query))))
            names.append(e.get("name", "?"))
    return {"urls": urls, "engines": names, "out": urls}
