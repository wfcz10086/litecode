"""
session_fork.py — 会话分支 fork (P23)
=======================================
从已有 session 的某个 turn 切一个新 session, 用于"我想从这里走另一条路".

API 设计:
  fork_session(workspace, src_sid, until_turn=None) -> new_sid
    - 复制 sessions/{src_sid}/ 整个目录到 sessions/{new_sid}/
    - history.json 截到 until_turn (None=全部复制)
    - meta 加 forked_from / forked_at / forked_at_turn

不动原 session.
"""
import json
import time
import uuid
import shutil
from pathlib import Path
from typing import Optional


def _new_sid() -> str:
    return f"fork_{uuid.uuid4().hex[:12]}"


def fork_session(workspace, src_sid: str, until_turn: Optional[int] = None,
                 sessions_dir: Optional[Path] = None) -> Optional[str]:
    """从 src_sid 切出新 session, 返回新 sid (或 None 失败).
    
    until_turn: 截到第 N 个 turn (含). None 表示全复制.
                turn 用 history.json 里 user/assistant 配对计数.
    """
    base = Path(sessions_dir) if sessions_dir else Path(workspace) / "sessions"
    src = base / src_sid
    if not src.exists() or not src.is_dir():
        return None
    
    new_sid = _new_sid()
    dst = base / new_sid
    try:
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".cache", "wechat_creds"
        ))
    except Exception:
        return None
    
    # 截 history.json
    if until_turn is not None and until_turn >= 0:
        hist = dst / "history.json"
        if hist.exists():
            try:
                msgs = json.loads(hist.read_text(errors="replace"))
                if isinstance(msgs, list):
                    # 数 turn: 每个 user message 算 1 turn
                    kept = []
                    turn_count = 0
                    for m in msgs:
                        kept.append(m)
                        if isinstance(m, dict) and m.get("role") == "user":
                            turn_count += 1
                            if turn_count > until_turn:
                                kept.pop()  # 当前这条不要
                                break
                    hist.write_text(json.dumps(kept, ensure_ascii=False, indent=2))
            except Exception:
                pass
    
    # 写 fork 元数据
    meta = {
        "forked_from": src_sid,
        "forked_at": time.time(),
        "forked_at_turn": until_turn,
    }
    try:
        (dst / "fork_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception:
        pass
    
    return new_sid


def get_fork_info(workspace, sid: str, sessions_dir: Optional[Path] = None) -> Optional[dict]:
    """读 fork_meta.json"""
    base = Path(sessions_dir) if sessions_dir else Path(workspace) / "sessions"
    fp = base / sid / "fork_meta.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(errors="replace"))
    except Exception:
        return None
