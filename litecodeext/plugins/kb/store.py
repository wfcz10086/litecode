"""plugins/kb/store.py — PostgreSQL + pgvector 存储与混合检索。

表 (建表/迁移幂等, 首次连接时执行; 思路来自原项目 kb-server.py 的 knowledge_bases/documents/vectors):
  kb_bases      库
  kb_documents  文档: kb_id 外键 (删库级联), content_hash 同库去重, 解析全文 text (上下文窗口与预览用),
                入库状态 status = queued / parsing / embedding / done / failed + progress + error
  kb_chunks     块: 450 字小块, 起止偏移 start_pos/end_pos, 出处 loc/page, 关键词 terms (中文 bigram) →
                tsv 生成列 + GIN; embedding vector(768) + HNSW (pgvector ≥ 0.5, 否则 ivfflat) 余弦索引

检索 = 向量近邻 40 条 ∪ 关键词命中 40 条 → 融合分 0.75×余弦 + 0.25×关键词覆盖率 → 阈值 → 同内容去重 →
取 top_k → 同文档命中按偏移扩成上下文窗口 (默认 3000 字), 窗口重叠的合并成一条, 不重复返回。
"""
from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass, field

from . import cfg
from .text import clean as _clean, terms as _terms

SCHEMA_VERSION = 1
_pool_lock = threading.Lock()
_pool: dict = {"dsn": None, "pool": None, "schema": False, "index": ""}

_DDL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS kb_bases (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS kb_documents (
        id SERIAL PRIMARY KEY,
        kb_id INT NOT NULL REFERENCES kb_bases(id) ON DELETE CASCADE,
        filename TEXT NOT NULL,
        ext TEXT NOT NULL DEFAULT '',
        content_hash TEXT NOT NULL,
        size BIGINT NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'upload',
        status TEXT NOT NULL DEFAULT 'queued',
        progress REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        attempts INT NOT NULL DEFAULT 0,
        text TEXT NOT NULL DEFAULT '',
        chunk_count INT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (kb_id, content_hash))""",
    "CREATE INDEX IF NOT EXISTS kb_documents_status ON kb_documents (status, id)",
    """CREATE TABLE IF NOT EXISTS kb_chunks (
        id BIGSERIAL PRIMARY KEY,
        kb_id INT NOT NULL REFERENCES kb_bases(id) ON DELETE CASCADE,
        doc_id INT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
        chunk_index INT NOT NULL,
        start_pos INT NOT NULL,
        end_pos INT NOT NULL,
        content TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        loc TEXT NOT NULL DEFAULT '',
        page INT,
        terms TEXT NOT NULL DEFAULT '',
        tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', terms)) STORED,
        embedding vector(768),
        UNIQUE (doc_id, chunk_index))""",
    "CREATE INDEX IF NOT EXISTS kb_chunks_kb ON kb_chunks (kb_id)",
    "CREATE INDEX IF NOT EXISTS kb_chunks_tsv ON kb_chunks USING gin (tsv)",
]


class KBError(RuntimeError):
    """给用户看的错误 (中文)。"""


# ── 连接 ──────────────────────────────────────────────────────────

def _get_pool():
    dsn = cfg().get("dsn") or ""
    if not dsn:
        raise KBError("知识库未配置数据库 (config.json kb.dsn)")
    with _pool_lock:
        if _pool["dsn"] != dsn or _pool["pool"] is None:
            import psycopg2.pool
            if _pool["pool"] is not None:
                with contextlib.suppress(Exception):
                    _pool["pool"].closeall()
            try:
                _pool["pool"] = psycopg2.pool.ThreadedConnectionPool(1, 8, dsn, connect_timeout=5)
            except Exception as e:  # noqa: BLE001
                _pool["pool"] = None
                raise KBError(f"连不上知识库数据库: {str(e).strip()[:200]}") from e
            _pool.update(dsn=dsn, schema=False)
        return _pool["pool"]


@contextlib.contextmanager
def conn():
    """取一个连接; 块正常结束提交, 异常回滚。坏连接直接丢弃。"""
    pool = _get_pool()
    c = pool.getconn()
    broken = False
    try:
        yield c
        c.commit()
    except Exception:
        with contextlib.suppress(Exception):
            c.rollback()
        broken = bool(getattr(c, "closed", 0))
        raise
    finally:
        pool.putconn(c, close=broken)


def db():
    """保证建过表后返回连接上下文。"""
    if not _pool["schema"] or _pool["dsn"] != cfg().get("dsn"):
        ensure_schema()
    return conn()


def ensure_schema() -> str:
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(7420915)")      # 多进程同时启动只建一次
        for sql in _DDL:
            try:
                cur.execute("SAVEPOINT s")
                cur.execute(sql)
                cur.execute("RELEASE SAVEPOINT s")
            except Exception as e:  # noqa: BLE001
                cur.execute("ROLLBACK TO SAVEPOINT s")
                if "EXTENSION" in sql:      # 非超级用户建不了扩展: 部署脚本应已建好, 这里只检查
                    continue
                raise KBError(f"知识库建表失败: {e}") from e
        cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
        row = cur.fetchone()
        if not row:
            raise KBError("数据库缺少 pgvector 扩展 (需超级用户执行 CREATE EXTENSION vector)")
        ver = tuple(int(x) for x in row[0].split(".")[:2])
        kind = "hnsw" if ver >= (0, 5) else "ivfflat"
        cur.execute("SELECT 1 FROM pg_indexes WHERE indexname='kb_chunks_vec'")
        if not cur.fetchone():
            opt = "" if kind == "hnsw" else " WITH (lists = 100)"
            cur.execute(f"CREATE INDEX kb_chunks_vec ON kb_chunks USING {kind} (embedding vector_cosine_ops){opt}")
        cur.execute("INSERT INTO kb_meta VALUES ('schema_version', %s) ON CONFLICT (key) DO UPDATE SET value = "
                    "EXCLUDED.value", (str(SCHEMA_VERSION),))
    _pool.update(schema=True, index=f"{kind} (pgvector {row[0]})")
    return _pool["index"]


def status() -> dict:
    out = {"db_ok": False, "index": "", "error": ""}
    try:
        with db() as c, c.cursor() as cur:
            cur.execute("SELECT version()")
            out["pg"] = cur.fetchone()[0].split(" on ")[0]
        out.update(db_ok=True, index=_pool["index"])
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
    return out


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _iso(rows):
    for r in rows:
        for k in ("created_at", "updated_at"):
            if r.get(k) is not None and hasattr(r[k], "isoformat"):
                r[k] = r[k].isoformat(timespec="seconds")
    return rows


# ── 库 ────────────────────────────────────────────────────────────

def list_bases() -> list[dict]:
    with db() as c, c.cursor() as cur:
        cur.execute("""SELECT b.id, b.name, b.description, b.created_at, b.updated_at,
                              count(d.id) AS doc_count,
                              coalesce(sum(d.chunk_count), 0)::int AS chunk_count,
                              count(d.id) FILTER (WHERE d.status NOT IN ('done', 'failed')) AS pending,
                              count(d.id) FILTER (WHERE d.status = 'failed') AS failed
                       FROM kb_bases b LEFT JOIN kb_documents d ON d.kb_id = b.id
                       GROUP BY b.id ORDER BY b.id""")
        return _iso(_rows(cur))


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise KBError("库名不能为空, 且不超过 80 字")
    return name


def create_base(name: str, description: str = "") -> dict:
    name = _clean_name(name)
    with db() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM kb_bases WHERE name = %s", (name,))
        if cur.fetchone():
            raise KBError(f"已有同名知识库「{name}」")
        cur.execute("INSERT INTO kb_bases (name, description) VALUES (%s, %s) RETURNING id, name, description",
                    (name, (description or "").strip()[:500]))
        return _rows(cur)[0]


def update_base(kb_id: int, name: str | None = None, description: str | None = None) -> dict:
    with db() as c, c.cursor() as cur:
        if name is not None:
            name = _clean_name(name)
            cur.execute("SELECT 1 FROM kb_bases WHERE name = %s AND id <> %s", (name, kb_id))
            if cur.fetchone():
                raise KBError(f"已有同名知识库「{name}」")
        cur.execute("""UPDATE kb_bases SET name = coalesce(%s, name), description = coalesce(%s, description),
                       updated_at = now() WHERE id = %s RETURNING id, name, description""",
                    (name, None if description is None else description.strip()[:500], kb_id))
        rows = _rows(cur)
        if not rows:
            raise KBError("知识库不存在")
        return rows[0]


def delete_base(kb_id: int) -> bool:
    with db() as c, c.cursor() as cur:
        cur.execute("DELETE FROM kb_bases WHERE id = %s", (kb_id,))
        return cur.rowcount > 0


def get_base(kb_id: int) -> dict | None:
    with db() as c, c.cursor() as cur:
        cur.execute("SELECT id, name, description FROM kb_bases WHERE id = %s", (kb_id,))
        rows = _rows(cur)
        return rows[0] if rows else None


def resolve_bases(spec) -> list[dict]:
    """库名 / id / 列表 / 空 (=全部) → [{id, name}]。名字支持不区分大小写的精确或包含匹配。"""
    with db() as c, c.cursor() as cur:
        cur.execute("SELECT id, name FROM kb_bases ORDER BY id")
        allb = _rows(cur)
    if spec in (None, "", [], "*", "all", "全部"):
        return allb
    items = spec if isinstance(spec, (list, tuple)) else [spec]
    out = []
    for it in items:
        s = str(it).strip().lower()
        hit = [b for b in allb if str(b["id"]) == s or b["name"].lower() == s] or \
              [b for b in allb if s and s in b["name"].lower()]
        out.extend(b for b in hit if b not in out)
    return out


# ── 文档 ──────────────────────────────────────────────────────────

_DOC_COLS = ("id, kb_id, filename, ext, content_hash, size, source, status, progress, error, attempts, "
             "chunk_count, length(text) AS text_len, created_at, updated_at")


def add_document(kb_id: int, filename: str, ext: str, content_hash: str, size: int,
                 source: str = "upload") -> tuple[dict, bool]:
    """→ (文档, 是否新建)。同库同内容 (sha256) 已存在则返回已有文档, 不重复入库。"""
    with db() as c, c.cursor() as cur:
        cur.execute("""INSERT INTO kb_documents (kb_id, filename, ext, content_hash, size, source)
                       VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (kb_id, content_hash) DO NOTHING
                       RETURNING """ + _DOC_COLS, (kb_id, filename[:255], ext, content_hash, size, source))
        rows = _rows(cur)
        if rows:
            return _iso(rows)[0], True
        cur.execute(f"SELECT {_DOC_COLS} FROM kb_documents WHERE kb_id = %s AND content_hash = %s",
                    (kb_id, content_hash))
        return _iso(_rows(cur))[0], False


def list_documents(kb_id: int) -> list[dict]:
    with db() as c, c.cursor() as cur:
        cur.execute(f"SELECT {_DOC_COLS} FROM kb_documents WHERE kb_id = %s ORDER BY id DESC", (kb_id,))
        return _iso(_rows(cur))


def get_document(doc_id: int) -> dict | None:
    with db() as c, c.cursor() as cur:
        cur.execute(f"SELECT {_DOC_COLS} FROM kb_documents WHERE id = %s", (doc_id,))
        rows = _iso(_rows(cur))
        return rows[0] if rows else None


def delete_document(doc_id: int) -> dict | None:
    with db() as c, c.cursor() as cur:
        cur.execute("DELETE FROM kb_documents WHERE id = %s RETURNING id, kb_id, ext, content_hash", (doc_id,))
        rows = _rows(cur)
        return rows[0] if rows else None


def retry_document(doc_id: int) -> bool:
    with db() as c, c.cursor() as cur:
        cur.execute("""UPDATE kb_documents SET status = 'queued', progress = 0, error = '', updated_at = now()
                       WHERE id = %s AND status IN ('failed', 'done')""", (doc_id,))
        return cur.rowcount > 0


def claim_next() -> dict | None:
    """后台队列认领一份待处理文档 (单语句事务 + FOR UPDATE SKIP LOCKED, 多进程/多线程不会重复认领)。"""
    with db() as c, c.cursor() as cur:
        cur.execute(f"""UPDATE kb_documents SET status = 'parsing', progress = 0.02, error = '',
                           attempts = attempts + 1, updated_at = now()
                        WHERE id = (SELECT id FROM kb_documents WHERE status = 'queued'
                                    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                        RETURNING {_DOC_COLS}""")
        rows = _rows(cur)
        return _iso(rows)[0] if rows else None


def requeue_stale(minutes: int = 15) -> int:
    """进程重启前没做完的 (parsing/embedding 且久未更新) 放回队列。"""
    with db() as c, c.cursor() as cur:
        cur.execute("""UPDATE kb_documents SET status = 'queued', updated_at = now()
                       WHERE status IN ('parsing', 'embedding') AND updated_at < now() - %s * interval '1 minute'""",
                    (minutes,))
        return cur.rowcount


def set_status(doc_id: int, status: str, progress: float | None = None, error: str | None = None) -> None:
    with db() as c, c.cursor() as cur:
        cur.execute("""UPDATE kb_documents SET status = %s, progress = coalesce(%s, progress),
                       error = coalesce(%s, error), updated_at = now() WHERE id = %s""",
                    (status, progress, None if error is None else error[:1000], doc_id))


def save_chunks(doc_id: int, kb_id: int, text: str, chunks: list[dict]) -> None:
    """一个事务内: 删旧块 → 写新块 → 文档置 done。文档已被删除则什么都不写。"""
    from psycopg2.extras import execute_values
    from .embed import to_pg
    with db() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM kb_documents WHERE id = %s FOR UPDATE", (doc_id,))
        if not cur.fetchone():
            return
        cur.execute("DELETE FROM kb_chunks WHERE doc_id = %s", (doc_id,))
        execute_values(cur, """INSERT INTO kb_chunks (kb_id, doc_id, chunk_index, start_pos, end_pos, content,
                                   content_hash, loc, page, terms, embedding) VALUES %s""",
                       [(kb_id, doc_id, ch["index"], ch["start"], ch["end"], ch["content"], ch["hash"],
                         ch["loc"], ch["page"], ch["terms"], to_pg(ch["vec"])) for ch in chunks],
                       template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)", page_size=200)
        cur.execute("""UPDATE kb_documents SET text = %s, chunk_count = %s, status = 'done', progress = 1,
                       error = '', updated_at = now() WHERE id = %s""", (text, len(chunks), doc_id))


def list_chunks(doc_id: int, offset: int = 0, limit: int = 50) -> list[dict]:
    with db() as c, c.cursor() as cur:
        cur.execute("""SELECT chunk_index, loc, page, start_pos, end_pos, content FROM kb_chunks
                       WHERE doc_id = %s ORDER BY chunk_index OFFSET %s LIMIT %s""",
                    (doc_id, max(0, offset), max(1, min(limit, 200))))
        return _rows(cur)


def doc_text(doc_id: int, start: int = 0, length: int = 20000) -> str:
    with db() as c, c.cursor() as cur:
        cur.execute("SELECT substr(text, %s, %s) FROM kb_documents WHERE id = %s",
                    (max(0, start) + 1, max(1, length), doc_id))
        row = cur.fetchone()
        return row[0] if row else ""


# ── 检索 ──────────────────────────────────────────────────────────

@dataclass
class Hit:
    chunk_id: int
    doc_id: int
    chunk_index: int
    start: int
    end: int
    content: str
    content_hash: str
    loc: str
    page: int | None
    filename: str
    kb_name: str
    cos: float
    coverage: float = 0.0
    score: float = 0.0
    terms: set = field(default_factory=set)


def fuse(hits: list[Hit], q_terms: list[str], min_score: float, top_k: int,
         w_vec: float = 0.75, w_kw: float = 0.25) -> list[Hit]:
    """融合打分 + 阈值 + 同内容去重 + 截断 (纯函数, 便于单测)。"""
    qs = set(q_terms)
    for h in hits:
        h.coverage = len(qs & h.terms) / len(qs) if qs else 0.0
        h.score = round(w_vec * max(h.cos, 0.0) + w_kw * h.coverage, 4)
    seen, out = set(), []
    for h in sorted(hits, key=lambda x: (-x.score, x.doc_id, x.chunk_index)):
        if h.score < min_score or h.content_hash in seen:
            continue
        seen.add(h.content_hash)
        out.append(h)
        if len(out) >= top_k:
            break
    return out


def windows(hits: list[Hit], doc_len: dict[int, int], context_chars: int, total_chars: int = 0) -> list[dict]:
    """命中块 → 上下文窗口 (纯函数)。按分数从高到低: 已被前面窗口覆盖的命中并入该窗口; 否则以块为中心向两边
    扩到 context_chars (贴着文档首尾时往另一侧补), 与同文档已有窗口重叠的部分裁掉 (同一段文字只出现一次),
    最后把同文档首尾相接的窗口合成一个。total_chars>0 时为所有窗口的总字数上限, 用完就不再开新窗口。
    返回 [{doc_id, start, end, hits:[Hit...]}], 按窗口内最高分排序。"""
    wins: list[dict] = []
    budget = total_chars if total_chars > 0 else 10 ** 12
    for h in sorted(hits, key=lambda x: -x.score):
        same = [w for w in wins if w["doc_id"] == h.doc_id]
        host = next((w for w in same if w["start"] <= h.start and h.end <= w["end"]), None)
        if host:
            host["hits"].append(h)
            continue
        size = min(context_chars, budget)
        if size < h.end - h.start:
            if wins:
                continue
            size = h.end - h.start
        n = max(doc_len.get(h.doc_id, h.end), h.end)
        pad = (size - (h.end - h.start)) // 2
        a, b = max(0, h.start - pad), min(n, h.end + pad)
        if b - a < size:                              # 贴着文档首尾: 另一侧补足
            a, b = max(0, min(a, b - size)), min(n, max(b, a + size))
        for w in sorted(same, key=lambda w: w["start"]):
            if w["start"] < b and a < w["end"]:
                if w["start"] <= a:
                    a = w["end"]
                else:
                    b = w["start"]
        if b <= a:                                    # 已被前后两个窗口完全覆盖
            host = next((w for w in same if w["start"] < h.end and h.start < w["end"]), None)
            if host:
                host["hits"].append(h)
            continue
        budget -= b - a
        wins.append({"doc_id": h.doc_id, "start": a, "end": b, "hits": [h]})
    out: list[dict] = []
    for w in sorted(wins, key=lambda w: (w["doc_id"], w["start"])):
        if out and out[-1]["doc_id"] == w["doc_id"] and w["start"] <= out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["end"], w["end"])
            out[-1]["hits"] += w["hits"]
        else:
            out.append(w)
    for w in out:
        w["hits"].sort(key=lambda x: -x.score)
    out.sort(key=lambda w: -w["hits"][0].score)
    return out


def search(query: str, kb_ids: list[int], top_k: int | None = None, min_score: float | None = None,
           context_chars: int | None = None, with_context: bool = True) -> list[dict]:
    from .embed import encode, to_pg
    c_ = cfg()
    top_k = int(top_k or c_["top_k"])
    min_score = float(c_["min_score"] if min_score is None else min_score)
    context_chars = int(context_chars or c_["context_chars"])
    query = _clean(query).strip()
    if not query or not kb_ids:
        return []
    qv = to_pg(encode([query])[0])
    q_terms = list(dict.fromkeys(_terms(query, query=True)))
    with db() as c, c.cursor() as cur:
        cur.execute("""SELECT id FROM kb_chunks WHERE kb_id = ANY(%s)
                       ORDER BY embedding <=> %s::vector LIMIT 40""", (kb_ids, qv))
        ids = [r[0] for r in cur.fetchall()]
        if q_terms:
            tsq = " | ".join("'" + t.replace("'", "") + "'" for t in q_terms[:64])
            cur.execute("""SELECT id FROM kb_chunks, to_tsquery('simple', %s) q
                           WHERE kb_id = ANY(%s) AND tsv @@ q ORDER BY ts_rank(tsv, q) DESC LIMIT 40""",
                        (tsq, kb_ids))
            ids += [r[0] for r in cur.fetchall() if r[0] not in ids]
        if not ids:
            return []
        cur.execute("""SELECT ch.id, ch.doc_id, ch.chunk_index, ch.start_pos, ch.end_pos, ch.content,
                              ch.content_hash, ch.loc, ch.page, ch.terms, d.filename, b.name,
                              1 - (ch.embedding <=> %s::vector) AS cos, length(d.text) AS doc_len
                       FROM kb_chunks ch JOIN kb_documents d ON d.id = ch.doc_id JOIN kb_bases b ON b.id = ch.kb_id
                       WHERE ch.id = ANY(%s)""", (qv, ids))
        rows = cur.fetchall()
        hits = [Hit(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[10], r[11], float(r[12]),
                    terms=set(r[9].split())) for r in rows]
        doc_len = {r[1]: r[13] for r in rows}
        top = fuse(hits, q_terms, min_score, top_k)
        if not with_context:
            return [{"doc_id": h.doc_id, "filename": h.filename, "kb": h.kb_name, "loc": h.loc, "page": h.page,
                     "chunk_index": h.chunk_index, "score": h.score, "cos": round(h.cos, 4),
                     "coverage": round(h.coverage, 3), "snippet": h.content,
                     "hit_start": h.start, "hit_end": h.end} for h in top]
        out = []
        for w in windows(top, doc_len, context_chars, int(c_.get("total_chars") or 0)):
            cur.execute("SELECT substr(text, %s, %s) FROM kb_documents WHERE id = %s",
                        (w["start"] + 1, w["end"] - w["start"], w["doc_id"]))
            ctx = (cur.fetchone() or [""])[0]
            best = w["hits"][0]
            out.append({
                "doc_id": w["doc_id"], "filename": best.filename, "kb": best.kb_name,
                "loc": best.loc,
                "also": "、".join(dict.fromkeys(h.loc for h in sorted(w["hits"], key=lambda x: x.start)
                                               if h.loc and h.loc != best.loc)),
                "page": best.page, "chunk_index": best.chunk_index, "score": best.score,
                "cos": round(best.cos, 4), "coverage": round(best.coverage, 3),
                "snippet": best.content, "context": ctx, "start": w["start"], "end": w["end"],
                "hit_start": best.start, "hit_end": best.end,
                "head": w["start"] > 0, "tail": w["end"] < doc_len.get(w["doc_id"], w["end"]),
            })
        return out
