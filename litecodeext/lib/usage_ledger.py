"""usage_ledger.py — token 用量账本 (SQLite, 个人版; user_id 预留, 默认 "admin")。

每次调大模型记一行 calls; 每轮对话结束记一行 turns (技能 / 成败)。看板查询见 lib/usage_queries.py。

- 路径: env LITECODE_USAGE_DB > WORKSPACE/usage.db
- 保留期: env LITECODE_USAGE_RETENTION_DAYS > config usage.retention_days > 365 天
- WAL + busy_timeout, 进程内一把锁; gateway / web_ui 两个进程同写一个库也没事
- **写失败只打日志, 绝不影响对话**

谁在调模型、属于哪个会话/哪一轮, 走 ContextVar 作用域 (begin_turn / scope):
gateway 每轮 begin_turn; DAG / 旁路调用用 scope 或 record(source=...) 覆盖来源。
lib/transport.py 的 vllm_stream / vllm_call 统一记账 (Meter); 直接 httpx 调模型的地方用 record_http。
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger("openclaw")

DEFAULT_USER = "admin"
DEFAULT_RETENTION_DAYS = 365
_PRUNE_EVERY = 500          # 每写这么多行顺手清一次过期数据

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user_id TEXT NOT NULL DEFAULT 'admin',
    session_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1,
    estimated INTEGER NOT NULL DEFAULT 0,
    skills TEXT NOT NULL DEFAULT '[]',
    tools TEXT NOT NULL DEFAULT '[]',
    backfill INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS idx_calls_session ON calls(session_id);
CREATE INDEX IF NOT EXISTS idx_calls_model ON calls(model);
CREATE INDEX IF NOT EXISTS idx_calls_turn ON calls(turn_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_calls_backfill ON calls(turn_id) WHERE backfill = 1;
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    user_id TEXT NOT NULL DEFAULT 'admin',
    session_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '[]',
    ok INTEGER NOT NULL DEFAULT 1,
    backfill INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
"""

_CALL_COLS = ("ts", "user_id", "session_id", "turn_id", "source", "model", "prompt_tokens",
              "completion_tokens", "cached_tokens", "reasoning_tokens", "total", "duration_ms",
              "ok", "estimated", "skills", "tools", "backfill")


# ── 存储 ──────────────────────────────────────────────────────
def db_path() -> Path:
    env = os.environ.get("LITECODE_USAGE_DB", "").strip()
    if env:
        return Path(env)
    try:
        from lib.config import WORKSPACE
        return Path(WORKSPACE) / "usage.db"
    except Exception:
        return Path("/tmp/litecode_workspace/usage.db")


def retention_days() -> int:
    env = os.environ.get("LITECODE_USAGE_RETENTION_DAYS", "").strip()
    if env.isdigit():
        return int(env)
    cfg_mod = sys.modules.get("lib.config")   # 服务进程里早已加载; 脚本里不为这个去加载整套配置
    try:
        return int((getattr(cfg_mod, "CFG", {}).get("usage") or {}).get("retention_days", DEFAULT_RETENTION_DAYS))
    except Exception:
        return DEFAULT_RETENTION_DAYS


class _Store:
    def __init__(self):
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._path: Optional[Path] = None
        self._writes = 0

    def conn(self) -> sqlite3.Connection:
        """按当前路径懒打开 (测试改 env 后自动换库)。调用方须持锁。"""
        p = db_path()
        if self._conn is not None and self._path == p:
            return self._conn
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
        p.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(p), timeout=3, check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=3000")
        c.executescript(_SCHEMA)
        self._conn, self._path = c, p
        self._prune(c)
        return c

    def _prune(self, c: sqlite3.Connection):
        days = retention_days()
        if days <= 0:
            return
        cut = time.time() - days * 86400
        c.execute("DELETE FROM calls WHERE ts < ?", (cut,))
        c.execute("DELETE FROM turns WHERE ts < ?", (cut,))

    def write(self, sql: str, args: Iterable = ()) -> int:
        """执行写语句; 失败返回 -1 (不抛)。"""
        try:
            with self._lock:
                c = self.conn()
                cur = c.execute(sql, tuple(args))
                self._writes += 1
                if self._writes % _PRUNE_EVERY == 0:
                    self._prune(c)
                return cur.rowcount
        except Exception as e:
            log.debug(f"[usage] 账本写入失败 (忽略): {e}")
            return -1

    def query(self, sql: str, args: Iterable = ()) -> list:
        with self._lock:
            return [dict(r) for r in self.conn().execute(sql, tuple(args)).fetchall()]

    def close(self):
        with self._lock:
            if self._conn is not None:
                with contextlib.suppress(Exception):
                    self._conn.close()
            self._conn, self._path = None, None


STORE = _Store()


# ── 来源 / 作用域 ─────────────────────────────────────────────
def source_of(session_id: Optional[str]) -> str:
    """按会话 id 前缀推来源 (各端建会话的约定见 web_ui / wechat / cli / dag_state)。"""
    s = session_id or ""
    for pre, src in (("web-", "web"), ("wx-", "wechat"), ("wc-", "wecom"), ("cli-", "cli"),
                     ("dag:", "dag"), ("subagent_", "subagent")):
        if s.startswith(pre):
            return src
    return "api" if not s else "web"


_SCOPE: contextvars.ContextVar = contextvars.ContextVar("litecode_usage_scope", default=None)


def current() -> dict:
    return _SCOPE.get() or {}


def begin_turn(session_id: Optional[str], turn_id: str, *, source: str = "",
               user_id: str = DEFAULT_USER, skills: Optional[set] = None):
    """一轮开始: 设作用域, 返回句柄给 end_turn。skills 传 ctx.turn_skills (同一个 set, 边跑边加)。"""
    sc = {"session_id": session_id or "", "turn_id": turn_id, "user_id": user_id or DEFAULT_USER,
          "source": source or source_of(session_id),
          "skills": skills if skills is not None else set(), "t0": time.time()}
    return (_SCOPE.set(sc), sc)


def end_turn(token=None, ok: bool = True, session_id: Optional[str] = None):
    """一轮结束: 记 turns 行 (技能 / 成败), 并把本轮技能补写到本轮各次调用上。
    session_id: 轮中会话被自动轮换 (auto-rotate) 时传新 id, 本轮的账一并改挂过去。
    token 是 begin_turn 的返回值 (生成器的 finally 可能跑在别的 context, 所以作用域随句柄带着)。"""
    token, sc = token if isinstance(token, tuple) else (token, current())
    if sc.get("turn_id") and session_id and session_id != sc.get("session_id"):
        sc["session_id"] = session_id
        STORE.write("UPDATE calls SET session_id=? WHERE turn_id=? AND backfill=0", (session_id, sc["turn_id"]))
    if sc.get("turn_id"):
        sk = json.dumps(sorted(sc.get("skills") or ()), ensure_ascii=False)
        STORE.write("INSERT OR REPLACE INTO turns(turn_id, ts, user_id, session_id, source, skills, ok) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (sc["turn_id"], sc.get("t0") or time.time(), sc.get("user_id") or DEFAULT_USER,
                     sc.get("session_id") or "", sc.get("source") or "", sk, 1 if ok else 0))
        if sk != "[]":
            STORE.write("UPDATE calls SET skills=? WHERE turn_id=? AND backfill=0", (sk, sc["turn_id"]))
    if token is not None:
        with contextlib.suppress(Exception):   # 生成器 finally 可能跑在别的 context, reset 会报错
            _SCOPE.reset(token)


@contextlib.contextmanager
def scope(**kw):
    """临时覆盖作用域字段 (source/session_id/turn_id/user_id), 未给的沿用外层。"""
    base = dict(current())
    base.update({k: v for k, v in kw.items() if v is not None})
    base.setdefault("skills", set())
    tok = _SCOPE.set(base)
    try:
        yield base
    finally:
        with contextlib.suppress(Exception):
            _SCOPE.reset(tok)


def set_scope(**kw):
    """scope() 的非 with 版本 (不便缩进的长函数用), 返回 token; 用 reset_scope 复位。"""
    base = dict(current())
    base.update({k: v for k, v in kw.items() if v is not None})
    base.setdefault("skills", set())
    return _SCOPE.set(base)


def reset_scope(token):
    with contextlib.suppress(Exception):
        _SCOPE.reset(token)


def note_skills(names: Iterable[str], ctx: Any = None):
    """记本轮用到的技能: 写进当前作用域 (账本用) 和 ctx.turn_skills (若给了 ctx)。"""
    names = [str(n) for n in (names or ()) if n]
    if not names:
        return
    sk = current().get("skills")
    if isinstance(sk, set):
        sk.update(names)
    if ctx is not None:
        ts = getattr(ctx, "turn_skills", None)
        if isinstance(ts, set):
            ts.update(names)


def _merge_source(override: str, scoped: str) -> str:
    if not override:
        return scoped or "api"
    if override == "subagent" and scoped == "dag":   # DAG 的步骤本质是子代理, 归 DAG
        return "dag"
    return override


# ── usage 解析 / 估算 ─────────────────────────────────────────
def _int(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def parse_usage(u: Optional[dict]) -> Optional[dict]:
    """OpenAI / vLLM / DeepSeek / Anthropic 风格 usage → 统一 5 项; 拿不到返回 None。"""
    if not isinstance(u, dict):
        return None
    p = _int(u.get("prompt_tokens") or u.get("input_tokens"))
    c = _int(u.get("completion_tokens") or u.get("output_tokens"))
    if not p and not c:
        return None
    pd = u.get("prompt_tokens_details") or u.get("input_tokens_details") or {}
    cd = u.get("completion_tokens_details") or u.get("output_tokens_details") or {}
    cached = _int((pd.get("cached_tokens") if isinstance(pd, dict) else 0)
                  or u.get("prompt_cache_hit_tokens") or u.get("cache_read_input_tokens")
                  or u.get("cached_tokens"))
    if u.get("cache_read_input_tokens") and not u.get("prompt_tokens"):
        p += cached      # Anthropic: input_tokens 不含缓存读取部分
    reasoning = _int((cd.get("reasoning_tokens") if isinstance(cd, dict) else 0)
                     or u.get("reasoning_tokens"))
    return {"prompt": p, "completion": c, "cached": min(cached, p),
            "reasoning": min(reasoning, c) if c else reasoning}


def est_text(text) -> int:
    """复用 lib.config.est_tokens (中文 1 字 1 token, 其余 3~4 字符 1 token); 未加载时用同口径简化版。"""
    cfg_mod = sys.modules.get("lib.config")
    if cfg_mod is not None and hasattr(cfg_mod, "est_tokens"):
        try:
            return int(cfg_mod.est_tokens(text))
        except Exception:
            pass
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(s) - cjk) // 3


def est_messages(messages: Optional[list], tools: Optional[list] = None) -> int:
    n = 0
    for m in messages or ():
        if not isinstance(m, dict):
            continue
        n += est_text(m.get("content") or "") + 4
        if m.get("tool_calls"):
            n += est_text(json.dumps(m["tool_calls"], ensure_ascii=False, default=str))
        if m.get("reasoning_content"):
            n += est_text(m["reasoning_content"])
    if tools:
        n += len(json.dumps(tools, ensure_ascii=False, default=str)) // 4
    return n


# ── 记账 ──────────────────────────────────────────────────────
def record(model: str, usage: Optional[dict] = None, *, source: str = "", messages: Optional[list] = None,
           tools_schema: Optional[list] = None, out_text: str = "", reasoning_text: str = "",
           duration_ms: int = 0, ok: bool = True, tools: Optional[Iterable[str]] = None,
           session_id: Optional[str] = None, turn_id: Optional[str] = None,
           user_id: Optional[str] = None, ts: Optional[float] = None, backfill: bool = False,
           skills: Optional[Iterable[str]] = None, scope_snapshot: Optional[dict] = None) -> bool:
    """记一次模型调用。usage 拿不到 → 按字符估算并标 estimated=1; 啥都没收到的失败调用记 0。
    scope_snapshot: 调用开始时的作用域 (流结束的 finally 可能跑在别的 context)。返回是否写入成功。永不抛异常。"""
    try:
        sc = scope_snapshot if scope_snapshot is not None else current()
        u = parse_usage(usage)
        estimated = 0
        if u is None:
            got_any = bool(out_text or reasoning_text or tools)
            if got_any or ok:
                p = est_messages(messages, tools_schema)
                r = est_text(reasoning_text) if reasoning_text else 0
                c = est_text(out_text) + r + sum(8 for _ in (tools or ()))
                u = {"prompt": p, "completion": c, "cached": 0, "reasoning": r}
                estimated = 1 if (p or c) else 0
            else:
                u = {"prompt": 0, "completion": 0, "cached": 0, "reasoning": 0}
        elif not u["reasoning"] and reasoning_text and u["completion"]:
            u["reasoning"] = min(est_text(reasoning_text), u["completion"])  # 上游没拆思考, 按字数分
        sk = skills if skills is not None else (sc.get("skills") or ())
        row = (ts or time.time(), user_id or sc.get("user_id") or DEFAULT_USER,
               session_id if session_id is not None else (sc.get("session_id") or ""),
               turn_id if turn_id is not None else (sc.get("turn_id") or ""),
               _merge_source(source, sc.get("source", "")), str(model or ""),
               u["prompt"], u["completion"], u["cached"], u["reasoning"], u["prompt"] + u["completion"],
               int(duration_ms or 0), 1 if ok else 0, estimated,
               json.dumps(sorted(set(sk)), ensure_ascii=False), json.dumps(list(tools or ()), ensure_ascii=False),
               1 if backfill else 0)
        verb = "INSERT OR IGNORE" if backfill else "INSERT"
        return STORE.write(f"{verb} INTO calls({','.join(_CALL_COLS)}) VALUES ({','.join('?' * len(_CALL_COLS))})",
                           row) > 0
    except Exception as e:
        log.debug(f"[usage] record 失败 (忽略): {e}")
        return False


def record_turn(turn_id: str, *, session_id: str, source: str, skills: Iterable[str] = (), ok: bool = True,
                ts: Optional[float] = None, user_id: str = DEFAULT_USER, backfill: bool = False) -> bool:
    verb = "INSERT OR IGNORE" if backfill else "INSERT OR REPLACE"
    return STORE.write(f"{verb} INTO turns(turn_id, ts, user_id, session_id, source, skills, ok, backfill) "
                       "VALUES (?,?,?,?,?,?,?,?)",
                       (turn_id, ts or time.time(), user_id, session_id or "", source,
                        json.dumps(sorted(set(skills or ())), ensure_ascii=False), 1 if ok else 0,
                        1 if backfill else 0)) > 0


def _resp_json(resp) -> Optional[dict]:
    if isinstance(resp, dict):
        return resp
    try:
        return resp.json()
    except Exception:
        return None


def record_http(source: str, model: str, payload: Optional[dict], resp, t0: float,
                session_id: Optional[str] = None) -> bool:
    """直接 httpx/aiohttp 调模型的地方用: resp 是 httpx.Response、已解析的 dict 或 None (请求失败)。"""
    try:
        status = getattr(resp, "status_code", 200 if isinstance(resp, dict) else 0)
        data = _resp_json(resp) if status == 200 else None
        msg = ((data or {}).get("choices") or [{}])[0].get("message") or {}
        return record(model, (data or {}).get("usage"), source=source,
                      messages=(payload or {}).get("messages"),
                      out_text=str(msg.get("content") or ""),
                      reasoning_text=str(msg.get("reasoning_content") or msg.get("reasoning") or ""),
                      duration_ms=int((time.time() - t0) * 1000), ok=data is not None,
                      session_id=session_id)
    except Exception as e:
        log.debug(f"[usage] record_http 失败 (忽略): {e}")
        return False


class Meter:
    """流式调用的计量器: transport 逐块 feed, 结束 close 一次落账。"""

    def __init__(self, model: str, messages: Optional[list] = None, tools_schema: Optional[list] = None,
                 source: str = ""):
        self.model, self.messages, self.tools_schema, self.source = model, messages, tools_schema, source
        self.t0 = time.time()
        self.usage: Optional[dict] = None
        self.text: list = []
        self.reasoning: list = []
        self.tools: list = []
        self.ok = True
        self.closed = False
        self.scope = current()

    def feed(self, chunk: Any):
        if not isinstance(chunk, dict):
            return
        choices = chunk.get("choices") or []
        delta = (choices[0].get("delta") or {}) if choices and isinstance(choices[0], dict) else {}
        u = chunk.get("usage") if isinstance(chunk.get("usage"), dict) else delta.get("usage")
        if isinstance(u, dict) and parse_usage(u):
            self.usage = u
        if delta.get("content"):
            self.text.append(str(delta["content"]))
        rc = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking")
        if rc:
            self.reasoning.append(str(rc))
        for tc in delta.get("tool_calls") or ():
            fn = (tc or {}).get("function") or {}
            if fn.get("name"):
                self.tools.append(fn["name"])
            if fn.get("arguments"):
                self.text.append(str(fn["arguments"]))

    def fail(self):
        self.ok = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        record(self.model, self.usage, source=self.source, messages=self.messages,
               tools_schema=self.tools_schema, out_text="".join(self.text),
               reasoning_text="".join(self.reasoning), duration_ms=int((time.time() - self.t0) * 1000),
               ok=self.ok, tools=self.tools, scope_snapshot=self.scope)
