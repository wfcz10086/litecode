"""handlers/code_research.py — github_search / github_repo / package_search (方案与开源实现调研)."""
from __future__ import annotations

import asyncio
from typing import Any

try:
    import github_research as _gr
    import package_research as _pr
except ImportError:  # pragma: no cover
    _gr = _pr = None

from . import register

_TIMEOUT = 45   # 单次 HTTP 15s; 代码搜索最多串 3 个后端


async def _run(name: str, fn, **kw) -> tuple[str, Any]:
    if fn is None:
        return f"ERROR: {name} 模块不可用", None
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(loop.run_in_executor(None, lambda: fn(**kw)), timeout=_TIMEOUT)
        return r, None
    except asyncio.TimeoutError:
        return f"ERROR: {name} 超时 ({_TIMEOUT}s)", None
    except Exception as e:
        return f"ERROR: {name} 失败: {e}", None


@register("github_search")
async def h_github_search(sid: str, args: dict) -> tuple[str, Any]:
    return await _run("github_search", _gr and _gr.github_search,
                      kind=args.get("kind") or "repositories", query=args.get("query") or "",
                      language=args.get("language") or "", sort=args.get("sort") or "",
                      limit=args.get("limit") or 8, qualifiers=args.get("qualifiers"))


@register("github_repo")
async def h_github_repo(sid: str, args: dict) -> tuple[str, Any]:
    return await _run("github_repo", _gr and _gr.github_repo,
                      action=args.get("action") or "info", repo=args.get("repo") or "",
                      path=args.get("path") or "", ref=args.get("ref") or "",
                      limit=args.get("limit") or 0, query=args.get("query") or "")


@register("package_search")
async def h_package_search(sid: str, args: dict) -> tuple[str, Any]:
    return await _run("package_search", _pr and _pr.package_search,
                      query=args.get("query") or "", ecosystem=args.get("ecosystem") or "all",
                      limit=args.get("limit") or 5)
