"""plugins/kb/tool.py — kb_search 工具 (主 agent handler: core/tools/handlers/kb.py)。

工具跑在 litecode_server 进程, 向量模型只在 web_ui 进程加载一份: 这里优先走本机 web_ui 的
/api/kb/search (用 config server.token 做内部鉴权), web_ui 不可达时才在本进程直接检索。
输出把每条命中编号 [n], 要求模型回答时按编号标注出处; 前端按同样的编号做悬停引用 (kb_panel.js)。
"""
from __future__ import annotations

import asyncio
import json
import os

from . import cfg, config_path, enabled

NAME = "kb_search"


def _internal() -> tuple[str, str]:
    try:
        c = json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        c = {}
    port = os.environ.get("WEB_PORT") or (c.get("web_ui") or {}).get("port") or 18790
    return f"http://127.0.0.1:{port}/api/kb/search", str((c.get("server") or {}).get("token") or "")


def do_search(query: str, kb=None, top_k: int | None = None) -> dict:
    """同步检索 (本进程)。→ {bases:[...], results:[...]} 或 {error}。"""
    from . import store
    try:
        bases = store.resolve_bases(kb)
        if not bases:
            names = [b["name"] for b in store.resolve_bases(None)]
            return {"error": f"没有找到知识库「{kb}」" + (f"; 现有: {', '.join(names)}" if names else "; 还没有建任何知识库")}
        res = store.search(query, [b["id"] for b in bases], top_k=top_k)
        return {"bases": [b["name"] for b in bases], "results": res}
    except Exception as e:  # noqa: BLE001
        return {"error": f"知识库检索失败: {e}"}


async def _search(query: str, kb, top_k) -> dict:
    url, token = _internal()
    if token:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60) as cli:
                r = await cli.post(url, json={"query": query, "kb": kb, "top_k": top_k},
                                   headers={"X-LiteCode-Token": token})
            if r.status_code == 200:
                return r.json()
        except Exception:  # noqa: BLE001  web_ui 不在 (CLI 等): 本进程检索
            pass
    return await asyncio.to_thread(do_search, query, kb, top_k)


def format_results(query: str, d: dict) -> str:
    if d.get("error"):
        return f"ERROR: {d['error']}"
    scope = "、".join(d.get("bases") or []) or "全部"
    res = d.get("results") or []
    if not res:
        return (f"知识库「{scope}」里没有找到与「{query}」相关的内容 (相关度阈值 {cfg()['min_score']})。"
                "可以换个说法或关键词再搜一次; 仍没有就如实告诉用户资料里没有, 不要编造。")
    lines = [f"知识库「{scope}」检索「{query}」命中 {len(res)} 处资料。回答必须基于这些资料, "
             "在用到的句子后用 [编号] 标注出处 (如 [1]、[2]); 资料里没有的内容就说没找到, 不要编造。", ""]
    for i, r in enumerate(res, 1):
        where = (f" · {r['loc']}" if r.get("loc") else "") + (f" (同文另见 {r['also']})" if r.get("also") else "")
        lines.append(f"[{i}] {r['filename']}{where} · 相关度 {r['score']:.2f}")
        body = (r.get("context") or r.get("snippet") or "").strip()
        lines.append(("…" if r.get("head") else "") + body + ("…" if r.get("tail") else ""))
        lines.append("")
    lines.append("出处: " + "; ".join(f"[{i}] {r['filename']}{(' ' + r['loc']) if r.get('loc') else ''}"
                                     for i, r in enumerate(res, 1)))
    return "\n".join(lines)


async def run(args: dict) -> str:
    if not enabled():
        return ("ERROR: 知识库未启用 (config.json 没有 kb.dsn 或 kb.enabled=false)。"
                "部署见 docs/KNOWLEDGE_BASE.md; 暂时只能根据已有知识回答并告知用户。")
    query = str(args.get("query") or "").strip()
    if not query:
        return "ERROR: kb_search 需要 query (要查的问题或关键词)"
    try:
        top_k = max(1, min(int(args.get("top_k") or cfg()["top_k"]), 10))
    except (TypeError, ValueError):
        top_k = cfg()["top_k"]
    return format_results(query, await _search(query, args.get("kb"), top_k))
