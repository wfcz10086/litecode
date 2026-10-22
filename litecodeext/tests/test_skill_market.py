"""技能市场索引解析 (全 mock, 不联网): GitHub 树 + raw / ClawHub / 腾讯 SkillHub / 聚合与错误。"""
import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib import skill_market as m  # noqa: E402

PDF_MD = "---\nname: pdf\ndescription: PDF toolkit for extracting text and tables\n---\n# PDF\n"


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, t in files.items():
            z.writestr(n, t)
    return buf.getvalue()


def _routes(req: httpx.Request):
    u = str(req.url)
    if u == "https://api.github.com/repos/anthropics/skills":
        return httpx.Response(200, json={"default_branch": "main"})
    if u.startswith("https://api.github.com/repos/anthropics/skills/git/trees/main"):
        return httpx.Response(200, json={"tree": [
            {"path": "README.md", "type": "blob", "size": 10},
            {"path": "skills/pdf/SKILL.md", "type": "blob", "size": 60},
            {"path": "skills/pdf/scripts/x.py", "type": "blob", "size": 9},
            {"path": "skills/docx/SKILL.md", "type": "blob", "size": 60},
        ]})
    if u == "https://raw.githubusercontent.com/anthropics/skills/main/skills/pdf/SKILL.md":
        return httpx.Response(200, text=PDF_MD)
    if u == "https://raw.githubusercontent.com/anthropics/skills/main/skills/pdf/scripts/x.py":
        return httpx.Response(200, text="print(1)\n")
    if u == "https://raw.githubusercontent.com/anthropics/skills/main/skills/docx/SKILL.md":
        return httpx.Response(200, text="---\nname: docx\ndescription: Word documents\n---\n")
    if u.startswith("https://clawhub.ai/api/v1/search"):
        return httpx.Response(200, json={"results": [{
            "displayName": "Pdf", "slug": "pdf", "ownerHandle": "awspace", "downloads": 50978,
            "native": {"skill": {"summary": "Comprehensive PDF toolkit", "stats": {"downloads": 50978}}}}]})
    if u.startswith("https://clawhub.ai/api/v1/skills/pdf/file"):
        return httpx.Response(200, text=PDF_MD)
    if u.startswith("https://clawhub.ai/api/v1/skills/pdf"):
        return httpx.Response(200, json={"skill": {"displayName": "Pdf", "summary": "PDF toolkit"},
                                         "latestVersion": {"version": "0.1.0"}})
    if u.startswith("https://clawhub.ai/api/v1/download"):
        return httpx.Response(200, content=_zip({"SKILL.md": PDF_MD, "_meta.json": '{"slug":"pdf"}'}))
    if u.startswith("https://api.skillhub.cn/api/v1/search"):
        return httpx.Response(200, json={"results": [{
            "displayName": "PDF和图片文字提取", "slug": "pdf-image-text-extractor", "description_zh": "从图片或 PDF 提取文字",
            "namespace": {"handle": "user_5f9c21aa"}, "version": "2.1.0", "downloads": 262644,
            "publisher": {"name": "RedFox"}}]})
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def _mock(tmp_path, monkeypatch):
    monkeypatch.setenv("LITECODE_SKILLS_HOME", str(tmp_path / "skills"))
    monkeypatch.setenv("LITECODE_SKILLS_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(m, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(_routes)))


def test_github_index_parses_tree_and_frontmatter():
    items = asyncio.run(m.github_index("anthropics/skills"))
    by = {i["name"]: i for i in items}
    assert set(by) == {"pdf", "docx"}
    assert by["pdf"]["id"] == "github:anthropics/skills@main:skills/pdf" and by["pdf"]["source"] == "anthropic"
    assert by["pdf"]["description"].startswith("PDF toolkit")
    assert {f["path"] for f in by["pdf"]["files"]} == {"SKILL.md", "scripts/x.py"}
    assert m.cache_get("gh-index:anthropics/skills:") is not None            # 24h 缓存


def test_github_download_fetches_all_files(tmp_path):
    asyncio.run(m.github_index("anthropics/skills"))
    root = asyncio.run(m.github_download("github:anthropics/skills@main:skills/pdf", tmp_path))
    assert (root / "SKILL.md").read_text() == PDF_MD and (root / "scripts" / "x.py").is_file()


def test_clawhub_and_skillhub_parse():
    c = asyncio.run(m.clawhub_search("pdf"))[0]
    assert c["id"] == "clawhub:awspace/pdf" and c["downloads"] == 50978 and c["description"].startswith("Comprehensive")
    s = asyncio.run(m.skillhub_search("pdf"))[0]
    assert s["id"] == "skillhub:user_5f9c21aa/pdf-image-text-extractor" and s["title"] == "PDF和图片文字提取"
    assert s["version"] == "2.1.0" and s["author"] == "RedFox"
    d = asyncio.run(m.clawhub_detail("clawhub:awspace/pdf"))
    assert d["skill_md"] == PDF_MD and d["version"] == "0.1.0"
    kind, data = asyncio.run(m.fetch("clawhub:awspace/pdf", Path("/tmp")))
    assert kind == "zip" and zipfile.ZipFile(io.BytesIO(data)).namelist() == ["SKILL.md", "_meta.json"]


def test_search_aggregates_and_reports_source_errors():
    out = asyncio.run(m.search("pdf", "all"))
    srcs = {i["source"] for i in out["items"]}
    assert srcs == {"anthropic", "clawhub", "skillhub"} and out["errors"] == {}
    assert out["items"][0]["name"] == "pdf"


def test_search_error_is_readable(monkeypatch):
    def boom(req):
        raise httpx.ConnectTimeout("t", request=req)
    monkeypatch.setattr(m, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    out = asyncio.run(m.search("zzz-none", "skillhub"))
    assert out["items"] == [] and "超时" in out["errors"]["skillhub"]


def test_repo_spec_and_ids():
    assert m.parse_repo("https://github.com/a/b/tree/main/skills") == ("a/b", "skills")
    assert m.parse_repo("a/b") == ("a/b", "")
    with pytest.raises(m.MarketError):
        m.parse_repo("not a repo")
    assert m.origin_url("skillhub:ns/x") == "https://skillhub.cn/skills/x?namespace=ns"
    with pytest.raises(m.MarketError):
        asyncio.run(m.detail("ftp:x"))


def test_install_from_market_via_api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import skills_router
    monkeypatch.setattr(skills_router, "require_auth", lambda request: None)
    app = FastAPI()
    app.include_router(skills_router.router)
    c = TestClient(app)
    r = c.get("/api/skills/market", params={"q": "pdf", "source": "clawhub"}).json()
    assert r["items"][0]["id"] == "clawhub:awspace/pdf"
    d = c.get("/api/skills/market/clawhub:awspace/pdf").json()
    assert d["scan"]["level"] == "ok" and "skill_md" in d
    # 与内置 pdf 同名 → 拒装; 用 github 的 docx 也同名 → 拒装 (内置不覆盖)
    out = c.post("/api/skills/install", json={"market_id": "clawhub:awspace/pdf"}).json()
    assert not out["ok"] and "内置" in out["skipped"][0]["reason"]
    assert json.dumps(out, ensure_ascii=False)
