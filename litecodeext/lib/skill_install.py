"""lib/skill_install.py — 技能包安装: 暂存 → 识别 → 扫描 → 落盘。

来源: zip (可能包一层目录, 一个包可含多个技能) / git https 仓库 / 粘贴 SKILL.md / 技能市场下载。
格式: Claude Agent Skills (目录 + SKILL.md, frontmatter name/description) 与 OpenClaw/ClawHub
(SKILL.md + _meta.json/skill-card.md 等附带文件; frontmatter 可能没有 name, 用 _meta.json slug 或目录名)。

两段式: stage_*() 把内容放进临时暂存目录并返回 Stage (含每个技能的扫描结果与冲突判断);
install(stage, confirm_warn) 才真正落盘。block 级发现一律拒装; 有 warn 时 confirm_warn=False 不装,
由前端展示扫描结果让用户勾选确认后带 stage_id 再提交。
冲突: 与内置同名 → 不覆盖 (拒装); 与已安装同名 → 视为升级, 旧版挪到 _backup/。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import skill_registry as reg
from . import skill_scan

MAX_ZIP_BYTES = 30 * 1024 * 1024        # 上传包大小
MAX_UNZIPPED = 80 * 1024 * 1024         # 解压后总大小
MAX_ENTRIES = 3000
GIT_TIMEOUT = 60
STAGE_TTL = 1800
_SKIP_DIRS = {".git", "__MACOSX", "node_modules", "__pycache__", ".github"}


class InstallError(Exception):
    def __init__(self, msg: str, scan: dict | None = None):
        super().__init__(msg)
        self.scan = scan


@dataclass
class Candidate:
    name: str
    dir: Path
    title: str = ""
    description: str = ""
    version: str = ""
    scan: dict = field(default_factory=dict)
    conflict: str = "new"          # new / upgrade / builtin
    error: str = ""

    def public(self) -> dict:
        return {"name": self.name, "title": self.title, "description": self.description[:300],
                "version": self.version, "scan": self.scan, "conflict": self.conflict, "error": self.error}


@dataclass
class Stage:
    id: str
    root: Path
    origin_url: str = ""
    source: str = "installed"
    candidates: list = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def public(self) -> dict:
        lv = "ok"
        for c in self.candidates:
            if c.error:
                continue
            lv = max(lv, c.scan.get("level", "ok"), key=lambda x: {"ok": 0, "warn": 1, "block": 2}[x])
        return {"stage_id": self.id, "origin_url": self.origin_url, "level": lv,
                "skills": [c.public() for c in self.candidates]}


_STAGES: dict[str, Stage] = {}
_LOCK = threading.Lock()


def _gc():
    now = time.time()
    with _LOCK:
        for k, st in list(_STAGES.items()):
            if now - st.created > STAGE_TTL:
                shutil.rmtree(st.root, ignore_errors=True)
                _STAGES.pop(k, None)


def _new_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="lc_skill_stage_"))


def get_stage(stage_id: str) -> Stage | None:
    _gc()
    with _LOCK:
        return _STAGES.get(stage_id)


def drop_stage(stage_id: str) -> None:
    with _LOCK:
        st = _STAGES.pop(stage_id, None)
    if st:
        shutil.rmtree(st.root, ignore_errors=True)


# ── 识别 ──────────────────────────────────────────────────────────
def _skill_md(d: Path) -> Path | None:
    for n in ("SKILL.md", "skill.md", "Skill.md"):
        if (d / n).is_file():
            return d / n
    return None


def find_skill_dirs(root: Path) -> list[Path]:
    """含 SKILL.md 的目录; 技能目录内部不再往下找 (scripts/ 里的同名文件不算新技能)。"""
    out: list[Path] = []

    def walk(d: Path, depth: int):
        if depth > 6 or d.name in _SKIP_DIRS:
            return
        if _skill_md(d):
            out.append(d)
            return
        try:
            subs = sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return
        for s in subs:
            walk(s, depth + 1)
    walk(root, 0)
    return out


def _meta_json(d: Path) -> dict:
    for n in ("_meta.json", "meta.json", "skill.json"):
        p = d / n
        if p.is_file():
            try:
                v = json.loads(p.read_text(errors="replace"))
                return v if isinstance(v, dict) else {}
            except Exception:
                return {}
    return {}


def _describe(d: Path, fallback_name: str = "") -> Candidate:
    md = _skill_md(d)
    if md.name != "SKILL.md":
        md.rename(d / "SKILL.md")
        md = d / "SKILL.md"
    text = md.read_text(errors="replace")
    fm = reg.frontmatter(text)
    meta = _meta_json(d)
    raw = fm.get("name") or meta.get("slug") or meta.get("name") or (d.name if not d.name.startswith("lc_skill_stage_") else "") \
        or fallback_name
    name = reg.slugify(str(raw))
    info = reg.parse(md, name or "unnamed")
    c = Candidate(name=name, dir=d, title=info["title"], description=info["description"],
                  version=str(info["version"] or meta.get("version") or ""))
    if not name or not reg.valid_name(name):
        c.error = f"技能名不合法: {raw!r} (只能用小写字母/数字/-/_)"
    if not c.description:
        c.error = c.error or "SKILL.md 缺少 description (frontmatter)"
    c.scan = skill_scan.scan_dir(d)
    builtin = {n for n, _ in reg._skill_mds(reg.builtin_dir())}
    if name in builtin:
        c.conflict = "builtin"
        c.error = c.error or f"与内置技能 {name} 同名, 不覆盖内置技能 (可改 frontmatter name 后再装)"
    elif (reg.home() / name / "SKILL.md").is_file():
        c.conflict = "upgrade"
    return c


def _register(root: Path, origin_url: str, fallback_name: str = "", source: str = "installed") -> Stage:
    dirs = find_skill_dirs(root)
    if not dirs:
        shutil.rmtree(root, ignore_errors=True)
        raise InstallError("包里没有找到 SKILL.md")
    st = Stage(id=uuid.uuid4().hex[:12], root=root, origin_url=origin_url, source=source)
    seen = set()
    for d in dirs[:50]:
        c = _describe(d, fallback_name if len(dirs) == 1 else "")
        if c.name in seen:
            c.error = c.error or f"包内重复的技能名 {c.name}"
        seen.add(c.name)
        st.candidates.append(c)
    _gc()
    with _LOCK:
        _STAGES[st.id] = st
    return st


# ── 暂存 ──────────────────────────────────────────────────────────
def _safe_member(name: str) -> bool:
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return False
    parts = re.split(r"[\\/]+", name)
    return ".." not in parts


def stage_zip(data: bytes, origin_url: str = "", fallback_name: str = "") -> Stage:
    if len(data) > MAX_ZIP_BYTES:
        raise InstallError(f"压缩包过大 ({len(data) // 1024 // 1024} MB), 上限 {MAX_ZIP_BYTES // 1024 // 1024} MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise InstallError("不是有效的 zip 压缩包")
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise InstallError(f"压缩包文件过多 ({len(infos)})")
    bad = [i.filename for i in infos if not _safe_member(i.filename)]
    links = [i.filename for i in infos if (i.external_attr >> 16) & 0o170000 == 0o120000]
    if bad or links:
        findings = [skill_scan.zip_traversal_finding(n) for n in bad]
        findings += [{"rule": "symlink", "level": "block", "title": "压缩包含符号链接", "file": n, "line": 0,
                      "snippet": n} for n in links]
        raise InstallError("压缩包含路径穿越或符号链接, 已拒绝", skill_scan.summarize(findings))
    if sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise InstallError("解压后过大, 已拒绝")
    root = _new_root()
    for i in infos:
        target = (root / i.filename).resolve()
        if root.resolve() not in target.parents and target != root.resolve():
            shutil.rmtree(root, ignore_errors=True)
            raise InstallError("压缩包路径穿越", skill_scan.summarize([skill_scan.zip_traversal_finding(i.filename)]))
        if i.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with z.open(i) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return _register(root, origin_url, fallback_name)


def stage_skill_md(text: str, name: str = "", origin_url: str = "", source: str = "installed") -> Stage:
    text = (text or "").strip()
    if not text:
        raise InstallError("SKILL.md 内容为空")
    if len(text) > 400_000:
        raise InstallError("SKILL.md 过长")
    fm = reg.frontmatter(text)
    nm = reg.slugify(name or fm.get("name") or "")
    if not nm:
        raise InstallError("请填写技能名, 或在 frontmatter 里写 name:")
    if not fm:   # 没有 frontmatter 的纯 markdown: 用第一段正文当 description 补上
        first = next((ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")), nm)
        text = f"---\nname: {nm}\ndescription: {json.dumps(first[:150], ensure_ascii=False)}\n---\n\n{text}"
    root = _new_root()
    (root / nm).mkdir()
    (root / nm / "SKILL.md").write_text(text + "\n", encoding="utf-8")
    return _register(root, origin_url, nm, source=source)


def stage_dir(src: Path, origin_url: str = "", fallback_name: str = "", source: str = "installed") -> Stage:
    root = _new_root()
    dst = root / (src.name or "skill")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*_SKIP_DIRS), symlinks=True)
    return _register(root, origin_url, fallback_name, source=source)


_GH_TREE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/(?:tree|blob)/([^/]+)/(.+?)/?$")


def parse_git_url(url: str) -> tuple[str, str, str]:
    """→ (clone_url, branch, subpath)。只允许 https; github 的 /tree/<branch>/<path> 链接拆开。"""
    url = (url or "").strip()
    if re.match(r"^[\w.-]+/[\w.-]+(/.*)?$", url):        # owner/repo[/path]
        o, r, *rest = url.split("/", 2)
        return f"https://github.com/{o}/{r}.git", "", rest[0].strip("/") if rest else ""
    if not url.startswith("https://"):
        raise InstallError("只支持 https:// 开头的 git 地址")
    if re.search(r"[\s;|&`$<>]", url):
        raise InstallError("git 地址含非法字符")
    m = _GH_TREE.match(url)
    if m:
        o, r, br, sub = m.groups()
        sub = re.sub(r"/SKILL\.md$", "", sub, flags=re.I)
        return f"https://github.com/{o}/{r}.git", br, sub
    return url, "", ""


def stage_git(url: str) -> Stage:
    clone, branch, sub = parse_git_url(url)
    root = _new_root()
    dest = root / "repo"
    cmd = ["git", "clone", "--depth", "1", "--single-branch"]
    if branch:
        cmd += ["--branch", branch]
    cmd += ["--", clone, str(dest)]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        shutil.rmtree(root, ignore_errors=True)
        raise InstallError(f"git clone 超时 ({GIT_TIMEOUT}s), 国内访问 GitHub 慢可配代理后重试")
    except FileNotFoundError:
        shutil.rmtree(root, ignore_errors=True)
        raise InstallError("本机没有 git 命令")
    if r.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise InstallError("git clone 失败: " + (r.stderr or r.stdout).strip()[-300:])
    shutil.rmtree(dest / ".git", ignore_errors=True)
    base = (dest / sub).resolve() if sub else dest
    if dest.resolve() not in base.parents and base != dest.resolve():
        shutil.rmtree(root, ignore_errors=True)
        raise InstallError("子路径非法")
    if not base.is_dir():
        shutil.rmtree(root, ignore_errors=True)
        raise InstallError(f"仓库里没有目录 {sub}")
    if base != dest:      # 只留子目录
        keep = root / base.name
        shutil.move(str(base), str(keep))
        shutil.rmtree(dest, ignore_errors=True)
    return _register(root, url, fallback_name=base.name)


# ── 落盘 ──────────────────────────────────────────────────────────
def install(stage: Stage, confirm_warn: bool = False, only: list | None = None) -> dict:
    """返回 {installed:[...], skipped:[{name, reason}], need_confirm: bool, ...stage.public()}。"""
    installed, skipped = [], []
    need = False
    for c in stage.candidates:
        if only and c.name not in only:
            continue
        if c.error:
            skipped.append({"name": c.name, "reason": c.error})
            continue
        lv = c.scan.get("level", "ok")
        if lv == "block":
            skipped.append({"name": c.name, "reason": "安全扫描未通过 (block), 已拒装"})
            continue
        if lv == "warn" and not confirm_warn:
            need = True
            continue
    if need:
        out = stage.public()
        out.update(installed=[], skipped=skipped, need_confirm=True)
        return out
    for c in stage.candidates:
        if only and c.name not in only:
            continue
        if c.error or c.scan.get("level") == "block":
            continue
        r = reg.place(c.dir, c.name, source=stage.source, origin_url=stage.origin_url, version=c.version)
        installed.append({"name": c.name, "title": c.title, "upgraded": r["upgraded"], "backup": r["backup"]})
    drop_stage(stage.id)
    out = stage.public()
    out.update(installed=installed, skipped=skipped, need_confirm=False)
    return out


def activate_draft(name: str, confirm_warn: bool = False) -> dict:
    """草稿 → 已安装 (source=created), 同样先扫描。成功后删草稿。"""
    d = reg.draft_path(name)
    if d is None:
        raise InstallError(f"没有草稿 {name}")
    st = stage_dir(d, origin_url="", fallback_name=name, source="created")
    out = install(st, confirm_warn=confirm_warn)
    if out.get("installed"):
        reg.discard_draft(name)
    return out
