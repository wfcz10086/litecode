#!/usr/bin/env python3
"""wecom_store.py — 每个企微 bot 一份 SQLite 状态存储.

存 ~/.wecom/{bot_id}/state.sqlite:
  - contacts      userid → 首/末次交互 + 名称 + 消息数
  - messages      收发流水 (in/out, msgtype, content_json, media_id)
  - media         已上传/下载媒体 (media_id 3 天过期)
  - rate_limit    per-userid 分钟/小时桶 (30/min · 1000/hr)
"""
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

_BASE = Path.home() / ".wecom"
_MEDIA_TTL_SEC = 3 * 24 * 3600  # 企微 media_id 3 天过期


def _bot_dir(bot_id: str) -> Path:
    d = _BASE / bot_id
    d.mkdir(parents=True, exist_ok=True)
    return d


class WeComStore:
    """线程安全的 per-bot SQLite. sqlite3 用 check_same_thread=False + Lock."""

    def __init__(self, bot_id: str):
        self.bot_id = bot_id
        self.dir = _bot_dir(bot_id)
        self.db_path = self.dir / "state.sqlite"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False,
                                     isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self):
        cur = self._conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            userid       TEXT PRIMARY KEY,
            name         TEXT DEFAULT '',
            first_seen   REAL,
            last_seen    REAL,
            msg_in_count INTEGER DEFAULT 0,
            msg_out_count INTEGER DEFAULT 0,
            chat_type    TEXT DEFAULT 'single',
            extra_json   TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL,
            direction   TEXT,        -- 'in' | 'out'
            userid      TEXT,
            msgtype     TEXT,        -- text/markdown/file/image/voice/video/stream
            content     TEXT,        -- 展平后的纯文本 (用于列表预览)
            content_json TEXT,       -- 完整 body JSON
            media_id    TEXT DEFAULT '',
            msgid       TEXT DEFAULT '',
            errcode     INTEGER DEFAULT 0,
            errmsg      TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_messages_userid_ts
            ON messages(userid, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_ts
            ON messages(ts DESC);
        CREATE TABLE IF NOT EXISTS media (
            media_id     TEXT PRIMARY KEY,
            filename     TEXT,
            mtype        TEXT,        -- file/image/voice/video
            size         INTEGER,
            md5          TEXT,
            local_path   TEXT DEFAULT '',
            uploaded_at  REAL,
            expires_at   REAL
        );
        CREATE TABLE IF NOT EXISTS rate_limit (
            userid       TEXT,
            bucket_type  TEXT,        -- 'minute' | 'hour'
            bucket_key   INTEGER,     -- floor(ts / 60) or floor(ts/3600)
            count        INTEGER DEFAULT 0,
            PRIMARY KEY (userid, bucket_type, bucket_key)
        );
        """)

    # ── contacts ──────────────────────────────────────────────
    def upsert_contact(self, userid: str, chat_type: str = "single",
                       name: str = "", direction: str = "in"):
        if not userid:
            return
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT userid FROM contacts WHERE userid=?", (userid,)
            ).fetchone()
            if row:
                if direction == "in":
                    self._conn.execute(
                        "UPDATE contacts SET last_seen=?, msg_in_count=msg_in_count+1, "
                        "chat_type=?, name=CASE WHEN ?='' THEN name ELSE ? END "
                        "WHERE userid=?",
                        (now, chat_type, name, name, userid),
                    )
                else:
                    self._conn.execute(
                        "UPDATE contacts SET last_seen=?, msg_out_count=msg_out_count+1 "
                        "WHERE userid=?",
                        (now, userid),
                    )
            else:
                in_c = 1 if direction == "in" else 0
                out_c = 1 if direction == "out" else 0
                self._conn.execute(
                    "INSERT INTO contacts(userid, name, first_seen, last_seen, "
                    "msg_in_count, msg_out_count, chat_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (userid, name, now, now, in_c, out_c, chat_type),
                )

    def list_contacts(self, limit: int = 200) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT userid, name, first_seen, last_seen, msg_in_count, "
                "msg_out_count, chat_type FROM contacts "
                "ORDER BY last_seen DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{
            "userid": r[0], "name": r[1], "first_seen": r[2], "last_seen": r[3],
            "msg_in_count": r[4], "msg_out_count": r[5], "chat_type": r[6],
        } for r in rows]

    def contact_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM contacts").fetchone()
        return int(row[0]) if row else 0

    # ── messages ──────────────────────────────────────────────
    def add_message(self, direction: str, userid: str, msgtype: str,
                    content: str = "", body: Optional[dict] = None,
                    media_id: str = "", msgid: str = "",
                    errcode: int = 0, errmsg: str = "") -> int:
        content = (content or "")[:2000]
        body_json = json.dumps(body or {}, ensure_ascii=False)[:8000]
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(ts, direction, userid, msgtype, content, "
                "content_json, media_id, msgid, errcode, errmsg) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), direction, userid, msgtype, content,
                 body_json, media_id, msgid, errcode, errmsg),
            )
            return cur.lastrowid or 0

    def clear_messages(self, userid: str = "") -> int:
        """清空消息表. userid 为空 → 清所有; 否则只清该 userid 的.
        返回删除行数. 联系人 (contacts) / media / rate_limit 表**不动**.
        """
        with self._lock:
            if userid:
                cur = self._conn.execute(
                    "DELETE FROM messages WHERE userid=?", (userid,))
            else:
                cur = self._conn.execute("DELETE FROM messages")
            n = cur.rowcount or 0
            try:
                self._conn.execute("VACUUM")
            except Exception:
                pass
        return int(n)

    def list_messages(self, userid: str = "", limit: int = 50) -> list:
        with self._lock:
            if userid:
                rows = self._conn.execute(
                    "SELECT id, ts, direction, userid, msgtype, content, media_id, "
                    "msgid, errcode, errmsg FROM messages WHERE userid=? "
                    "ORDER BY ts DESC LIMIT ?", (userid, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, ts, direction, userid, msgtype, content, media_id, "
                    "msgid, errcode, errmsg FROM messages "
                    "ORDER BY ts DESC LIMIT ?", (limit,),
                ).fetchall()
        return [{
            "id": r[0], "ts": r[1], "direction": r[2], "userid": r[3],
            "msgtype": r[4], "content": r[5], "media_id": r[6],
            "msgid": r[7], "errcode": r[8], "errmsg": r[9],
        } for r in rows]

    # ── media ─────────────────────────────────────────────────
    def add_media(self, media_id: str, filename: str, mtype: str,
                  size: int, md5: str = "", local_path: str = ""):
        if not media_id:
            return
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO media(media_id, filename, mtype, size, md5, "
                "local_path, uploaded_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (media_id, filename, mtype, size, md5, local_path,
                 now, now + _MEDIA_TTL_SEC),
            )

    def find_media_by_md5(self, md5: str) -> Optional[dict]:
        """按 md5 找未过期的 media_id 复用."""
        if not md5:
            return None
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT media_id, filename, mtype, size, expires_at, local_path "
                "FROM media WHERE md5=? AND expires_at > ? "
                "ORDER BY uploaded_at DESC LIMIT 1", (md5, now),
            ).fetchone()
        if not row:
            return None
        return {"media_id": row[0], "filename": row[1], "mtype": row[2],
                "size": row[3], "expires_at": row[4], "local_path": row[5]}

    # ── rate_limit ────────────────────────────────────────────
    def check_and_incr_rate(self, userid: str,
                            per_min: int = 30, per_hour: int = 1000) -> tuple:
        """返回 (allowed: bool, reason: str). 允许则计数 +1."""
        if not userid:
            return True, ""
        now = time.time()
        m_key = int(now // 60)
        h_key = int(now // 3600)
        with self._lock:
            # 清老桶 (>2 小时)
            self._conn.execute(
                "DELETE FROM rate_limit WHERE bucket_type='minute' AND bucket_key<?",
                (m_key - 5,),
            )
            self._conn.execute(
                "DELETE FROM rate_limit WHERE bucket_type='hour' AND bucket_key<?",
                (h_key - 2,),
            )
            m_row = self._conn.execute(
                "SELECT count FROM rate_limit WHERE userid=? AND bucket_type='minute' "
                "AND bucket_key=?", (userid, m_key),
            ).fetchone()
            h_row = self._conn.execute(
                "SELECT count FROM rate_limit WHERE userid=? AND bucket_type='hour' "
                "AND bucket_key=?", (userid, h_key),
            ).fetchone()
            m_cnt = m_row[0] if m_row else 0
            h_cnt = h_row[0] if h_row else 0
            if m_cnt >= per_min:
                return False, f"minute limit {per_min} hit ({m_cnt})"
            if h_cnt >= per_hour:
                return False, f"hour limit {per_hour} hit ({h_cnt})"
            self._conn.execute(
                "INSERT INTO rate_limit(userid, bucket_type, bucket_key, count) "
                "VALUES (?, 'minute', ?, 1) "
                "ON CONFLICT(userid, bucket_type, bucket_key) DO UPDATE SET count=count+1",
                (userid, m_key),
            )
            self._conn.execute(
                "INSERT INTO rate_limit(userid, bucket_type, bucket_key, count) "
                "VALUES (?, 'hour', ?, 1) "
                "ON CONFLICT(userid, bucket_type, bucket_key) DO UPDATE SET count=count+1",
                (userid, h_key),
            )
        return True, ""

    def stats(self) -> dict:
        with self._lock:
            c = self._conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
            m = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            md = self._conn.execute(
                "SELECT COUNT(*) FROM media WHERE expires_at>?", (time.time(),)
            ).fetchone()[0]
        return {"contacts": c, "messages": m, "media_valid": md}

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


# ── 单例注册表 ────────────────────────────────────────────────
_STORES: dict = {}
_STORES_LOCK = threading.Lock()


def get_store(bot_id: str) -> WeComStore:
    with _STORES_LOCK:
        s = _STORES.get(bot_id)
        if s is None:
            s = WeComStore(bot_id)
            _STORES[bot_id] = s
        return s


def drop_store(bot_id: str):
    with _STORES_LOCK:
        s = _STORES.pop(bot_id, None)
        if s:
            s.close()
