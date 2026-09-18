"""
memory_index.py — SQLite FTS5 语义记忆检索
============================================
零外部依赖的长期记忆检索引擎。

功能:
  - 跨 session 的历史经验索引
  - 错误-解决方案对的精确匹配
  - 代码模式和架构决策检索
  - 自动过期清理

架构位置:
  Supervisor 派发前 → memory_index.search("类似问题")
                   → 注入历史解决路径到 context

vs 向量检索:
  - SQLite FTS5: 零依赖, 关键词匹配, 适合 <1000 条记录
  - ChromaDB:    需安装, 语义匹配, 适合 >1000 条记录
  当前选择 FTS5，未来可平滑迁移到向量方案。

v1.0 新增模块。
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class MemoryIndex:
    """
    基于 SQLite FTS5 的长期记忆检索。

    使用:
        idx = MemoryIndex(workspace / ".memory_index.db")
        # 索引记忆
        idx.index_memory(session_id, "error", "pip install 报 PEP668",
                         solution="加 --break-system-packages")
        # 检索
        results = idx.search("pip install 报错")
        # 注入 context
        ctx = idx.search_as_context("启动 uvicorn 失败")
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """每线程独立连接（SQLite 线程安全要求）。"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self._db_path), timeout=10)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            -- 主表: 记忆条目
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                solution TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                created_at REAL NOT NULL,
                access_count INTEGER DEFAULT 0,
                last_accessed REAL DEFAULT 0
            );

            -- FTS5 全文索引
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content, solution, tags,
                content='memories',
                content_rowid='id',
                tokenize='unicode61'
            );

            -- 触发器: 自动同步 FTS
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, solution, tags)
                VALUES (new.id, new.content, new.solution, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, solution, tags)
                VALUES ('delete', old.id, old.content, old.solution, old.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, solution, tags)
                VALUES ('delete', old.id, old.content, old.solution, old.tags);
                INSERT INTO memories_fts(rowid, content, solution, tags)
                VALUES (new.id, new.content, new.solution, new.tags);
            END;

            -- 索引
            CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
            CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
            CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
        """)
        conn.commit()

    # ── 索引 ──────────────────────────────────────────────

    def index_memory(self, session_id: str, category: str,
                     content: str, solution: str = "",
                     tags: str = "") -> int:
        """
        添加一条记忆。

        category: error / decision / pattern / config / milestone / preference
        content:  问题描述或知识内容
        solution: 解决方案（可选）
        tags:     标签（空格分隔）

        返回: 记录 ID
        """
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO memories (session_id, category, content, solution, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, category, content[:2000], solution[:2000], tags, time.time()),
        )
        conn.commit()
        return cur.lastrowid

    def index_error_solution(self, session_id: str,
                              error_msg: str, root_cause: str,
                              fix: str) -> int:
        """快捷: 索引 错误→根因→修复 三元组。"""
        content = f"ERROR: {error_msg}\nROOT_CAUSE: {root_cause}"
        return self.index_memory(
            session_id, "error", content,
            solution=f"FIX: {fix}", tags="error fix debug",
        )

    def index_decision(self, session_id: str,
                       decision: str, reason: str) -> int:
        """快捷: 索引架构/技术决策。"""
        return self.index_memory(
            session_id, "decision", decision,
            solution=reason, tags="decision architecture",
        )

    def index_from_memory_md(self, session_id: str, memory_text: str) -> int:
        """从 MEMORY.md 内容中批量提取并索引。"""
        import re
        count = 0

        # 提取 Errors & Corrections 段
        errors = re.findall(
            r'- ERROR:\s*(.+?)\s*\|\s*ROOT CAUSE:\s*(.+?)\s*\|\s*FIX:\s*(.+?)$',
            memory_text, re.MULTILINE,
        )
        for err, cause, fix in errors:
            self.index_error_solution(session_id, err.strip(), cause.strip(), fix.strip())
            count += 1

        # 提取 Key References 段
        refs = re.findall(r'- (.+?)$', memory_text, re.MULTILINE)
        for ref in refs:
            if any(kw in ref.lower() for kw in ['path', 'url', 'port', '地址', '路径']):
                self.index_memory(session_id, "config", ref.strip(), tags="reference config")
                count += 1

        return count

    # ── 检索 ──────────────────────────────────────────────

    def search(self, query: str, limit: int = 5,
               category: str = None,
               min_age_days: float = 0) -> List[Dict]:
        """
        全文检索。

        返回: [{id, session_id, category, content, solution, tags, score, age_days}]
        """
        conn = self._get_conn()

        # 构建 FTS 查询（分词）
        tokens = query.strip().split()
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in tokens[:8])

        sql = """
            SELECT m.id, m.session_id, m.category, m.content, m.solution,
                   m.tags, m.created_at, m.access_count,
                   rank AS score
            FROM memories_fts fts
            JOIN memories m ON m.id = fts.rowid
            WHERE memories_fts MATCH ?
        """
        params = [fts_query]

        if category:
            sql += " AND m.category = ?"
            params.append(category)

        if min_age_days > 0:
            cutoff = time.time() - (min_age_days * 86400)
            sql += " AND m.created_at < ?"
            params.append(cutoff)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            return []

        results = []
        for row in rows:
            rid = row[0]
            # 更新访问计数
            conn.execute(
                "UPDATE memories SET access_count = access_count + 1, "
                "last_accessed = ? WHERE id = ?",
                (time.time(), rid),
            )
            age_days = (time.time() - row[6]) / 86400
            results.append({
                "id": rid,
                "session_id": row[1],
                "category": row[2],
                "content": row[3],
                "solution": row[4],
                "tags": row[5],
                "score": abs(row[8]) if len(row) > 8 and row[8] else 0,
                "age_days": round(age_days, 1),
            })
        conn.commit()
        return results

    def search_errors(self, error_msg: str, limit: int = 3) -> List[Dict]:
        """快捷: 搜索历史错误解决方案。"""
        return self.search(error_msg, limit=limit, category="error")

    def search_as_context(self, query: str, limit: int = 3,
                          max_chars: int = 2000) -> str:
        """
        搜索并格式化为 context 字符串，直接注入 Agent prompt。
        """
        results = self.search(query, limit=limit)
        if not results:
            return ""

        parts = ["## 历史经验参考\n"]
        total = 0
        for r in results:
            entry = f"**[{r['category']}]** (session:{r['session_id'][:8]}, {r['age_days']:.0f}天前)\n"
            entry += f"  问题: {r['content'][:200]}\n"
            if r['solution']:
                entry += f"  方案: {r['solution'][:200]}\n"
            entry += "\n"

            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)

        return "".join(parts)

    # ── 维护 ──────────────────────────────────────────────

    def cleanup(self, max_age_days: int = 90,
                max_records: int = 1000) -> int:
        """清理过期/超量记录。"""
        conn = self._get_conn()
        deleted = 0

        # 1. 删除过期
        cutoff = time.time() - (max_age_days * 86400)
        cur = conn.execute(
            "DELETE FROM memories WHERE created_at < ? AND access_count < 3",
            (cutoff,),
        )
        deleted += cur.rowcount

        # 2. 超量时删除最旧的
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if count > max_records:
            excess = count - max_records
            conn.execute(
                "DELETE FROM memories WHERE id IN "
                "(SELECT id FROM memories ORDER BY last_accessed ASC, "
                "access_count ASC LIMIT ?)",
                (excess,),
            )
            deleted += excess

        # 3. 重建 FTS 索引
        if deleted > 0:
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")

        conn.commit()
        return deleted

    def stats(self) -> Dict:
        """索引统计。"""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        by_cat = dict(conn.execute(
            "SELECT category, COUNT(*) FROM memories GROUP BY category"
        ).fetchall())
        oldest = conn.execute(
            "SELECT MIN(created_at) FROM memories"
        ).fetchone()[0]
        return {
            "total": total,
            "by_category": by_cat,
            "oldest_days": round((time.time() - oldest) / 86400, 1) if oldest else 0,
            "db_size_kb": round(self._db_path.stat().st_size / 1024, 1)
                          if self._db_path.exists() else 0,
        }

    def close(self):
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# ── 全局单例 ───────────────────────────────────────────

_instance: Optional[MemoryIndex] = None
_instance_lock = threading.Lock()


def get_index(workspace: Path) -> MemoryIndex:
    """获取全局 MemoryIndex 单例。"""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = MemoryIndex(workspace / ".memory_index.db")
        return _instance
