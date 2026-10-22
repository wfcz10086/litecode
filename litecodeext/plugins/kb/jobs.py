"""plugins/kb/jobs.py — 后台入库队列 + 原始文件存放。

队列状态全在数据库 (kb_documents.status), 进程内只有一个守护线程:
  认领   store.claim_next(): 单语句 UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)
         —— 多个 web_ui 进程 (如测试第二实例) 同时跑也不会重复处理同一份文档;
  处理   解析 (parsing) → 切块 → 向量化 (embedding, 按批更新进度) → 一个事务写入全部块 (done);
  失败   status=failed + 错误原因, 面板上点「重试」放回队列; 进程重启时把卡在中途的放回队列。
上传/新文档入库后调用 notify() 立即唤醒, 平时每 5 秒看一眼队列。

原始文件: WORKSPACE/kb/<kb_id>/files/<sha256>.<ext> (同库同内容只存一份, 原文件名在 kb_documents.filename);
删除文档/库时同步删文件。所有路径都由 int 库 id + 十六进制哈希 + 白名单扩展名拼出, 再校验在库目录内。
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import time
from pathlib import Path

from . import cfg, data_dir, enabled

log = logging.getLogger("kb.jobs")

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_EXT_RE = re.compile(r"^\.[a-z0-9]{1,8}$")
_wake = threading.Event()
_state = {"thread": None}
_lock = threading.Lock()


# ── 文件 ──────────────────────────────────────────────────────────

def kb_dir(kb_id: int) -> Path:
    return data_dir() / str(int(kb_id))


def file_path(kb_id: int, content_hash: str, ext: str) -> Path:
    """原始文件路径; 参数不合法或拼出的路径不在库目录内则抛 ValueError。"""
    if not _HASH_RE.match(content_hash or "") or not (ext == "" or _EXT_RE.match(ext)):
        raise ValueError("非法的文件标识")
    root = (kb_dir(kb_id) / "files").resolve()
    p = (root / f"{content_hash}{ext}").resolve()
    if p.parent != root:
        raise ValueError("路径越界")
    return p


def norm_ext(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    return ext if _EXT_RE.match(ext) else ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def store_file(kb_id: int, src: Path, content_hash: str, ext: str, move: bool) -> Path:
    dst = file_path(kb_id, content_hash, ext)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if move:
            Path(src).unlink(missing_ok=True)
    elif move:
        shutil.move(str(src), str(dst))
    else:
        shutil.copyfile(str(src), str(dst))
    return dst


def remove_file(kb_id: int, content_hash: str, ext: str) -> None:
    try:
        file_path(kb_id, content_hash, ext).unlink(missing_ok=True)
    except (ValueError, OSError) as e:
        log.warning("[kb] 删除原始文件失败: %s", e)


def remove_kb_dir(kb_id: int) -> None:
    d = kb_dir(kb_id).resolve()
    if d.parent == data_dir().resolve() and d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


# ── 处理一份文档 ───────────────────────────────────────────────────

def process(doc: dict) -> None:
    from . import store, text
    from .embed import encode
    c = cfg()
    did = doc["id"]
    try:
        path = file_path(doc["kb_id"], doc["content_hash"], doc["ext"])
        if not path.is_file():
            raise RuntimeError("原始文件不见了, 请删除后重新上传")
        segs = text.parse(path, ocr=bool(c.get("ocr", True)))
        full, spans = text.join(segs)
        if not full.strip():
            raise RuntimeError("没有解析出文字 (空文档, 或扫描件识别失败)")
        pieces = text.split(full, c["chunk_size"], c["chunk_overlap"])
        store.set_status(did, "embedding", 0.1)
        chunks, batch = [], 16
        for k in range(0, len(pieces), batch):
            part = pieces[k:k + batch]
            vecs = encode([p[2] for p in part])
            for (a, b, content), v in zip(part, vecs):
                loc, page = text.locate(spans, a, b)
                chunks.append({"index": len(chunks), "start": a, "end": b, "content": content,
                               "hash": hashlib.sha1(content.encode()).hexdigest(), "loc": loc, "page": page,
                               "terms": " ".join(dict.fromkeys(text.terms(content))), "vec": v})
            store.set_status(did, "embedding", round(0.1 + 0.85 * min(1.0, (k + batch) / len(pieces)), 3))
        store.save_chunks(did, doc["kb_id"], full, chunks)
        log.info("[kb] 入库完成 doc=%s %s: %d 字 %d 块", did, doc["filename"], len(full), len(chunks))
    except Exception as e:  # noqa: BLE001  任何失败都落到文档状态上, 不让线程挂掉
        log.warning("[kb] 入库失败 doc=%s %s: %s", did, doc.get("filename"), e)
        try:
            store.set_status(did, "failed", error=str(e) or type(e).__name__)
        except Exception:  # noqa: BLE001
            pass


# ── 守护线程 ──────────────────────────────────────────────────────

def _loop() -> None:
    from . import store
    requeued = False
    while True:
        try:
            if not enabled():
                _wake.wait(10)
                _wake.clear()
                continue
            if not requeued:
                n = store.requeue_stale()
                requeued = True
                if n:
                    log.info("[kb] %d 份中断的文档已放回队列", n)
            doc = store.claim_next()
            if doc is None:
                _wake.wait(5)
                _wake.clear()
                continue
            process(doc)
        except Exception as e:  # noqa: BLE001  数据库暂时不可用等: 稍后再试
            log.warning("[kb] 队列循环出错: %s", e)
            time.sleep(10)


def start() -> None:
    with _lock:
        t = _state["thread"]
        if t is None or not t.is_alive():
            t = threading.Thread(target=_loop, name="kb-ingest", daemon=True)
            t.start()
            _state["thread"] = t


def notify() -> None:
    start()
    _wake.set()
