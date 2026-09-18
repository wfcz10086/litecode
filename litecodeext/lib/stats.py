"""lib/stats.py — Token 统计 + 用户提问历史（线程安全）"""
import json, time, threading, datetime
from lib.config import WORKSPACE

_STATS_FILE = WORKSPACE / "token_stats.json"
_STATS_LOCK = threading.Lock()

def stats_load() -> dict:
    try:
        if _STATS_FILE.exists():
            return json.loads(_STATS_FILE.read_text())
    except Exception:
        pass
    return {"total_prompt": 0, "total_completion": 0, "total_calls": 0,
            "sessions": {}, "by_day": {}, "created_at": time.time()}

def stats_save(s: dict):
    try:
        _STATS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    except Exception:
        pass

def stats_add(session_id: str, prompt_tokens: int, completion_tokens: int):
    day = datetime.datetime.now().strftime("%Y-%m-%d")
    with _STATS_LOCK:
        s = stats_load()
        s["total_prompt"]     += prompt_tokens
        s["total_completion"] += completion_tokens
        s["total_calls"]      += 1
        s["last_updated"]      = time.time()
        if session_id not in s["sessions"]:
            s["sessions"][session_id] = {"prompt": 0, "completion": 0, "calls": 0}
        ss = s["sessions"][session_id]
        ss["prompt"] += prompt_tokens; ss["completion"] += completion_tokens; ss["calls"] += 1
        if day not in s["by_day"]:
            s["by_day"][day] = {"prompt": 0, "completion": 0, "calls": 0}
        sd = s["by_day"][day]
        sd["prompt"] += prompt_tokens; sd["completion"] += completion_tokens; sd["calls"] += 1
        stats_save(s)

# ── Question history ─────────────────────────────────────────
_Q_FILE = WORKSPACE / "user_questions.json"
_Q_MAX  = 1000
_Q_LOCK = threading.Lock()

def questions_load() -> list:
    try:
        if _Q_FILE.exists():
            return json.loads(_Q_FILE.read_text())
    except Exception:
        pass
    return []

def questions_append(session_id: str, question: str):
    if not question or not question.strip():
        return
    q = question.strip()
    if q.startswith("[SYSTEM") or q.startswith("[system"):
        return
    entry = {
        "ts":      time.time(),
        "time":    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session": session_id or "anon",
        "q":       q[:2000],
    }
    with _Q_LOCK:
        qs = questions_load()
        qs.append(entry)
        if len(qs) > _Q_MAX:
            qs = qs[-_Q_MAX:]
        try:
            _Q_FILE.write_text(json.dumps(qs, ensure_ascii=False, indent=2))
        except Exception:
            pass
