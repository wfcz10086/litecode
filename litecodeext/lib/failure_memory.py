"""
failure_memory.py — 失败模式记忆 (P21)
========================================
跨 session 记录"做过什么失败/错误", 下次再见类似情况时绕开.

存储: workspace/telemetry/failures.jsonl (与 P12 埋点同 dir)
每条: {ts, signature, error_type, summary, fix_hint}
- signature: hash 错误关键特征 (用于查重)
- error_type: 'timeout' / 'tool_error' / 'compress_fail' / 'patch_not_found' / 其他
- summary: 错误摘要 (前 200 字)
- fix_hint: 已知修复建议 (空字符串若不知道)

API:
- record_failure(workspace, error_type, summary, fix_hint="")
- find_similar(workspace, error_type, query, limit=3) → 最相关的过往失败
- get_summary(workspace) → 顶级 error_type 计数

不依赖 LLM, 全部规则匹配 (substring + jaccard).
"""
import json
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Optional


def _path(workspace) -> Path:
    return Path(workspace) / "telemetry" / "failures.jsonl"


def _sig(error_type: str, summary: str) -> str:
    """指纹: 错误类型 + summary 头 100 字 -> sha256[:12]"""
    h = hashlib.sha256()
    h.update(error_type.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update(summary[:100].encode("utf-8", errors="replace"))
    return h.hexdigest()[:12]


def record_failure(workspace, error_type: str, summary: str, fix_hint: str = "") -> bool:
    """记一条失败. 返回 True = 新记录, False = 已存在 (按 signature dedup)."""
    try:
        p = _path(workspace)
        p.parent.mkdir(parents=True, exist_ok=True)
        sig = _sig(error_type, summary)
        # 查 dedup
        if p.exists():
            try:
                for line in p.read_text(errors="replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("signature") == sig:
                            return False
                    except Exception:
                        continue
            except Exception:
                pass
        entry = {
            "ts": time.time(),
            "signature": sig,
            "error_type": error_type[:40],
            "summary": summary[:400],
            "fix_hint": fix_hint[:300],
        }
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def _jaccard(a: str, b: str) -> float:
    """简单 jaccard 相似度: 字符 bigram"""
    def grams(s):
        return set(s[i:i+2] for i in range(len(s) - 1))
    A, B = grams(a or ""), grams(b or "")
    if not A and not B:
        return 0.0
    return len(A & B) / max(len(A | B), 1)


def find_similar(workspace, error_type: str, query: str, limit: int = 3) -> List[Dict]:
    """查找最相关的过往失败. error_type 必须匹配 (空串=不限). query 用 jaccard 排序."""
    p = _path(workspace)
    if not p.exists():
        return []
    cands: List[Dict] = []
    try:
        for line in p.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if error_type and obj.get("error_type") != error_type:
                continue
            sim = _jaccard(query, obj.get("summary", ""))
            obj["_sim"] = round(sim, 3)
            cands.append(obj)
    except Exception:
        return []
    cands.sort(key=lambda x: x.get("_sim", 0), reverse=True)
    return cands[:limit]


def get_summary(workspace) -> Dict[str, int]:
    """error_type -> count 的统计"""
    p = _path(workspace)
    if not p.exists():
        return {}
    counts: Dict[str, int] = {}
    try:
        for line in p.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                t = obj.get("error_type", "?")
                counts[t] = counts.get(t, 0) + 1
            except Exception:
                continue
    except Exception:
        return {}
    return counts
