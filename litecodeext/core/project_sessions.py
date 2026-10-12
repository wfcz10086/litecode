"""project_sessions.py — 会话 ↔ 项目绑定, 以及注入给模型的项目上下文块。

绑定关系存在各项目目录的 sessions.txt (每行一个 session id), 一个会话最多属于一个项目。
项目给模型看的内容:
  - INSTRUCTIONS.md      项目指令 (用户在界面里写的"这个项目里你要怎么做")
  - meta.workspace       绑定的工作目录
  - load_project_memory  PROJECT_MAP / DECISIONS / CONVENTIONS / TERMS 等项目文档
"""
from __future__ import annotations

import hashlib
from typing import Optional

try:
    from projects import ProjectMeta, ProjectStore, load_project_memory   # 服务进程: core 在 sys.path
except ImportError:
    from core.projects import ProjectMeta, ProjectStore, load_project_memory

SESSIONS_FILE = "sessions.txt"
INSTRUCTIONS_FILE = "INSTRUCTIONS.md"
INSTRUCTIONS_MAX = 4000


def sessions_of(store: ProjectStore, pid: str) -> list[str]:
    sf = store.project_dir(pid) / SESSIONS_FILE
    if not sf.exists():
        return []
    return [s for s in sf.read_text(errors="replace").splitlines() if s.strip()]


def _write_sessions(store: ProjectStore, pid: str, sids: list[str]) -> None:
    sf = store.project_dir(pid) / SESSIONS_FILE
    tmp = sf.with_suffix(".tmp")
    tmp.write_text("".join(s + "\n" for s in sids))
    tmp.replace(sf)


def session_map(store: ProjectStore) -> dict[str, str]:
    """{session_id: project_id}, 只算未删除 (非 archived) 的项目。"""
    out: dict[str, str] = {}
    for m in store.list_all():
        if m.status == "archived":
            continue
        for sid in sessions_of(store, m.project_id):
            out.setdefault(sid, m.project_id)
    return out


def project_of(store: ProjectStore, sid: str) -> Optional[ProjectMeta]:
    pid = session_map(store).get(sid)
    return store.get(pid) if pid else None


def unbind(store: ProjectStore, sid: str) -> bool:
    changed = False
    for m in store.list_all():
        sids = sessions_of(store, m.project_id)
        if sid in sids:
            _write_sessions(store, m.project_id, [s for s in sids if s != sid])
            changed = True
    return changed


def bind(store: ProjectStore, pid: str, sid: str) -> int:
    """把会话挪进项目 (先从别的项目里摘掉)。返回该项目的会话数。"""
    if not store.get(pid):
        raise KeyError(pid)
    unbind(store, sid)
    sids = sessions_of(store, pid) + [sid]
    _write_sessions(store, pid, sids)
    return len(sids)


def read_instructions(store: ProjectStore, pid: str) -> str:
    fp = store.project_dir(pid) / INSTRUCTIONS_FILE
    return fp.read_text(errors="replace") if fp.exists() else ""


def write_instructions(store: ProjectStore, pid: str, text: str) -> None:
    (store.project_dir(pid) / INSTRUCTIONS_FILE).write_text(text[:INSTRUCTIONS_MAX * 2])


def context_block(store: ProjectStore, meta: ProjectMeta) -> str:
    """给模型的项目块。首行带内容指纹: 内容不变时历史里已有同一行, 调用方据此不重复注入。"""
    parts = []
    ins = read_instructions(store, meta.project_id).strip()
    if ins:
        parts.append("### 项目指令 (用户为这个项目定的要求, 优先遵守)\n" + ins[:INSTRUCTIONS_MAX])
    ws = (getattr(meta, "workspace", "") or "").strip()
    if ws:
        parts.append(f"### 工作目录\n本项目的文件在 `{ws}`, 读写项目文件默认在这个目录下进行。")
    mem = load_project_memory(store, meta.project_id)
    if mem:
        parts.append(mem)
    if not parts:
        return ""
    body = "\n\n".join(parts)
    tag = hashlib.sha256((meta.name + "\n" + body).encode()).hexdigest()[:8]
    return f"[Project: {meta.name} · {tag}]\n本会话属于项目「{meta.name}」。\n\n{body}"
