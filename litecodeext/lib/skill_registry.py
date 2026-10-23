"""lib/skill_registry.py — 技能注册表: 内置 + 已安装 + 草稿的合并视图, 启停与使用统计。

目录 (个人版, 单用户):
  内置   = SKILLS_DIR (代码包里的 skills/, 桌面版升级时整体替换)
  已安装 = WORKSPACE/skills/<name>/SKILL.md (市场/zip/git 安装, 自建技能, 草稿启用后都在这里; 升级不丢)
  草稿   = WORKSPACE/skills/_drafts/<name>/ (兼容旧位置 SKILLS_DIR/drafts/)
  备份   = WORKSPACE/skills/_backup/<name>-<时间>/ (已安装技能被升级覆盖前的旧版)
状态:
  WORKSPACE/skills_state.json  {"disabled": [...], "meta": {name: {source, origin_url, version, installed_at}}}
  WORKSPACE/skills_usage.json  {name: {"count": n, "last": ts}}  (单独存: 统计每轮都写, 别让索引跟着重建)
  旧统计 SKILLS_DIR/_usage.json 只读合并, test-/zz- 前缀条目 (测试残留) 展示时滤掉。
测试: 环境变量 LITECODE_SKILLS_HOME / LITECODE_SKILLS_STATE 指到临时目录 (tests/conftest.py)。

停用的技能不进索引 (lib/skills.py 经 enabled_files() 建索引), 因此 list_skills / load_skill /
自动匹配 / 强制注入都看不到它。web_ui 与 litecode_server 是两个进程, 服务端靠
index_signature() (状态文件与目录的 mtime) 发现变化后重建索引。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .atomic_io import atomic_write_json

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TEST_PREFIX = ("test-", "zz-")
_LOCK = threading.Lock()

try:
    import fcntl as _fcntl
except ImportError:          # 非 Unix: 只有进程内锁
    _fcntl = None


# ── 路径 ──────────────────────────────────────────────────────────
def _cfg():
    from . import config
    return config


def builtin_dir() -> Path:
    return Path(_cfg().SKILLS_DIR)


def home() -> Path:
    env = os.environ.get("LITECODE_SKILLS_HOME", "").strip()
    return Path(env) if env else Path(_cfg().WORKSPACE) / "skills"


def drafts_dir() -> Path:
    return home() / "_drafts"


def legacy_drafts_dir(builtin: Path | None = None) -> Path:
    return (builtin or builtin_dir()) / "drafts"


def backup_dir() -> Path:
    return home() / "_backup"


def state_path() -> Path:
    env = os.environ.get("LITECODE_SKILLS_STATE", "").strip()
    return Path(env) if env else Path(_cfg().WORKSPACE) / "skills_state.json"


def usage_path() -> Path:
    return state_path().with_name("skills_usage.json")


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower()).strip("-_")
    return s[:64]


# ── 状态文件 ──────────────────────────────────────────────────────
def _read_json(p: Path, default):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, type(default)) else default
    except Exception:
        return default


@contextmanager
def _locked(p: Path):
    """进程内 + 跨进程 (web_ui / litecode_server) 的读改写锁。"""
    with _LOCK:
        fh = None
        if _fcntl is not None:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                fh = open(str(p) + ".lock", "a")
                _fcntl.flock(fh, _fcntl.LOCK_EX)
            except Exception:
                fh = None
        try:
            yield
        finally:
            if fh is not None:
                try:
                    _fcntl.flock(fh, _fcntl.LOCK_UN)
                    fh.close()
                except Exception:
                    pass


def load_state() -> dict:
    st = _read_json(state_path(), {})
    st.setdefault("disabled", [])
    st.setdefault("meta", {})
    return st


def update_state(fn) -> dict:
    """fn(state) 原地修改; 原子写回。每次修改都会改变状态文件 mtime → 服务端重建索引。"""
    p = state_path()
    with _locked(p):
        st = load_state()
        fn(st)
        st["updated_at"] = time.time()
        atomic_write_json(p, st)
        return st


def touch_state() -> None:
    update_state(lambda st: None)


def disabled_set() -> set:
    return set(load_state().get("disabled") or [])


def is_enabled(name: str) -> bool:
    return name not in disabled_set()


def set_enabled(name: str, on: bool) -> None:
    def _f(st):
        d = [x for x in st.get("disabled") or [] if x != name]
        if not on:
            d.append(name)
        st["disabled"] = sorted(set(d))
    update_state(_f)


def set_meta(name: str, **fields) -> None:
    def _f(st):
        m = st["meta"].setdefault(name, {})
        m.update({k: v for k, v in fields.items() if v is not None})
    update_state(_f)


def drop_meta(name: str) -> None:
    def _f(st):
        st["meta"].pop(name, None)
        st["disabled"] = [x for x in st.get("disabled") or [] if x != name]
    update_state(_f)


# ── 使用统计 ──────────────────────────────────────────────────────
def record_use(name: str, n: int = 1) -> None:
    if not name or n <= 0:
        return
    p = usage_path()
    try:
        with _locked(p):
            d = _read_json(p, {})
            e = d.setdefault(name, {"count": 0, "last": 0})
            e["count"] = int(e.get("count", 0)) + n
            e["last"] = time.time()
            e.setdefault("first", e["last"])
            atomic_write_json(p, d)
    except Exception:
        pass


def usage(builtin: Path | None = None, include_test: bool = False) -> dict:
    """{name: {"count", "last"}}: 新统计 + 旧 SKILLS_DIR/_usage.json (只读; 测试残留 test-/zz- 滤掉)。"""
    out: dict = {}
    legacy = _read_json((builtin or builtin_dir()) / "_usage.json", {})
    for k, v in legacy.items():
        if not include_test and k.startswith(_TEST_PREFIX):
            continue
        if isinstance(v, dict):
            out[k] = {"count": int(v.get("loaded", 0) or 0), "last": float(v.get("last_ts", 0) or 0)}
    for k, v in _read_json(usage_path(), {}).items():
        if not isinstance(v, dict) or (not include_test and k.startswith(_TEST_PREFIX)):
            continue
        e = out.setdefault(k, {"count": 0, "last": 0.0})
        e["count"] += int(v.get("count", 0) or 0)
        e["last"] = max(e["last"], float(v.get("last", 0) or 0))
    return out


# ── 解析 SKILL.md ─────────────────────────────────────────────────
def frontmatter(text: str) -> dict:
    from .skills import _frontmatter
    return _frontmatter(text)


def as_list(v) -> list:
    """frontmatter 列表字段: YAML 列表 / JSON 串 / 逗号顿号分隔串 都认。"""
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if s.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(s) if str(x).strip()]
        except Exception:
            s = s.strip("[]")
    return [x.strip().strip("\"'") for x in re.split(r"[,，、]", s) if x.strip().strip("\"'")]


_TITLES = {   # 正文没有中文标题的内置技能
    "algorithmic-art": "算法生成艺术", "canvas-design": "海报与视觉设计", "docx": "Word 文档",
    "frontend-design": "前端界面设计", "internal-comms": "内部通讯写作", "pdf": "PDF 处理",
    "pdf-reading": "PDF 读取", "pptx": "PPT 演示文稿", "xlsx": "Excel 表格",
    "skill-creator": "技能创建", "slack-gif-creator": "GIF 动图制作", "theme-factory": "主题样式工厂",
    "deep-search": "深度搜索", "doc-coauthoring": "文档协作写作", "system-selfcheck": "系统自检",
}

_GROUP_NAMES = [
    ("小说写作", ("novel-", "long-novel", "anti-ai-tell-audit", "outline-generation")),
    ("办公文档", ("docx", "xlsx", "pptx", "pdf", "file-reading", "large-file-handling", "longtext-processing",
              "doc-coauthoring", "internal-comms", "html-to-report", "deep-report", "chinese-documentation")),
    ("图像音视频", ("douyin-", "video-studio", "bgm-music", "ai-song", "anime-short", "diagram", "slack-gif-creator", "canvas-design",
               "algorithmic-art", "theme-factory", "image-ocr")),
    ("运维系统", ("linux-admin", "docker-ops", "network-tools", "log-analyzer", "automation", "remote-ops",
              "service-lifecycle", "single-instance-daemon", "system-selfcheck", "windows-container-dev",
              "db-toolkit")),
    ("桌面浏览器", ("browser-", "linux-desktop", "remote-desktop-control", "wechat")),
    ("研究分析", ("research-analyst", "crypto-tracker", "data-analysis", "data-scraper", "deep-search",
              "code-research")),
    ("规划协作", ("brainstorming", "writing-plans", "executing-plans", "subagent-driven-development",
              "dispatching-parallel-agents", "workflow-runner", "product-manager", "using-superpowers",
              "skill-creator", "writing-skills")),
    ("决策思考", ("decision-compass", "first-principles", "premortem", "decision-matrix", "red-team",
              "second-order-thinking", "calibrated-forecast", "bias-check", "after-action-review",
              "discernment-nudge", "priority-triage")),
    ("人物视角", ("companion", "mao-zedong")),   # 私有人物技能在自己的 SKILL.md 写 group: 人物视角 (名字不进公开代码)
    ("编程开发", ("frontend-design", "api-builder", "go", "python-dev", "mcp-builder", "template-engine",
              "webapp-testing", "e2e-self-test", "bug-localization", "large-project", "dependency-audit",
              "systematic-debugging", "test-driven-development", "verification-before-completion",
              "chinese-code-review", "chinese-commit-conventions", "chinese-git-workflow", "using-git-worktrees",
              "finishing-a-development-branch", "receiving-code-review", "requesting-code-review",
              "litecode-debug-protocol")),
]
_GROUP_WORDS = [
    ("小说写作", r"小说|novel|网文|章节|剧情"),
    ("办公文档", r"\bpdf\b|word|excel|\bppt|docx|xlsx|文档|表格|幻灯片|报告"),
    ("图像音视频", r"video|audio|image|music|视频|图片|音乐|配音|海报|绘图|生图"),
    ("编程开发", r"\bcode\b|debug|\btest|\bgit\b|\bapi\b|python|golang|前端|代码|开发|调试|编程"),
    ("运维系统", r"docker|linux|server|kubernetes|运维|服务器|容器|部署"),
    ("桌面浏览器", r"browser|desktop|浏览器|桌面|网页操作"),
    ("研究分析", r"search|research|analy|数据|分析|搜索|调研|股票"),
]


def classify(name: str, desc: str = "", fm: dict | None = None) -> str:
    fm = fm or {}
    if fm.get("group"):
        return str(fm["group"]).strip()
    for probe in (name, str(fm.get("parent") or "")):
        if not probe:
            continue
        for g, keys in _GROUP_NAMES:
            if any(probe == k or (k.endswith("-") and probe.startswith(k)) for k in keys):
                return g
    hay = f"{name} {desc}".lower()
    for g, pat in _GROUP_WORDS:
        if re.search(pat, hay):
            return g
    return "其他"


def _title(name: str, text: str, fm: dict) -> str:
    for k in ("title", "title_zh", "display_name", "displayName"):
        if fm.get(k):
            return str(fm[k]).strip()[:40]
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    if m and re.search(r"[一-龥]", m.group(1)):
        t = re.sub(r"\s*(Skill|skill)\s*$", "", m.group(1)).strip()
        return t[:40]
    return _TITLES.get(name, name)


def _section(text: str, head: str) -> str:
    m = re.search(rf"##\s*{head}.*?\n(.+?)(?:\n##|\Z)", text, re.S | re.I)
    return m.group(1).strip() if m else ""


def parse(md: Path, name: str | None = None) -> dict:
    text = md.read_text(errors="replace")
    fm = frontmatter(text)
    name = name or md.parent.name
    desc = " ".join(str(fm.get("description") or "").split())
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    return {
        "name": name,
        "title": _title(name, text, fm),
        "description": desc,
        "group": classify(name, desc, fm),
        "version": str(fm.get("version") or meta.get("version") or ""),
        "aliases": as_list(fm.get("aliases")),
        "keywords": _section(text, "keyword")[:600] or " ".join(as_list(fm.get("keywords"))),
        "parent": str(fm.get("parent") or ""),
        "path": str(md),
        "chars": len(text),
    }


# ── 枚举 ──────────────────────────────────────────────────────────
def _skill_mds(root: Path):
    if not root.is_dir():
        return []
    out = []
    for md in sorted(root.glob("*/SKILL.md")):
        n = md.parent.name
        if n.startswith(("_", ".")) or n == "drafts":
            continue
        out.append((n, md))
    return out


def all_files(builtin: Path | None = None) -> list[tuple[str, Path, str]]:
    """[(name, SKILL.md, source)]: 内置在前; 已安装与内置同名时内置优先 (安装时已拦截, 这里兜底)。"""
    b = builtin or builtin_dir()
    out, seen = [], set()
    for n, md in _skill_mds(b):
        out.append((n, md, "builtin"))
        seen.add(n)
    meta = load_state().get("meta") or {}
    for n, md in _skill_mds(home()):
        if n in seen:
            continue
        src = (meta.get(n) or {}).get("source") or "installed"
        out.append((n, md, "created" if src == "created" else "installed"))
        seen.add(n)
    return out


def enabled_files(builtin: Path | None = None) -> list[tuple[str, Path]]:
    off = disabled_set()
    return [(n, md) for n, md, _ in all_files(builtin) if n not in off]


def index_signature(builtin: Path | None = None) -> tuple:
    """索引是否要重建: 状态文件 / 内置目录 / 安装目录的 mtime。"""
    sig = []
    for p in (state_path(), builtin or builtin_dir(), home()):
        try:
            sig.append(p.stat().st_mtime_ns)
        except OSError:
            sig.append(0)
    # 再看每个技能子目录: 先建目录、后写 SKILL.md 时, 上一级目录 mtime 不变 (2026-10-23 新加 ai-song 后一直"not found")
    for d in (builtin or builtin_dir(), home()):
        try:
            sig.append(max((e.stat().st_mtime_ns for e in os.scandir(d) if e.is_dir()), default=0))
        except OSError:
            sig.append(0)
    return tuple(sig)


def list_skills(builtin: Path | None = None) -> list[dict]:
    off, meta, use = disabled_set(), load_state().get("meta") or {}, usage(builtin)
    items = []
    for n, md, src in all_files(builtin):
        try:
            info = parse(md, n)
        except Exception:
            continue
        m = meta.get(n) or {}
        u = use.get(n) or {}
        info.update(source=src, enabled=n not in off, use_count=u.get("count", 0), last_used=u.get("last", 0),
                    origin_url=m.get("origin_url", ""), installed_at=m.get("installed_at", 0))
        if not info["version"] and m.get("version"):
            info["version"] = str(m["version"])
        items.append(info)
    return items


def find(name: str, builtin: Path | None = None) -> tuple[Path, str] | None:
    for n, md, src in all_files(builtin):
        if n == name:
            return md, src
    return None


def list_files(skill_dir: Path, limit: int = 200) -> list[dict]:
    out = []
    for p in sorted(skill_dir.rglob("*")):
        if len(out) >= limit:
            break
        if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts:
            out.append({"path": p.relative_to(skill_dir).as_posix(), "size": p.stat().st_size})
    return out


def get_skill(name: str, builtin: Path | None = None) -> dict | None:
    hit = next((s for s in list_skills(builtin) if s["name"] == name), None)
    if hit is None:
        d = get_draft(name)
        return d
    md = Path(hit["path"])
    hit["body"] = md.read_text(errors="replace")
    hit["files"] = list_files(md.parent)
    return hit


# ── 草稿 ──────────────────────────────────────────────────────────
def _draft_roots(builtin: Path | None = None) -> list[Path]:
    return [drafts_dir(), legacy_drafts_dir(builtin)]


def list_drafts(builtin: Path | None = None) -> list[dict]:
    out, seen = [], set()
    for root in _draft_roots(builtin):
        for n, md in _draft_mds(root):
            if n in seen:
                continue
            seen.add(n)
            try:
                info = parse(md, n)
            except Exception:
                continue
            info.update(source="draft", enabled=False, created_at=md.stat().st_mtime)
            out.append(info)
    return sorted(out, key=lambda x: -x["created_at"])


def _draft_mds(root: Path):
    return [(md.parent.name, md) for md in sorted(root.glob("*/SKILL.md"))] if root.is_dir() else []


def get_draft(name: str, builtin: Path | None = None) -> dict | None:
    for root in _draft_roots(builtin):
        md = root / name / "SKILL.md"
        if md.is_file():
            info = parse(md, name)
            info.update(source="draft", enabled=False, body=md.read_text(errors="replace"),
                        files=list_files(md.parent))
            return info
    return None


def draft_path(name: str, builtin: Path | None = None) -> Path | None:
    for root in _draft_roots(builtin):
        if (root / name / "SKILL.md").is_file():
            return root / name
    return None


def write_draft(name: str, skill_md: str) -> Path:
    d = drafts_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    from .atomic_io import atomic_write_text
    atomic_write_text(d / "SKILL.md", skill_md)
    return d


def discard_draft(name: str, builtin: Path | None = None) -> bool:
    d = draft_path(name, builtin)
    if d is None:
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


# ── 安装目录写入 / 删除 / 导出 ────────────────────────────────────
def place(src_dir: Path, name: str, *, source: str, origin_url: str = "", version: str = "") -> dict:
    """把 src_dir 拷进已安装目录。已存在 = 升级: 旧版先挪到 _backup/。返回 {upgraded, backup}。"""
    target = home() / name
    home().mkdir(parents=True, exist_ok=True)
    backup = ""
    tmp = home() / f".{name}.incoming-{int(time.time() * 1000)}"
    shutil.copytree(src_dir, tmp, ignore=shutil.ignore_patterns(".git", "__MACOSX", ".DS_Store", "__pycache__"))
    upgraded = target.exists()
    if upgraded:
        backup_dir().mkdir(parents=True, exist_ok=True)
        b = backup_dir() / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.move(str(target), str(b))
        backup = str(b)
    os.replace(tmp, target)
    set_meta(name, source=source, origin_url=origin_url or "", version=version or "",
             installed_at=time.time(), backup=backup or None)
    return {"upgraded": upgraded, "backup": backup, "path": str(target)}


def uninstall(name: str) -> bool:
    d = home() / name
    if not (d / "SKILL.md").is_file():
        return False
    shutil.rmtree(d, ignore_errors=True)
    drop_meta(name)
    return True


def export_zip(name: str, builtin: Path | None = None) -> bytes | None:
    import io
    import zipfile
    hit = find(name, builtin)
    d = hit[0].parent if hit else draft_path(name, builtin)
    if d is None:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in list_files(d, limit=2000):
            z.write(d / f["path"], f"{name}/{f['path']}")
    return buf.getvalue()
