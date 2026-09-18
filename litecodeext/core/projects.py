"""
projects.py — 项目数据模型 (P30-a)
====================================
跨 session 的项目层. 与 sessions 平行, 用于支持长周期任务
(代码/小说/报告/研究等).

- 每个项目: workspace/projects/{project_id}/
- meta.json: 项目元数据
- 后续 P30-b/c/d 在此基础上加 API + Web UI + 共享上下文
"""
from __future__ import annotations
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional



# Project type templates (P30-e)
_TEMPLATES = {
    "novel": {
        "outline.md": "# 大纲\n\n## 主线\n\n## 章节\n- 第 1 章 \n",
        "characters.md": "# 人物\n\n## 主角\n- 姓名: \n- 性格: \n- 目标: \n",
        "progress.md": "# 进度\n\n## 已写\n\n## 待写\n\n## 卡点\n",
    },
    "code": {
        "PROJECT_MAP.md": "# Project Map\n\n## 文件结构\n\n## 模块说明\n",
        "DECISIONS.md": "# 架构决策\n\n- [日期] 决策: \n  原因: \n",
        "CONVENTIONS.md": "# 代码约定\n\n## 命名\n\n## 风格\n\n## 测试\n",
        "TERMS.md": "# 术语表\n\n- 术语: 含义\n",
    },
    "report": {
        "sources.md": "# 来源\n\n- \n",
        "outline.md": "# 提纲\n\n## 摘要\n\n## 正文\n\n## 结论\n",
        "draft.md": "# 草稿\n\n",
    },
    "research": {
        "questions.md": "# 研究问题\n\n- \n",
        "hypotheses.md": "# 假设\n\n- \n",
        "evidence.md": "# 证据\n\n- \n",
    },
    "general": {
        "notes.md": "# 笔记\n\n",
    },
}


def _seed_templates(pdir, project_type: str) -> int:
    """根据 project_type 在 pdir 写模板 .md, 返回写入文件数"""
    tpl = _TEMPLATES.get(project_type) or _TEMPLATES.get("general") or {}
    n = 0
    for name, content in tpl.items():
        fp = pdir / name
        if not fp.exists():
            fp.write_text(content)
            n += 1
    return n


_TYPE_PREFIX = {
    "code": "code",
    "novel": "nov",
    "report": "rpt",
    "research": "res",
    "general": "gen",
}


def _gen_project_id(project_type: str) -> str:
    prefix = _TYPE_PREFIX.get(project_type, "gen")
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class ProjectMeta:
    project_id: str
    name: str
    project_type: str = "general"
    description: str = ""
    created: float = 0.0
    last_active: float = 0.0
    status: str = "active"  # active|paused|archived|completed
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectMeta":
        return cls(
            project_id=d.get("project_id", ""),
            name=d.get("name", ""),
            project_type=d.get("project_type", "general"),
            description=d.get("description", ""),
            created=float(d.get("created", 0.0)),
            last_active=float(d.get("last_active", 0.0)),
            status=d.get("status", "active"),
            tags=list(d.get("tags") or []),
        )


class ProjectStore:
    """文件系统支持的项目存储."""

    def __init__(self, workspace_base: Path):
        self.root = Path(workspace_base) / "projects"
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        return self.root / project_id

    def _meta_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "meta.json"

    def _write_meta(self, meta: ProjectMeta) -> None:
        d = self.project_dir(meta.project_id)
        d.mkdir(parents=True, exist_ok=True)
        p = self._meta_path(meta.project_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta.to_dict(), ensure_ascii=False, indent=2))
        tmp.replace(p)

    def create(self, name: str, project_type: str = "general",
               description: str = "", tags: Optional[List[str]] = None) -> ProjectMeta:
        now = time.time()
        meta = ProjectMeta(
            project_id=_gen_project_id(project_type),
            name=name,
            project_type=project_type,
            description=description,
            created=now,
            last_active=now,
            status="active",
            tags=list(tags or []),
        )
        self._write_meta(meta)
        try:
            _seed_templates(self.project_dir(meta.project_id), project_type)
        except Exception:
            pass
        return meta

    def get(self, project_id: str) -> Optional[ProjectMeta]:
        p = self._meta_path(project_id)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(errors="replace"))
            return ProjectMeta.from_dict(d)
        except Exception:
            return None

    def list_all(self, status: str = "") -> List[ProjectMeta]:
        results: List[ProjectMeta] = []
        for meta_p in sorted(self.root.glob("*/meta.json")):
            try:
                d = json.loads(meta_p.read_text(errors="replace"))
                m = ProjectMeta.from_dict(d)
                if status and m.status != status:
                    continue
                results.append(m)
            except Exception:
                continue
        return results

    def update(self, project_id: str, **fields) -> Optional[ProjectMeta]:
        meta = self.get(project_id)
        if not meta:
            return None
        for k, v in fields.items():
            if hasattr(meta, k):
                setattr(meta, k, v)
        meta.last_active = time.time()
        self._write_meta(meta)
        return meta

    def delete(self, project_id: str, soft: bool = True) -> bool:
        if soft:
            return self.update(project_id, status="archived") is not None
        d = self.project_dir(project_id)
        if not d.exists():
            return False
        import shutil as _sh
        _sh.rmtree(d, ignore_errors=True)
        return not d.exists()

# Git root identification (P30-f): 代码类项目用 git 根 SHA 做稳定 ID
def _git_root_id(path) -> str:
    """如果 path 在 git 仓库内, 返回 'code_<git_root_sha8>' 作为稳定 project_id.
    否则返回空串.
    """
    import subprocess, hashlib
    from pathlib import Path as _P
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode != 0:
            return ""
        root = r.stdout.strip()
        if not root:
            return ""
        h = hashlib.sha256(root.encode()).hexdigest()[:8]
        return f"code_{h}"
    except Exception:
        return ""


def find_or_create_for_path(store, path, name: str = "", project_type: str = "code"):
    """P30-f: 给定一个本地路径, 找到或创建对应项目.
    - 路径在 git 仓库内: 用 git 根 SHA 作 project_id, 同根多次调用返回同一项目
    - 否则: 按 name 新建
    """
    git_id = _git_root_id(path) if project_type == "code" else ""
    if git_id:
        existing = store.get(git_id)
        if existing:
            return existing
        # 直接用稳定 ID 创建
        from pathlib import Path as _P
        d = store.project_dir(git_id)
        d.mkdir(parents=True, exist_ok=True)
        import time as _t
        meta = ProjectMeta(
            project_id=git_id,
            name=name or _P(path).name,
            project_type="code",
            description=f"git: {path}",
            created=_t.time(),
            last_active=_t.time(),
        )
        store._write_meta(meta)
        try:
            _seed_templates(d, "code")
        except Exception:
            pass
        return meta
    return store.create(name=name or "auto", project_type=project_type)

def load_project_memory(store, project_id: str, max_chars: int = 4000) -> str:
    """P19: 加载项目级共享记忆 (PROJECT_MAP / DECISIONS / CONVENTIONS / TERMS).
    供 lib/prompt.py 在构建 system prompt 时注入.
    返回 markdown 字符串, 项目无此文件时返回空.
    """
    if not project_id:
        return ""
    pdir = store.project_dir(project_id)
    if not pdir.exists():
        return ""
    parts = []
    total = 0
    for fname in ("PROJECT_MAP.md", "DECISIONS.md", "CONVENTIONS.md", "TERMS.md"):
        fp = pdir / fname
        if not fp.exists():
            continue
        try:
            txt = fp.read_text(errors="replace").strip()
        except Exception:
            continue
        if not txt:
            continue
        block = f"## [Project {fname}]\n{txt}"
        if total + len(block) > max_chars:
            block = block[: max(0, max_chars - total)] + "\n... (truncated)"
            parts.append(block)
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts) if parts else ""

