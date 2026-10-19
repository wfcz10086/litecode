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
- record_failure(workspace, error_type, summary, fix_hint="", extra=None)
  extra: 附加字段 (如 stuck_at / detail), 每个值截 300 字, 不参与指纹
- find_similar(workspace, error_type, query, limit=3) → 最相关的过往失败
- get_summary(workspace) → 顶级 error_type 计数
- resolve_similar(workspace, query, threshold) → 相似任务后来成功了, 删掉对应失败记录

[2026-10-19] 过期 + 取最新 + 成功清除: 原来记录永不过期、后来成功也不清、去重保留最旧的 detail,
  几周前早已修好的问题还在给相似任务注入"类似任务之前失败过… 避免: …", 把模型推去绕无谓的弯路。
  现在 find_similar 忽略 max_age_days (默认 7 天) 之前的条目; 去重命中时用新 detail/ts 覆盖旧条目;
  record_turn 判定回合成功时调 resolve_similar 删掉相似失败。

不依赖 LLM, 全部规则匹配 (substring + jaccard).
"""
import json
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Optional

MAX_AGE_DAYS = 7.0   # [2026-10-19] 超过这个天数的失败记录不再召回


def _read_entries(p: Path) -> List[Dict]:
    out: List[Dict] = []
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _write_entries(p: Path, entries: List[Dict]) -> None:
    text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    try:
        from lib.atomic_io import atomic_write_text
        atomic_write_text(p, text)
    except ImportError:
        p.write_text(text, encoding="utf-8")


def _path(workspace) -> Path:
    return Path(workspace) / "telemetry" / "failures.jsonl"


def _sig(error_type: str, summary: str) -> str:
    """指纹: 错误类型 + summary 头 100 字 -> sha256[:12]"""
    h = hashlib.sha256()
    h.update(error_type.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update(summary[:100].encode("utf-8", errors="replace"))
    return h.hexdigest()[:12]


def record_failure(workspace, error_type: str, summary: str, fix_hint: str = "",
                   extra: Optional[Dict] = None) -> bool:
    """记一条失败. 返回 True = 新记录, False = 已存在 (按 signature dedup).
    extra: 附加字段 (不覆盖内置字段, 不参与指纹), 值统一转 str 截 300 字。"""
    try:
        p = _path(workspace)
        p.parent.mkdir(parents=True, exist_ok=True)
        sig = _sig(error_type, summary)
        entry = {
            "ts": time.time(),
            "signature": sig,
            "error_type": error_type[:40],
            "summary": summary[:400],
            "fix_hint": fix_hint[:300],
        }
        for k, v in (extra or {}).items():
            if k not in entry:
                entry[k] = str(v)[:300]
        # 查 dedup. [2026-10-19] 命中时用最新的 detail/ts 覆盖 (原来保留最旧那条, 过时报错永远留着)
        if p.exists():
            try:
                entries = _read_entries(p)
            except Exception:
                entries = []
            for i, obj in enumerate(entries):
                if obj.get("signature") == sig:
                    entry["count"] = int(obj.get("count", 1) or 1) + 1
                    entries[i] = entry
                    try:
                        _write_entries(p, entries)
                    except Exception:
                        pass
                    return False
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


def find_similar(workspace, error_type: str, query: str, limit: int = 3,
                 max_age_days: Optional[float] = MAX_AGE_DAYS) -> List[Dict]:
    """查找最相关的过往失败. error_type 必须匹配 (空串=不限). query 用 jaccard 排序.
    max_age_days: 早于该天数的条目忽略 (None/<=0 = 不限)."""
    p = _path(workspace)
    if not p.exists():
        return []
    cands: List[Dict] = []
    cutoff = time.time() - float(max_age_days) * 86400 if max_age_days and max_age_days > 0 else None
    try:
        for obj in _read_entries(p):
            if cutoff is not None:
                try:
                    if float(obj.get("ts", 0) or 0) < cutoff:
                        continue
                except (TypeError, ValueError):
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


def resolve_similar(workspace, query: str, threshold: float = 0.6) -> int:
    """[2026-10-19] 相似任务后来成功了 → 删掉 summary 与 query 相似度 ≥ threshold 的失败记录.
    返回删除条数. 任何异常返回 0."""
    try:
        p = _path(workspace)
        if not p.exists() or not query:
            return 0
        entries = _read_entries(p)
        keep = [e for e in entries if _jaccard(query, e.get("summary", "")) < threshold]
        n = len(entries) - len(keep)
        if n:
            _write_entries(p, keep)
        return n
    except Exception:
        return 0


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
