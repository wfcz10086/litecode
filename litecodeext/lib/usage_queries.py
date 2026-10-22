"""usage_queries.py — 用量看板的聚合查询 (读 lib/usage_ledger.py 的账本)。

范围 range: today | 7d | 30d | all (按本机时区的自然日)。全部只读, 由 routers/usage_router.py 暴露。
"""
from __future__ import annotations

import csv
import io
import time
from datetime import date, datetime, timedelta
from typing import Optional

from .usage_ledger import STORE, source_of

RANGES = ("today", "7d", "30d", "all")
_MAX_BUCKETS = 400


def range_start(rng: str, now: Optional[float] = None) -> float:
    """范围起点 (本地时区零点); all → 0。未知值按 7d。"""
    now = now or time.time()
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if rng == "all":
        return 0.0
    days = {"today": 0, "30d": 29}.get(rng, 6)
    return (midnight - timedelta(days=days)).timestamp()


def _where(rng: str, user_id: Optional[str], col: str = "ts", window: Optional[tuple] = None) -> tuple[str, list]:
    if window:      # (起, 止) 显式时间窗, 环比用
        sql, args = f"{col} >= ? AND {col} < ?", [window[0], window[1]]
    else:
        sql, args = f"{col} >= ?", [range_start(rng)]
    if user_id:
        sql += " AND user_id = ?"
        args.append(user_id)
    return sql, args


def _q(sql: str, args) -> list:
    try:
        return STORE.query(sql, args)
    except Exception:
        return []


def _buckets(rng: str, rows: list) -> list:
    """补齐空桶: today 按小时 (0-23), 其余按天。"""
    have = {r["k"]: r for r in rows}
    zero = {"uncached": 0, "cached": 0, "completion": 0, "reasoning": 0, "calls": 0, "turns": 0}
    if rng == "today":
        keys = [f"{h:02d}" for h in range(24)]
    else:
        end = date.today()
        if rng == "all":
            first = min((r["k"] for r in rows), default=end.isoformat())
            start = date.fromisoformat(first)
        else:
            start = end - timedelta(days=29 if rng == "30d" else 6)
        n = min((end - start).days + 1, _MAX_BUCKETS)
        start = end - timedelta(days=n - 1)
        keys = [(start + timedelta(days=i)).isoformat() for i in range(n)]
    out = []
    for k in keys:
        r = have.get(k) or {}
        out.append({"key": k, **{f: int(r.get(f) or 0) for f in zero}})
    return out


_TOTALS_SQL = """SELECT COALESCE(SUM(prompt_tokens),0) prompt, COALESCE(SUM(completion_tokens),0) completion,
                  COALESCE(SUM(cached_tokens),0) cached, COALESCE(SUM(reasoning_tokens),0) reasoning,
                  COALESCE(SUM(total),0) total, COUNT(*) calls,
                  COUNT(DISTINCT CASE WHEN turn_id != '' THEN turn_id END) turns,
                  COALESCE(SUM(estimated),0) estimated_calls, COALESCE(SUM(1-ok),0) failed_calls,
                  COALESCE(AVG(duration_ms),0) avg_ms FROM calls WHERE {w}"""


def _totals(w: str, a: list) -> dict:
    tot = (_q(_TOTALS_SQL.format(w=w), a) or [{}])[0]
    tot = {k: int(v or 0) for k, v in tot.items()}
    tot["avg_per_turn"] = round(tot.get("total", 0) / tot["turns"]) if tot.get("turns") else 0
    tot["cache_ratio"] = round(tot["cached"] / tot["prompt"], 4) if tot.get("prompt") else 0
    return tot


def summary(rng: str = "7d", user_id: Optional[str] = None, now: Optional[float] = None) -> dict:
    """总量 + 上一个等长时间窗 (环比) + 时间序列 + 按模型/来源 + 星期×小时热力。"""
    w, a = _where(rng, user_id)
    tot = _totals(w, a)
    prev = None
    if rng != "all":
        now = now or time.time()
        start = range_start(rng, now)
        pw, pa = _where(rng, user_id, window=(start - (now - start), start))
        prev = _totals(pw, pa)
    key = ("strftime('%H', ts, 'unixepoch', 'localtime')" if rng == "today"
           else "date(ts, 'unixepoch', 'localtime')")
    series = _q(f"""SELECT {key} k, SUM(prompt_tokens - cached_tokens) uncached, SUM(cached_tokens) cached,
                    SUM(completion_tokens) completion, SUM(reasoning_tokens) reasoning, COUNT(*) calls,
                    COUNT(DISTINCT CASE WHEN turn_id != '' THEN turn_id END) turns
                    FROM calls WHERE {w} GROUP BY k ORDER BY k""", a)
    heat = _q(f"""SELECT CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER) dow,
                  CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) hour,
                  COUNT(*) calls, SUM(total) total FROM calls WHERE {w} GROUP BY dow, hour""", a)
    by = {}
    for col in ("model", "source"):
        by[col] = _q(f"""SELECT {col} name, SUM(total) total, SUM(prompt_tokens) prompt,
                         SUM(completion_tokens) completion, SUM(cached_tokens) cached, COUNT(*) calls,
                         SUM(estimated) estimated_calls
                         FROM calls WHERE {w} GROUP BY {col} ORDER BY total DESC""", a)
    return {"range": rng, "bucket": "hour" if rng == "today" else "day", "totals": tot, "prev_totals": prev,
            "series": _buckets(rng, series), "by_model": by["model"], "by_source": by["source"],
            "heatmap": heat}


def skills(rng: str = "7d", user_id: Optional[str] = None, per_skill_sessions: int = 10) -> list:
    """每个技能: 用过的轮数、会话数、这些轮的 token 合计、成功率、最近使用; 附用过它的会话 (按 token 排)。"""
    w, a = _where(rng, user_id, "t.ts")
    tok = "(SELECT turn_id, SUM(total) tok FROM calls GROUP BY turn_id)"
    rows = _q(f"""SELECT j.value skill, COUNT(*) turns, COUNT(DISTINCT t.session_id) sessions,
                  COALESCE(SUM(ct.tok),0) tokens, AVG(t.ok) success, MAX(t.ts) last_ts
                  FROM turns t JOIN json_each(t.skills) j LEFT JOIN {tok} ct ON ct.turn_id = t.turn_id
                  WHERE {w} GROUP BY j.value ORDER BY tokens DESC, turns DESC""", a)
    for r in rows:
        r["success"] = round(float(r["success"] or 0), 4)
        r["sessions_list"] = _q(f"""SELECT t.session_id, COUNT(*) turns, COALESCE(SUM(ct.tok),0) tokens,
                                    MAX(t.ts) last_ts
                                    FROM turns t JOIN json_each(t.skills) j LEFT JOIN {tok} ct
                                         ON ct.turn_id = t.turn_id
                                    WHERE {w} AND j.value = ? GROUP BY t.session_id
                                    ORDER BY tokens DESC LIMIT ?""", a + [r["skill"], per_skill_sessions])
    return rows


def sessions(rng: str = "7d", user_id: Optional[str] = None, limit: int = 20) -> list:
    """token 最多的会话; source 按会话 id 前缀算 (同一会话里还有识图/压缩等旁路调用, 不按调用来源)。"""
    w, a = _where(rng, user_id)
    rows = _q(f"""SELECT session_id, SUM(total) total, SUM(prompt_tokens) prompt, SUM(completion_tokens) completion,
                  SUM(cached_tokens) cached, COUNT(*) calls,
                  COUNT(DISTINCT CASE WHEN turn_id != '' THEN turn_id END) turns,
                  MAX(ts) last_ts, MAX(source) source
                  FROM calls WHERE {w} AND session_id != '' GROUP BY session_id
                  ORDER BY total DESC LIMIT ?""", a + [max(1, min(int(limit), 200))])
    for r in rows:
        r["source"] = source_of(r["session_id"])
    return rows


def tools(rng: str = "7d", user_id: Optional[str] = None, limit: int = 50) -> list:
    """模型发起的工具调用次数; tokens = 发起这些调用的那几次模型调用的 token (同一次调多个工具会各算一遍)。"""
    w, a = _where(rng, user_id, "c.ts")
    return _q(f"""SELECT j.value tool, COUNT(*) uses, COUNT(DISTINCT c.turn_id) turns,
                  COUNT(DISTINCT c.session_id) sessions, SUM(c.total) tokens, MAX(c.ts) last_ts
                  FROM calls c JOIN json_each(c.tools) j WHERE {w}
                  GROUP BY j.value ORDER BY uses DESC LIMIT ?""", a + [max(1, min(int(limit), 500))])


CSV_COLS = ("ts", "time", "user_id", "session_id", "turn_id", "source", "model", "prompt_tokens",
            "completion_tokens", "cached_tokens", "reasoning_tokens", "total", "duration_ms", "ok",
            "estimated", "backfill", "skills", "tools")


def export_csv(rng: str = "all", user_id: Optional[str] = None) -> str:
    w, a = _where(rng, user_id)
    rows = _q(f"SELECT * FROM calls WHERE {w} ORDER BY ts", a)
    buf = io.StringIO()
    buf.write("﻿")   # Excel 打开中文不乱码
    wr = csv.writer(buf)
    wr.writerow(CSV_COLS)
    for r in rows:
        r["time"] = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        wr.writerow([r.get(c, "") for c in CSV_COLS])
    return buf.getvalue()
