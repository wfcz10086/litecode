"""core/doubao_search.py — 火山引擎「豆包搜索 Custom 版」联网搜索 (付费 API, 每月有免费额度)。

web_search 的第一优先级: 配了 key 才启用, 失败/空结果交回 SearXNG → 浏览器链路。
key: config.json search.doubao_api_key, 或环境变量 DOUBAO_SEARCH_API_KEY。
接口: POST https://open.feedcoopapi.com/search_api/web_search, Authorization: Bearer <key>
"""
from __future__ import annotations

import os
import time

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

DOUBAO_SEARCH_URL = "https://open.feedcoopapi.com/search_api/web_search"
_TIMEOUT = float(os.environ.get("DOUBAO_SEARCH_TIMEOUT", "15"))


def api_key(search_cfg: dict | None = None) -> str:
    return (os.environ.get("DOUBAO_SEARCH_API_KEY")
            or str((search_cfg or {}).get("doubao_api_key") or "")).strip()


def doubao_search(query: str, key: str, top_k: int = 8, time_range: str = "") -> dict:
    """返回 {ok, query, results: [{title,url,snippet,site,published}], message, source}。
    不取全文 (NeedContent=false): 全文 markdown 很大, 需要细看时模型再 web_fetch。"""
    if httpx is None:
        return {"ok": False, "results": [], "message": "ERROR: httpx 未安装"}
    body = {
        "Query": query[:100],
        "SearchType": "web",
        "Count": max(1, min(int(top_k or 8), 20)),
        "Filter": {"NeedContent": False, "NeedUrl": True},
        "QueryControl": {"QueryRewrite": False},
    }
    if time_range:
        body["TimeRange"] = time_range
    t0 = time.time()
    try:
        r = httpx.post(DOUBAO_SEARCH_URL, json=body, timeout=_TIMEOUT,
                       headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        data = r.json() if r.content else {}
    except Exception as e:
        return {"ok": False, "query": query, "results": [], "message": f"ERROR: 豆包搜索调用失败: {e}"}
    if r.status_code != 200:
        meta = (data or {}).get("ResponseMetadata") or {}
        err = (meta.get("Error") or {}).get("Message") or data.get("message") or data.get("msg") or r.text[:300]
        return {"ok": False, "query": query, "results": [],
                "message": f"ERROR: 豆包搜索 HTTP {r.status_code}: {err}"}
    raw = ((data or {}).get("Result") or {}).get("WebResults") or []
    results = []
    for it in raw:
        title, url = (it.get("Title") or "").strip(), (it.get("Url") or "").strip()
        if not (title or url):
            continue
        results.append({
            "title": title[:200],
            "url": url,
            "snippet": ((it.get("Summary") or "").strip() or (it.get("Snippet") or "").strip())[:500],
            "site": (it.get("SiteName") or "").strip(),
            "published": (it.get("PublishTime") or "").strip()[:16].replace("T", " "),
        })
    if not results:
        return {"ok": False, "query": query, "results": [], "message": "豆包搜索: 0 条结果"}
    lines = [f"## 豆包搜索结果 ({len(results)} 条, {time.time() - t0:.1f}s)"]
    for i, x in enumerate(results, 1):
        tail = " · ".join(v for v in (x["site"], x["published"]) if v)
        lines.append(f"[{i}] **{x['title']}**" + (f"  ({tail})" if tail else "")
                     + f"\n    URL: {x['url']}" + (f"\n    {x['snippet']}" if x["snippet"] else ""))
    return {"ok": True, "query": query, "results": results,
            "message": "\n\n".join(lines), "source": "doubao"}
