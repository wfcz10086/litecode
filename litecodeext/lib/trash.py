"""lib/trash.py — 消息编辑/重试回收站 (P0-#17).

truncate/edit 前 snapshot dropped 消息到 ~/.litecode/trash/<sid>-<ts>.json,
TTL 24h. 前端可撤销 toast, 也可批量清理.
文件结构::

    { "sid": ..., "ts": ..., "expires": ts+86400,
      "kept": int,             # truncate 保留数
      "dropped_msgs": [...],   # 被裁剪掉的消息 (完整对象)
      "reason": "truncate|edit|retry" }
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import List, Optional

_TRASH_DIR = Path.home() / ".litecode" / "trash"
_TTL_SEC = 24 * 3600


def _ensure():
    _TRASH_DIR.mkdir(parents=True, exist_ok=True)


def _fname(sid: str, ts: float) -> Path:
    return _TRASH_DIR / f"{sid}-{int(ts * 1000)}.json"


def snapshot(sid: str, kept: int, dropped_msgs: list,
             reason: str = "truncate") -> Optional[dict]:
    """写一份回收记录, 返回 {trash_id, path, expires}."""
    if not dropped_msgs:
        return None
    _ensure()
    ts = time.time()
    fp = _fname(sid, ts)
    rec = {
        "sid": sid,
        "ts": ts,
        "expires": ts + _TTL_SEC,
        "kept": int(kept),
        "dropped_msgs": dropped_msgs,
        "reason": reason,
    }
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    tmp.replace(fp)
    return {"trash_id": fp.stem, "path": str(fp), "expires": rec["expires"]}


def _all_files() -> List[Path]:
    _ensure()
    return sorted(_TRASH_DIR.glob("*.json"))


def purge_expired() -> int:
    """删所有过期条目, 返回删除数."""
    now = time.time()
    n = 0
    for fp in _all_files():
        try:
            rec = json.loads(fp.read_text())
            if rec.get("expires", 0) <= now:
                fp.unlink(); n += 1
        except Exception:
            try: fp.unlink(); n += 1
            except Exception: pass
    return n


def list_trash(sid: Optional[str] = None) -> list:
    purge_expired()
    out = []
    for fp in _all_files():
        try:
            rec = json.loads(fp.read_text())
        except Exception:
            continue
        if sid and rec.get("sid") != sid:
            continue
        out.append({
            "trash_id": fp.stem,
            "sid": rec.get("sid"),
            "ts": rec.get("ts"),
            "expires": rec.get("expires"),
            "kept": rec.get("kept"),
            "dropped_count": len(rec.get("dropped_msgs", [])),
            "reason": rec.get("reason", ""),
        })
    return out


def load_trash(trash_id: str) -> Optional[dict]:
    fp = _TRASH_DIR / f"{trash_id}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text())
    except Exception:
        return None


def delete_trash(trash_id: str) -> bool:
    fp = _TRASH_DIR / f"{trash_id}.json"
    if fp.exists():
        try:
            fp.unlink(); return True
        except Exception:
            return False
    return False
