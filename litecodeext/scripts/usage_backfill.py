#!/usr/bin/env python3
"""usage_backfill.py — 把会话 json 里已有的每轮 usage 回填进 token 账本 (lib/usage_ledger.py)。

    python3 scripts/usage_backfill.py [--dry-run] [--dir DIR ...] [--all]

- 扫 ~/.litecode/web_sessions/*.json (可多次 --dir), 助手消息的 usage (或 meta.usage) 记一行:
  source=web, backfill=1, turn_id = bf-<会话>-<消息 id 或序号>; 技能取 meta.skills / skills。
- 幂等: 同一 turn_id 的回填行只会有一条 (唯一索引 + INSERT OR IGNORE), 反复跑不重复。
- 防重复计数: 账本里已有实时记账时, 默认只回填比最早一条实时记录更早的消息 (--all 关掉这个限制)。
- 账本路径同服务: env LITECODE_USAGE_DB > WORKSPACE/usage.db。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import usage_ledger as UL  # noqa: E402

DEFAULT_DIRS = [Path.home() / ".litecode" / "web_sessions"]


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _usage_of(m: dict):
    u = m.get("usage") or (m.get("meta") or {}).get("usage")
    if isinstance(u, str):          # 极老的会话把 dict 存成了字符串
        try:
            u = json.loads(u.replace("'", '"'))
        except Exception:
            return None
    return u if isinstance(u, dict) and UL.parse_usage(u) else None


def _skills_of(m: dict) -> list:
    sk = (m.get("meta") or {}).get("skills") or m.get("skills") or []
    return [str(s) for s in sk if s] if isinstance(sk, (list, tuple, set)) else []


def iter_turns(dirs: list[Path]):
    """产出 (session_id, turn_id, ts, model, usage, skills, ok)。"""
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                s = json.loads(p.read_text(errors="replace"))
            except Exception:
                continue
            sid = str(s.get("id") or p.stem)
            last_ts = _f(s.get("created"))
            for i, m in enumerate(s.get("messages") or []):
                if not isinstance(m, dict):
                    continue
                last_ts = _f(m.get("ts"), last_ts)
                if m.get("role") != "assistant":
                    continue
                u = _usage_of(m)
                if not u:
                    continue
                model = str(m.get("model") or (m.get("meta") or {}).get("model") or s.get("model") or "unknown")
                ok = not str(m.get("content") or "").lstrip().startswith(("[ERROR]", "⚠"))
                yield sid, f"bf-{sid}-{m.get('id') or i}", last_ts or _f(s.get("last_used")), model, u, _skills_of(m), ok


def first_live_ts() -> float:
    try:
        r = UL.STORE.query("SELECT MIN(ts) t FROM calls WHERE backfill = 0")
        return float(r[0]["t"]) if r and r[0]["t"] else 0.0
    except Exception:
        return 0.0


def run(dirs: list[Path], dry_run: bool = False, include_all: bool = False) -> dict:
    cutoff = 0.0 if include_all else first_live_ts()
    stat = {"seen": 0, "skipped_live": 0, "inserted": 0, "existing": 0, "tokens": 0}
    for sid, tid, ts, model, u, skills, ok in iter_turns(dirs):
        stat["seen"] += 1
        if cutoff and ts >= cutoff:
            stat["skipped_live"] += 1
            continue
        pu = UL.parse_usage(u) or {}
        if dry_run:
            stat["inserted"] += 1
            stat["tokens"] += pu.get("prompt", 0) + pu.get("completion", 0)
            continue
        dur = int(_f(u.get("elapsed_seconds")) * 1000)
        if UL.record(model, u, source="web", session_id=sid, turn_id=tid, ts=ts, duration_ms=dur, ok=ok,
                     backfill=True, skills=skills, user_id=UL.DEFAULT_USER, scope_snapshot={}):
            stat["inserted"] += 1
            stat["tokens"] += pu.get("prompt", 0) + pu.get("completion", 0)
            UL.record_turn(tid, session_id=sid, source="web", skills=skills, ok=ok, ts=ts, backfill=True)
        else:
            stat["existing"] += 1
    return stat


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把会话 json 里已有的 usage 回填进 token 账本 (幂等)")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument("--dir", action="append", type=Path, help="会话目录 (可多次), 默认 ~/.litecode/web_sessions")
    ap.add_argument("--all", action="store_true", help="不按最早实时记录截断 (可能与实时记账重复计数)")
    a = ap.parse_args(argv)
    stat = run(a.dir or DEFAULT_DIRS, dry_run=a.dry_run, include_all=a.all)
    mode = "试算 (未写库)" if a.dry_run else "已写入"
    print(f"账本: {UL.db_path()}")
    print(f"扫到带 usage 的助手消息 {stat['seen']} 条; {mode} {stat['inserted']} 条 / {stat['tokens']:,} token; "
          f"已存在跳过 {stat['existing']} 条; 晚于实时记账跳过 {stat['skipped_live']} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
