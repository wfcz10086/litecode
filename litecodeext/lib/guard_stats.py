"""守卫注入统计 — 让"某条守卫在空转"变成可见的数字。

起因 (2026-08-31 实测): `[SYSTEM-TEST]` 守卫用 Python/JS 的测试命名约定去检测
Go 项目, 从 iter 3 一路误报到 iter 79 —— 每轮往 agent 上下文注入一条内容错误的
警告, 而 agent 写的 40 个测试函数它一个都认不出。

**误报了 79 轮无人知晓, 因为没有任何地方统计守卫注入了多少次。**

这个模块只做一件小事: 记录每条守卫注入了几次, 回合收尾打一行, 并累计落盘。
判断"守卫有没有用"需要人看数据 —— 但至少数据得存在。

设计取舍:
- 只统计**注入次数**, 不猜"agent 有没有采纳"。采纳与否难以机械判定,
  强行推断只会造出第二个不可信的启发式 (正是本模块要防的东西)。
- 落盘失败不抛 —— 埋点绝不能影响主流程。但失败会 log.warning, 不静默。
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Optional

log = logging.getLogger("openclaw")

# 累计文件放工作区, 与 token_stats.json 同级
_STATS_NAME = "guard_stats.json"


class GuardStats:
    """一个回合一份。上层在回合开始时新建, 收尾时 flush()。"""

    def __init__(self, workspace: Optional[Path] = None, session_id: str = ""):
        self.counts: Counter[str] = Counter()
        self.workspace = workspace
        self.session_id = session_id or "unknown"

    def hit(self, guard: str, n: int = 1) -> None:
        """某条守卫注入了一次。guard 用稳定短名, 如 'SYSTEM-TEST'。"""
        self.counts[guard] += n

    def summary(self) -> str:
        """回合收尾的一行摘要; 无注入时返回空串 (不刷屏)。"""
        if not self.counts:
            return ""
        return " ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))

    def flush(self) -> None:
        """把本回合计数累加进工作区的 guard_stats.json。"""
        if not self.counts or self.workspace is None:
            return
        path = Path(self.workspace) / _STATS_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception as exc:
            log.warning(f"[guard-stats] 读取 {path} 失败, 本回合计数从零起算: {exc}")
            data = {}
        totals = data.setdefault("totals", {})
        for k, v in self.counts.items():
            entry = totals.setdefault(k, {"injections": 0, "turns": 0, "last_seen": ""})
            entry["injections"] += v
            entry["turns"] += 1
            entry["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning(f"[guard-stats] 写入 {path} 失败: {exc}")


def load_totals(workspace: Path) -> dict:
    """读累计统计, 供人工排查 / 后续做报表。读不到返回空 dict。"""
    path = Path(workspace) / _STATS_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("totals", {})
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log.warning(f"[guard-stats] 读取累计统计失败: {exc}")
        return {}
