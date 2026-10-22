"""skills_router.py — 技能包管理 (个人版): 已安装 / 草稿 / 技能市场 / 安装 / 新建。

GET    /api/skills?q=&group=&source=&enabled=   列表 (本地检索打分) + 分组计数 + 草稿数
GET    /api/skills/drafts                       草稿列表
PUT    /api/skills/drafts/{name}                改草稿正文 {skill_md}
POST   /api/skills/drafts/{name}/activate       草稿启用 {confirm?} (先扫描)
POST   /api/skills/drafts/{name}/discard        丢弃草稿
POST   /api/skills/create                       一句话新建 {description, name?} → 模型写草稿
GET    /api/skills/market?q=&source=&repo=      技能市场搜索 (anthropic/clawhub/skillhub/github)
GET    /api/skills/market/{id}                  市场条目详情 + SKILL.md 预览 + 预扫描
POST   /api/skills/install                      multipart zip / {git_url} / {skill_md,name} / {market_id} / {stage_id}
                                                + confirm (warn 需勾选确认); 先扫描后安装
GET    /api/skills/{name}                       详情 (正文 + 文件列表)
POST   /api/skills/{name}/toggle                启用/停用 {enabled?}
DELETE /api/skills/{name}                       卸载 (内置只能停用)
GET    /api/skills/{name}/export                导出 zip
逻辑在 lib/skill_registry.py / skill_install.py / skill_market.py / skill_scan.py / skill_author.py。
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from lib import skill_install as inst
from lib import skill_market as market
from lib import skill_registry as reg
from lib import skill_scan
from lib.skill_search import search as local_search
from routers._body import json_object
from routers.auth_router import require_auth

router = APIRouter()
ORDER = 150

_PUBLIC = ("name", "title", "description", "group", "source", "version", "aliases", "enabled", "use_count",
           "last_used", "origin_url", "installed_at", "parent", "score", "created_at", "chars")


def _pub(item: dict) -> dict:
    return {k: item[k] for k in _PUBLIC if k in item}


def _name_or_404(name: str) -> str:
    if not reg.valid_name(name):
        raise HTTPException(400, "技能名不合法")
    return name


def _bool(v) -> bool | None:
    if v is None or v == "":
        return None
    return str(v).lower() in ("1", "true", "yes", "on")


@router.get("/api/skills")
async def api_skills(request: Request, q: str = "", group: str = "", source: str = "", enabled: str = ""):
    require_auth(request)
    items = reg.list_skills()
    groups: dict = {}
    for it in items:
        groups[it["group"]] = groups.get(it["group"], 0) + 1
    en = _bool(enabled)
    if group:
        items = [i for i in items if i["group"] == group]
    if source:
        items = [i for i in items if i["source"] == source]
    if en is not None:
        items = [i for i in items if i["enabled"] == en]
    items = local_search(items, q) if q.strip() else sorted(items, key=lambda x: (x["group"], x["name"]))
    return {"items": [_pub(i) for i in items], "total": len(items),
            "groups": [{"name": g, "count": n} for g, n in sorted(groups.items(), key=lambda x: -x[1])],
            "drafts": len(reg.list_drafts())}


# ── 草稿 ──────────────────────────────────────────────────────────
@router.get("/api/skills/drafts")
async def api_drafts(request: Request):
    require_auth(request)
    out = []
    for d in reg.list_drafts():
        p = reg.draft_path(d["name"])
        out.append({**_pub(d), "scan": skill_scan.scan_dir(p) if p else {"level": "ok", "findings": []}})
    return {"items": out}


@router.put("/api/skills/drafts/{name}")
async def api_draft_edit(name: str, request: Request):
    require_auth(request)
    _name_or_404(name)
    body = await json_object(request)
    text = str(body.get("skill_md") or "").strip()
    if not text:
        raise HTTPException(400, "SKILL.md 不能为空")
    if len(text) > 200_000:
        raise HTTPException(413, "SKILL.md 过长")
    p = reg.draft_path(name)
    if p is None:
        raise HTTPException(404, "没有这个草稿")
    (p / "SKILL.md").write_text(text + "\n", encoding="utf-8")
    return {"ok": True, "draft": _draft_view(name)}


def _draft_view(name: str) -> dict:
    d = reg.get_draft(name)
    if d is None:
        raise HTTPException(404, "没有这个草稿")
    p = reg.draft_path(name)
    return {**_pub(d), "body": d.get("body", ""), "files": d.get("files", []), "scan": skill_scan.scan_dir(p)}


@router.post("/api/skills/drafts/{name}/activate")
async def api_draft_activate(name: str, request: Request):
    require_auth(request)
    _name_or_404(name)
    body = await json_object(request) if (await request.body()).strip() else {}
    try:
        out = await asyncio.to_thread(inst.activate_draft, name, bool(body.get("confirm")))
    except inst.InstallError as e:
        raise HTTPException(400, str(e))
    return out


@router.post("/api/skills/drafts/{name}/discard")
async def api_draft_discard(name: str, request: Request):
    require_auth(request)
    _name_or_404(name)
    if not reg.discard_draft(name):
        raise HTTPException(404, "没有这个草稿")
    return {"ok": True}


@router.post("/api/skills/create")
async def api_skill_create(request: Request):
    """一句话新建: 模型写 SKILL.md → 存草稿 → 返回预览 + 扫描结果 (前端卡片展示)。"""
    require_auth(request)
    from lib.rate_limit import rate_check
    rate_check(request, "skill_create", 10, 60)
    body = await json_object(request)
    desc = str(body.get("description") or "").strip()
    if len(desc) < 4:
        raise HTTPException(400, "请用一句话描述这个技能要做什么 (至少 4 个字)")
    name = reg.slugify(str(body.get("name") or ""))
    from lib import skill_author
    try:
        from routers.model_router import _active_model_id
        mid = await _active_model_id()
    except Exception:
        mid = ""
    try:
        name, md = await skill_author.generate(desc, name, model_id=mid)
    except Exception as e:
        raise HTTPException(502, f"生成失败: {e}")
    builtin = {n for n, _, s in reg.all_files() if s == "builtin"}
    base, i = name, 2
    while name in builtin or reg.draft_path(name) or (reg.home() / name).exists():
        name = f"{base}-{i}"
        i += 1
    if name != base:
        import re
        md = re.sub(r"(?m)^name:.*$", f"name: {name}", md, count=1)
    reg.write_draft(name, md)
    return {"ok": True, "draft": _draft_view(name)}


# ── 技能市场 ──────────────────────────────────────────────────────
@router.get("/api/skills/market")
async def api_market(request: Request, q: str = "", source: str = "all", repo: str = ""):
    require_auth(request)
    try:
        return await market.search(q, source or "all", repo)
    except market.MarketError as e:
        raise HTTPException(400, str(e))


@router.get("/api/skills/market/{mid:path}")
async def api_market_detail(mid: str, request: Request):
    require_auth(request)
    try:
        d = await market.detail(mid)
    except market.MarketError as e:
        raise HTTPException(502, str(e))
    d.pop("files", None)
    d["scan"] = skill_scan.summarize(skill_scan.scan_text(d.get("skill_md") or ""))
    d["installed"] = any(s["name"] == d.get("name") for s in reg.list_skills())
    return d


# ── 安装 ──────────────────────────────────────────────────────────
async def _stage_from_request(request: Request) -> tuple[inst.Stage | None, bool, dict]:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        form = await request.form()
        f = form.get("file")
        if f is None or not hasattr(f, "read"):
            raise HTTPException(400, "缺少 zip 文件 (字段名 file)")
        data = await f.read()
        confirm = str(form.get("confirm") or "").lower() in ("1", "true", "on")
        fname = Path(getattr(f, "filename", "") or "skill.zip").stem
        st = await asyncio.to_thread(inst.stage_zip, data, "", reg.slugify(fname))
        return st, confirm, {}
    body = await json_object(request)
    confirm = bool(body.get("confirm"))
    if body.get("stage_id"):
        st = inst.get_stage(str(body["stage_id"]))
        if st is None:
            raise HTTPException(410, "暂存已过期, 请重新安装")
        return st, confirm, body
    if body.get("skill_md"):
        return await asyncio.to_thread(inst.stage_skill_md, str(body["skill_md"]), str(body.get("name") or "")), \
            confirm, body
    if body.get("git_url"):
        return await asyncio.to_thread(inst.stage_git, str(body["git_url"])), confirm, body
    if body.get("market_id"):
        mid = str(body["market_id"])
        tmp = Path(tempfile.mkdtemp(prefix="lc_skill_dl_"))
        try:
            kind, got = await market.fetch(mid, tmp)
            name_hint = mid.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            if kind == "zip":
                st = await asyncio.to_thread(inst.stage_zip, got, market.origin_url(mid), reg.slugify(name_hint))
            else:
                st = await asyncio.to_thread(inst.stage_dir, got, market.origin_url(mid), reg.slugify(name_hint))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return st, confirm, body
    raise HTTPException(400, "需要 zip 文件 / git_url / skill_md / market_id / stage_id 之一")


@router.post("/api/skills/install")
async def api_install(request: Request):
    require_auth(request)
    try:
        st, confirm, body = await _stage_from_request(request)
    except inst.InstallError as e:
        return Response(status_code=400, media_type="application/json",
                        content=_json({"ok": False, "error": str(e), "scan": e.scan}))
    except market.MarketError as e:
        raise HTTPException(502, str(e))
    only = body.get("only") if isinstance(body.get("only"), list) else None
    out = await asyncio.to_thread(inst.install, st, confirm, only)
    out["ok"] = bool(out.get("installed"))
    return out


def _json(d) -> str:
    import json
    return json.dumps(d, ensure_ascii=False)


# ── 单个技能 ──────────────────────────────────────────────────────
@router.get("/api/skills/{name}")
async def api_skill_get(name: str, request: Request):
    require_auth(request)
    _name_or_404(name)
    d = reg.get_skill(name)
    if d is None:
        raise HTTPException(404, "没有这个技能")
    if d.get("source") == "draft":
        return _draft_view(name)
    return {**_pub(d), "body": d.get("body", ""), "files": d.get("files", []), "path": d.get("path", "")}


@router.post("/api/skills/{name}/toggle")
async def api_skill_toggle(name: str, request: Request):
    require_auth(request)
    _name_or_404(name)
    hit = reg.find(name)
    if hit is None:
        raise HTTPException(404, "没有这个技能")
    body = await json_object(request) if (await request.body()).strip() else {}
    on = body.get("enabled")
    on = (not reg.is_enabled(name)) if on is None else bool(on)
    reg.set_enabled(name, on)
    return {"ok": True, "name": name, "enabled": on}


@router.delete("/api/skills/{name}")
async def api_skill_delete(name: str, request: Request):
    require_auth(request)
    _name_or_404(name)
    hit = reg.find(name)
    if hit is None:
        if reg.discard_draft(name):
            return {"ok": True, "draft": True}
        raise HTTPException(404, "没有这个技能")
    if hit[1] == "builtin":
        raise HTTPException(400, "内置技能不能卸载, 只能停用")
    reg.uninstall(name)
    return {"ok": True}


@router.get("/api/skills/{name}/export")
async def api_skill_export(name: str, request: Request):
    require_auth(request)
    _name_or_404(name)
    data = reg.export_zip(name)
    if data is None:
        raise HTTPException(404, "没有这个技能")
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
