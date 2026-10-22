"""usage_router.py — token 用量看板接口 (账本见 lib/usage_ledger.py, 聚合见 lib/usage_queries.py)。

GET /api/usage/summary?range=today|7d|30d|all   总量 / 输入 / 输出 / 缓存命中 / 思考 / 调用数 / 轮数 / 每轮平均 / 时间序列 / 按模型 / 按来源
GET /api/usage/skills?range=                     每技能: 轮数 / 会话数 / token / 成功率 / 最近使用 / 用过的会话
GET /api/usage/sessions?range=&limit=            token 最多的会话 (带标题)
GET /api/usage/tools?range=                      工具排行
GET /api/usage/export.csv?range=                 逐次调用明细 CSV
GET /api/usage/report.html?range=                离线 HTML 报表 (同一套图表 + 数据内嵌, 单文件, 断网可开)
个人版: 不按用户过滤; ?user= 预留给多用户。
"""
from __future__ import annotations

import asyncio
import html
import json
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from lib import usage_queries as Q
from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()
ORDER = 300


def _args(request: Request) -> tuple[str, str | None]:
    rng = request.query_params.get("range", "7d")
    return (rng if rng in Q.RANGES else "7d"), (request.query_params.get("user") or None)


def _title(sid: str, cache: dict) -> str:
    """会话标题: web 会话读 name; DAG 用流水线名; 其余 (微信 / CLI) 显示 id。"""
    if sid in cache:
        return cache[sid]
    t = ""
    if sid.startswith("dag:"):
        t = "DAG · " + sid[4:]
    else:
        try:
            p = Path(S.sessions_dir) / f"{sid}.json"
            if p.is_file() and p.stat().st_size < 64 * 1024 * 1024:
                t = str(json.loads(p.read_text(errors="replace")).get("name") or "")
        except Exception:
            t = ""
    cache[sid] = t or sid
    return cache[sid]


def _with_titles(rows: list, cache: dict) -> list:
    for r in rows:
        r["title"] = _title(r.get("session_id") or "", cache)
    return rows


@router.get("/api/usage/summary")
async def usage_summary(request: Request):
    require_auth(request)
    rng, user = _args(request)
    return await asyncio.to_thread(Q.summary, rng, user)


@router.get("/api/usage/skills")
async def usage_skills(request: Request):
    require_auth(request)
    rng, user = _args(request)

    def run():
        rows, cache = Q.skills(rng, user), {}
        for r in rows:
            _with_titles(r["sessions_list"], cache)
        return rows
    return {"range": rng, "skills": await asyncio.to_thread(run)}


@router.get("/api/usage/sessions")
async def usage_sessions(request: Request):
    require_auth(request)
    rng, user = _args(request)
    try:
        limit = int(request.query_params.get("limit", "20"))
    except ValueError:
        limit = 20
    rows = await asyncio.to_thread(lambda: _with_titles(Q.sessions(rng, user, limit), {}))
    return {"range": rng, "sessions": rows}


@router.get("/api/usage/tools")
async def usage_tools(request: Request):
    require_auth(request)
    rng, user = _args(request)
    return {"range": rng, "tools": await asyncio.to_thread(Q.tools, rng, user)}


@router.get("/api/usage/export.csv")
async def usage_export(request: Request):
    require_auth(request)
    rng, user = _args(request)
    if "range" not in request.query_params:
        rng = "all"
    body = await asyncio.to_thread(Q.export_csv, rng, user)
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="litecode_usage_{rng}.csv"'})


_ASSETS = Path(__file__).resolve().parent.parent / "web_assets"
_REPORT_FILES = ("usage_dashboard.css", "usage_charts.js", "usage_dashboard.js")
_RANGE_NAMES = {"today": "今天", "7d": "近 7 天", "30d": "近 30 天", "all": "全部"}
# 报表自带主题 token (同 web_assets 的暖白 / 经典暗黑), 跟随系统深浅色
_REPORT_TOKENS = """:root{--bg:#FBFBF9;--sf:#FFFFFF;--sf2:#F5F4F0;--bd:#E8E4DC;--bd2:#DDD8CE;--tx:#1A1A1A;--tx2:#5C5C5C;
--tx3:#8A8A8A;--sans:"Inter","Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0c0f14;--sf:#13181f;--sf2:#1a2030;--bd:#252d3a;--bd2:#2e3a4e;--tx:#e2e8f4;
--tx2:#8fa3bc;--tx3:#6b7f99;color-scheme:dark}}
html,body{margin:0;background:var(--bg);color:var(--tx);font-family:var(--sans)}"""


def report_html(rng: str, user: str | None) -> str:
    """离线报表: 把看板的 css/js 与当前范围的数据一起内联成单个 HTML。"""
    cache: dict = {}
    skills = Q.skills(rng, user)
    for r in skills:
        _with_titles(r["sessions_list"], cache)
    data = {"range": rng, "generated": time.time(), "summary": Q.summary(rng, user), "skills": skills,
            "sessions": _with_titles(Q.sessions(rng, user, 10), cache), "tools": Q.tools(rng, user)}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    css = (_ASSETS / _REPORT_FILES[0]).read_text(encoding="utf-8")
    js = "\n".join((_ASSETS / f).read_text(encoding="utf-8") for f in _REPORT_FILES[1:]).replace("</script", "<\\/script")
    title = html.escape(f"LiteCode 用量报表 · {_RANGE_NAMES.get(rng, rng)}")
    return (f"<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title>"
            f"<style>{_REPORT_TOKENS}\n{css}</style></head><body class=\"ud-report\">"
            f"<script>window.__UD_REPORT__ = {blob};</script><script>{js}</script></body></html>")


@router.get("/api/usage/report.html")
async def usage_report(request: Request):
    require_auth(request)
    rng, user = _args(request)
    body = await asyncio.to_thread(report_html, rng, user)
    return Response(body, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="litecode_usage_report_{rng}.html"'})
