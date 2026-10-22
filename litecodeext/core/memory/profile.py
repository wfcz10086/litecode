"""memory/profile.py — 全局用户档案: 跨会话共享的用户偏好/身份事实 + 全局 USER.md。

为什么有它: 以前 save_memory / update_profile 只写 sessions/<sid>/ 下的文件, 新会话从模板
复制 USER.md, 用户说"记住我最喜欢山竹"换个会话就忘了, 模型却说"已写入长期偏好"。

存储 (每个档案一个目录):
  <workspace>/profile/<profile_id>/facts.json   结构化事实 (ChatGPT "已保存的记忆" / mem0 memories)
  <workspace>/profile/<profile_id>/USER.md      自由文本档案 (update_profile file=USER.md 写这里)
profile_id: Web / CLI / DAG / 定时任务 = "owner"; 微信/企微每个联系人单独一份
(别人在微信里说的话不能写进主人的档案)。

事实的写入语义 (upsert):
  - 同 key (归一后) → 新值替换旧值, 旧值进 history (Zep 式失效而非删除, 便于追溯);
  - 无 key: 规范化后整句相同 → unchanged; 两字切词 Jaccard ≥ 0.6 → 视为改写, 替换;
  - 带 "[来源=外部内容 …]" 标记 (lib/agent/untrusted.py 打的) → 拒绝写入全局。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from . import facts as _facts
from .iohelp import _atomic_write
from .recall import tokens as _tokens

try:
    import fcntl as _fcntl          # Linux/macOS: web_ui 与 server 两个进程可能同时写
except ImportError:                 # Windows 桌面版: 单进程, 线程锁足够
    _fcntl = None

PROFILE_DIRNAME = "profile"
OWNER = "owner"
UNTRUSTED_TAG = "[来源=外部内容"           # 与 lib/agent/untrusted.MEMORY_TAG 前缀一致
_ISOLATED = re.compile(r"^(wx|wecom|wc)[-_]")
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
MAX_HISTORY = 5


def profile_id_for(session_id: Optional[str]) -> str:
    """微信/企微会话按联系人隔离 (会话 id 本身就按 账号+联系人 生成); 其余都是主人。"""
    sid = str(session_id or "")
    if _ISOLATED.match(sid):
        return re.sub(r"[^A-Za-z0-9_\-]", "", sid)[:80] or OWNER
    return OWNER


def profile_dir(workspace: Path, session_id: Optional[str] = None) -> Path:
    return Path(workspace) / PROFILE_DIRNAME / profile_id_for(session_id)


def _now() -> float:
    return time.time()


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(_tokens(a)), set(_tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class ProfileStore:
    def __init__(self, workspace: Path, profile_id: str = OWNER):
        self.profile_id = profile_id
        self.root = Path(workspace) / PROFILE_DIRNAME / profile_id
        self.facts_file = self.root / "facts.json"
        self.user_md = self.root / "USER.md"
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.root), threading.Lock())

    @classmethod
    def for_session(cls, workspace: Path, session_id: Optional[str]) -> "ProfileStore":
        return cls(workspace, profile_id_for(session_id))

    # ── 读写 ────────────────────────────────────────────────
    @contextmanager
    def _locked(self):
        with self._lock:
            fh = None
            if _fcntl is not None:
                try:
                    self.root.mkdir(parents=True, exist_ok=True)
                    fh = open(self.root / ".lock", "w")
                    _fcntl.flock(fh, _fcntl.LOCK_EX)
                except OSError:
                    fh = None
            try:
                yield
            finally:
                if fh is not None:
                    try:
                        _fcntl.flock(fh, _fcntl.LOCK_UN)
                    finally:
                        fh.close()

    def _load(self) -> list[dict]:
        if not self.facts_file.exists():
            return []
        try:
            data = json.loads(self.facts_file.read_text(errors="replace"))
            return [f for f in data.get("facts", []) if isinstance(f, dict) and f.get("text")]
        except (ValueError, OSError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.facts_file, json.dumps(
            {"version": 1, "profile_id": self.profile_id, "facts": items}, ensure_ascii=False, indent=1))

    def list(self) -> list[dict]:
        return self._load()

    def get(self, fid: str) -> Optional[dict]:
        return next((f for f in self._load() if f.get("id") == fid), None)

    # ── 写入: 抽取 + 去重 + 冲突更新 ─────────────────────────
    def upsert(self, text: str, key: Optional[str] = None, *, source: str = "tool", session_id: str = "",
               importance: Optional[int] = None) -> tuple[str, Optional[dict], Optional[str]]:
        """返回 (动作, 事实, 被替换的旧文本)。动作 ∈ added / updated / unchanged / rejected。"""
        raw = " ".join((text or "").split())
        if not raw:
            return "rejected", None, None
        if UNTRUSTED_TAG in raw:
            return "rejected", None, None
        fact = _facts.extract(raw)
        if key:
            k = _facts.norm_key(key)
            value = fact.value if fact.key and _facts.norm_key(fact.key) == k else _facts.strip_remember(raw)[0]
            value = re.sub(rf"^{re.escape(str(key).strip())}\s*[:：=]\s*", "", value).strip() or value
            fact = _facts.Fact(k, value, f"{k}: {value}", "explicit_key", fact.changed, fact.explicit)
        imp = importance if importance is not None else (3 if fact.key or fact.explicit else 2)
        with self._locked():
            items = self._load()
            target, why = self._find_conflict(items, fact)
            if target is not None:
                if target["text"] == fact.text:
                    target["last_seen"] = _now()
                    self._save(items)
                    return "unchanged", target, None
                old = target["text"]
                hist = (target.get("history") or []) + [{"text": old, "until": _now(), "why": why}]
                target.update(text=fact.text, value=fact.value, key=fact.key or target.get("key"),
                              updated=_now(), source=source, session_id=session_id,
                              importance=max(int(target.get("importance", 1)), imp),
                              history=hist[-MAX_HISTORY:])
                self._save(items)
                return "updated", target, old
            new = {"id": uuid.uuid4().hex[:10], "key": fact.key, "value": fact.value, "text": fact.text,
                   "source": source, "session_id": session_id, "importance": imp,
                   "created": _now(), "updated": _now(), "hits": 0, "history": []}
            items.append(new)
            self._save(items)
            return "added", new, None

    @staticmethod
    def _find_conflict(items: list[dict], fact: "_facts.Fact") -> tuple[Optional[dict], str]:
        if fact.key:
            nk = _facts.norm_key(fact.key)
            for it in items:
                if it.get("key") and _facts.norm_key(it["key"]) == nk:
                    return it, "same_key"
        norm = re.sub(r"\s+", "", fact.text)
        for it in items:
            if re.sub(r"\s+", "", it.get("text", "")) == norm:
                return it, "same_text"
        if not fact.key:
            best = max(((it, _jaccard(it.get("text", ""), fact.text)) for it in items if not it.get("key")),
                       key=lambda x: x[1], default=(None, 0.0))
            if best[0] is not None and best[1] >= 0.6:
                return best[0], "rephrase"
        return None, ""

    def forget(self, query: str = "", fid: str = "", key: str = "") -> list[dict]:
        """按 id / key / 文本删除 (真删, 不留痕 —— 用户要求遗忘就该忘)。返回被删的事实。"""
        with self._locked():
            items = self._load()
            if fid:
                gone = [f for f in items if f.get("id") == fid]
            elif key:
                nk = _facts.norm_key(key)
                gone = [f for f in items if f.get("key") and _facts.norm_key(f["key"]) == nk]
            else:
                q = (query or "").strip()
                if not q:
                    return []
                gone = [f for f in items if q in f.get("text", "") or q == f.get("value")]
                if not gone:
                    scored = self._rank(q, items)
                    gone = [scored[0][1]] if scored and scored[0][0] >= 1.0 else []
            if gone:
                ids = {f["id"] for f in gone}
                self._save([f for f in items if f.get("id") not in ids])
            return gone

    def correct(self, fid: str, text: str) -> Optional[dict]:
        """用户在面板里改一条: 保留 id, 旧文本进 history。"""
        text = " ".join((text or "").split())
        if not text or UNTRUSTED_TAG in text:
            return None
        with self._locked():
            items = self._load()
            for it in items:
                if it.get("id") == fid:
                    f = _facts.extract(text)
                    hist = (it.get("history") or []) + [{"text": it["text"], "until": _now(), "why": "user_edit"}]
                    it.update(text=f.text if f.key else text, value=f.value, key=f.key or it.get("key"),
                              updated=_now(), source="user_edit", importance=3, history=hist[-MAX_HISTORY:])
                    self._save(items)
                    return it
        return None

    # ── 检索: BM25 × 重要度 × 时效 ───────────────────────────
    @staticmethod
    def _rank(query: str, items: list[dict], half_life_days: float = 180.0) -> list[tuple[float, dict]]:
        from .recall import Entry, bm25
        if not items:
            return []
        ents = [Entry(it.get("key") or "", it.get("text", ""), "profile") for it in items]
        base = bm25(query, ents)
        out = []
        for s, it in zip(base, items):
            if s <= 0:
                continue
            age = max(0.0, (_now() - float(it.get("updated") or it.get("created") or _now())) / 86400)
            decay = 0.5 ** (age / half_life_days)
            out.append((s * (1 + 0.15 * int(it.get("importance", 1))) * (0.6 + 0.4 * decay), it))
        out.sort(key=lambda x: -x[0])
        return out

    def search(self, query: str, k: int = 5) -> list[dict]:
        return [it for _, it in self._rank(query, self._load())[:k]]

    # ── 注入 ────────────────────────────────────────────────
    def split_budget(self, max_chars: int = 1200) -> tuple[list[dict], list[dict]]:
        """重要度高、最近更新的先进常驻块; 超预算的留给按相关度召回。"""
        items = sorted(self._load(), key=lambda f: (-int(f.get("importance", 1)), -float(f.get("updated", 0))))
        pinned, rest, used = [], [], 0
        for it in items:
            n = len(it["text"]) + 3
            if used + n <= max_chars:
                pinned.append(it)
                used += n
            else:
                rest.append(it)
        return pinned, rest


def render_block(pinned: list[dict], overflow: int = 0) -> str:
    if not pinned:
        return ""
    body = "\n".join(f"- {f['text']}" for f in pinned)
    digest = hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()[:8]
    tail = f"\n(另有 {overflow} 条, 与话题相关时自动附上)" if overflow else ""
    return (f"## 用户长期档案 [跨会话 v{digest}]\n"
            "以下是用户让你记住的偏好与个人信息 (所有会话共享)。与用户本次的说法冲突时以本次为准, "
            "并用 save_memory 更新; 用户可在「记忆」面板查看和删除。\n" + body + tail)


def turn_block(workspace: Path, session_id: Optional[str], query: str, seen: str = "",
               max_chars: int = 1200, k: int = 3) -> str:
    """每轮附加给模型的档案上下文: 常驻块内容变了才重给 (首行带指纹); 超预算的按相关度挑。"""
    store = ProfileStore.for_session(workspace, session_id)
    pinned, rest = store.split_budget(max_chars)
    parts = []
    block = render_block(pinned, len(rest))
    if block and block.split("\n", 1)[0] not in seen:
        parts.append(block)
    if rest and query:
        picked = [it for _, it in store._rank(query, rest)[:k] if it["text"] not in seen]
        if picked:
            parts.append("## 相关的用户档案条目\n" + "\n".join(f"- {f['text']}" for f in picked))
    return "\n\n".join(parts)


# ── 与跨会话检索索引 (core/memory_index.py, L3) 同步 ──────────────
def _index(workspace: Path):
    try:
        from memory_index import MemoryIndex          # 容器 / 服务进程
    except ImportError:
        from core.memory_index import MemoryIndex     # 宿主机测试
    return MemoryIndex(Path(workspace) / ".memory_index.db")


def sync_index(workspace: Path, action: str, fact: Optional[dict], old_text: Optional[str] = None,
               session_id: str = "") -> None:
    """档案变了 → L3 里的旧值删掉、新值以 category=profile 入库; 失败不影响档案本身。"""
    if not fact or action not in ("added", "updated", "forgotten"):
        return
    try:
        idx = _index(workspace)
        olds = [old_text] if old_text else []
        if action == "forgotten":
            olds.append(fact.get("text", ""))
        for t in olds:
            idx.delete_containing(t, ("profile",))
            val = t.split(": ", 1)[-1].strip()
            if len(val) >= 2:
                idx.delete_containing(val, ("preference", "user_fact"))
        if action != "forgotten":
            idx.index_memory(session_id or "profile", "profile", fact["text"], tags="profile user-preference")
    except Exception:
        pass


# ── 全局 USER.md ───────────────────────────────────────────
_BULLET_KV = re.compile(r"^\s*[-*]\s+(?:\*\*)?(?P<k>[^:：=*\n]{1,30}?)(?:\*\*)?\s*(?:\*\*)?[:：=](?:\*\*)?\s*(?P<v>.+)$")


def merge_user_md(existing: str, new_content: str, merge_fn) -> str:
    """先做同 key 覆盖 ("- 最喜欢的水果: 榴莲" 替换已有的 "- 最喜欢的水果: 山竹"), 剩余内容交给
    原来的按段合并 (merge_fn = core/tools/md_merge._merge_md_sections)。"""
    lines = existing.splitlines()
    index = {}
    for i, ln in enumerate(lines):
        m = _BULLET_KV.match(ln)
        if m:
            index.setdefault(_facts.norm_key(m.group("k")), i)
    leftover = []
    for ln in new_content.splitlines():
        m = _BULLET_KV.match(ln)
        if m and (i := index.get(_facts.norm_key(m.group("k")))) is not None:
            lines[i] = ln
            continue
        leftover.append(ln)
    merged = "\n".join(lines) + ("\n" if existing.endswith("\n") else "")
    rest = "\n".join(leftover).strip()
    if not rest or all(ln.lstrip().startswith("#") for ln in rest.splitlines() if ln.strip()):
        return merged
    return merge_fn(merged, rest)


def resolve_user_md(workspace: Path, session_id: Optional[str], fallbacks: list) -> Optional[Path]:
    """USER.md 读取顺序: 全局档案 > 会话副本 (老数据) > 模板。"""
    g = profile_dir(workspace, session_id) / "USER.md"
    if g.exists():
        return g
    for d in fallbacks:
        if d and (Path(d) / "USER.md").exists():
            return Path(d) / "USER.md"
    return None


def as_dict(f: dict) -> dict:
    """API 输出: 加可读时间。"""
    out = dict(f)
    for k in ("created", "updated"):
        if out.get(k):
            out[k + "_str"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(out[k])))
    return out


__all__ = ["ProfileStore", "profile_id_for", "profile_dir", "turn_block", "render_block",
           "merge_user_md", "resolve_user_md", "as_dict", "sync_index", "OWNER"]
