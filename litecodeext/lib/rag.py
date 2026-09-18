"""
rag.py — 简单检索增强骨架 (P15)
================================
极速版: 不依赖 sentence-transformers / sqlite-vec, 用 stdlib BM25 替代.
后续 v1.5 P15 可升级真 embedding (HuggingFace + sqlite-vec).

API:
  index_dir(workspace, dir_path, glob_pattern="*.md") → 文件 chunk 索引到 sqlite/jsonl
  query(workspace, q, top_k=5) → [{file, chunk, score}, ...]
  
Index 格式: workspace/rag/index.jsonl
  每行: {file, chunk_id, text, tokens (set, 用于 BM25)}

BM25: 标准 TF-IDF 变体, k1=1.5, b=0.75
"""
import json
import re
import math
import time
from pathlib import Path
from typing import List, Dict, Optional


_TOKEN_RE = re.compile(r"[a-zA-Z]+|[0-9]+|[\u4e00-\u9fa5]+")


def _tokenize(text: str) -> List[str]:
    """简单分词: 英文单词 / 数字 / 中文连续段 (不切字)"""
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _chunk_text(text: str, max_chars: int = 500) -> List[str]:
    """按段落 + max_chars 切块"""
    paras = re.split(r"\n\s*\n", text)
    chunks = []
    cur = ""
    for p in paras:
        p = p.strip()
        if not p:
            continue
        if len(cur) + len(p) > max_chars:
            if cur:
                chunks.append(cur)
            cur = p
        else:
            cur = cur + "\n" + p if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def _index_path(workspace) -> Path:
    p = Path(workspace) / "rag" / "index.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def index_dir(workspace, dir_path, glob_pattern: str = "*.md", max_chars: int = 500) -> int:
    """扫一个目录, 把所有文件按 chunk 索引. 返回索引的 chunk 数."""
    d = Path(dir_path)
    if not d.exists():
        return 0
    idx_p = _index_path(workspace)
    n = 0
    with idx_p.open("a", encoding="utf-8") as fout:
        for fp in sorted(d.rglob(glob_pattern)):
            if not fp.is_file():
                continue
            try:
                txt = fp.read_text(errors="replace")
            except Exception:
                continue
            for i, ck in enumerate(_chunk_text(txt, max_chars)):
                tokens = _tokenize(ck)
                entry = {
                    "file": str(fp),
                    "chunk_id": i,
                    "text": ck,
                    "tokens": tokens,
                    "indexed_at": time.time(),
                }
                fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
                n += 1
    return n


def _load_index(workspace) -> List[Dict]:
    idx_p = _index_path(workspace)
    if not idx_p.exists():
        return []
    out = []
    try:
        for line in idx_p.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return out


def query(workspace, q: str, top_k: int = 5,
          k1: float = 1.5, b: float = 0.75) -> List[Dict]:
    """BM25 检索, 返回 top_k chunks. v1.8 P36-b 加埋点."""
    import time as _t
    _t0 = _t.time()
    docs = _load_index(workspace)
    if not docs:
        try:
            from core.telemetry import emit as _emit
            _emit(event="rag_query_no_index",
                  fields={"query_len": len(q), "top_k": top_k},
                  jsonl="rag.jsonl",
                  latency_ms=int((_t.time() - _t0) * 1000))
        except Exception:
            pass
        return []
    q_tokens = _tokenize(q)
    if not q_tokens:
        try:
            from core.telemetry import emit as _emit
            _emit(event="rag_query_no_tokens",
                  fields={"query_len": len(q), "top_k": top_k, "doc_count": len(docs)},
                  jsonl="rag.jsonl",
                  latency_ms=int((_t.time() - _t0) * 1000))
        except Exception:
            pass
        return []
    # 文档统计
    N = len(docs)
    avgdl = sum(len(d.get("tokens", [])) for d in docs) / max(N, 1)
    # IDF 表
    df = {}
    for d in docs:
        for t in set(d.get("tokens", [])):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((N - df[t] + 0.5) / (df[t] + 0.5) + 1) for t in df}
    
    scored = []
    for d in docs:
        tokens = d.get("tokens", [])
        dl = len(tokens)
        if dl == 0:
            continue
        # term freq in this doc
        tf = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for qt in q_tokens:
            if qt not in tf:
                continue
            f = tf[qt]
            id_ = idf.get(qt, 0)
            score += id_ * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / max(avgdl, 1)))
        if score > 0:
            scored.append({
                "file": d["file"],
                "chunk_id": d["chunk_id"],
                "score": round(score, 3),
                "preview": d["text"][:200],
            })
    scored.sort(key=lambda x: x["score"], reverse=True)
    result = scored[:top_k]
    # [v1.8 P36-b] RAG 埋点 — 命中数 + top1 score
    try:
        from core.telemetry import emit as _emit
        _emit(event="rag_query",
              fields={
                  "query_len": len(q),
                  "top_k": top_k,
                  "doc_count": len(docs),
                  "hit_count": len(result),
                  "top1_score": (result[0]["score"] if result else 0),
              },
              jsonl="rag.jsonl",
              latency_ms=int((_t.time() - _t0) * 1000))
    except Exception:
        pass
    return result


def clear_index(workspace) -> bool:
    p = _index_path(workspace)
    if p.exists():
        p.unlink()
        return True
    return False
