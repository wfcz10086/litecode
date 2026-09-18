"""handlers/error_search.py — github/stackoverflow/error_text 语义搜索."""
from __future__ import annotations

import asyncio
from typing import Any

try:
    import error_search as _es
    HAS_ERROR_SEARCH = True
except ImportError:
    _es = None
    HAS_ERROR_SEARCH = False

from . import register


@register("github_search_issues")
async def h_github_search_issues(sid: str, args: dict) -> tuple[str, Any]:
    if not HAS_ERROR_SEARCH or not _es:
        return "ERROR: error_search module 不可用", None
    q = args.get("query", "")
    if not q:
        return "ERROR: github_search_issues 需要 query", None
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _es.github_search_issues, q,
                args.get("repo"), args.get("state", "all"),
                min(int(args.get("top_k", 5)), 10),
            ),
            timeout=15,
        )
        return r, None
    except asyncio.TimeoutError:
        return "ERROR: github_search_issues 超时 (15s)", None
    except Exception as e:
        return f"ERROR: {e}", None


@register("stackoverflow_search")
async def h_stackoverflow_search(sid: str, args: dict) -> tuple[str, Any]:
    if not HAS_ERROR_SEARCH or not _es:
        return "ERROR: error_search module 不可用", None
    q = args.get("query", "")
    if not q:
        return "ERROR: stackoverflow_search 需要 query", None
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _es.stackoverflow_search, q,
                args.get("tag"), int(args.get("top_k", 5)),
            ),
            timeout=15,
        )
        return r, None
    except asyncio.TimeoutError:
        return "ERROR: stackoverflow_search 超时 (15s)", None
    except Exception as e:
        return f"ERROR: {e}", None


@register("search_code_error")
async def h_search_code_error(sid: str, args: dict) -> tuple[str, Any]:
    if not HAS_ERROR_SEARCH or not _es:
        return "ERROR: error_search module 不可用", None
    err = args.get("error_text", "")
    if not err:
        return "ERROR: search_code_error 需要 error_text", None
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _es.search_code_error, err,
                args.get("repo"), int(args.get("top_k", 3)),
            ),
            timeout=20,
        )
        return r, None
    except asyncio.TimeoutError:
        return "ERROR: search_code_error 超时 (20s)", None
    except Exception as e:
        return f"ERROR: {e}", None
