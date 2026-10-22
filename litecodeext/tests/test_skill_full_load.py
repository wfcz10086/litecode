"""P0-1 技能完整加载: 索引只存元信息 + 自动注入预览; load_skill 读源文件全文, 长正文按章节续读。"""
import asyncio
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import lib.skills as S  # noqa: E402
from lib import skill_body as B  # noqa: E402

NAME = "zz-long-skill-test"


def _long_skill_text() -> str:
    head = ("---\nname: %s\ndescription: 超长测试技能 longskilltest 测试专用 合成长文\n---\n\n"
            "# 超长测试技能\n\n开头说明。\n\n" % NAME)
    secs = []
    for i in range(1, 9):
        body = "".join(f"第{i}章第{j}行 内容 SECMARK{i}-{j}\n" for j in range(120))
        secs.append(f"## 第{i}章 标题{i}\n\n{body}\n")
    # 代码块里的 ## 不应被当成章节
    secs.insert(2, "## 代码章\n\n```bash\n## 这不是标题\necho hi\n```\n\n")
    # 单章超长 (> UNIT_CHARS) → 切成"续 k/n"
    secs.append("## 巨型章\n\n" + "".join(f"巨型行{j} GIANT{j}\n" for j in range(1200)))
    secs.append("## 结尾章\n\nTHE_END_MARKER\n")
    return head + "".join(secs)


@pytest.fixture
def skill_env(tmp_path, monkeypatch):
    d = tmp_path / NAME
    d.mkdir()
    text = _long_skill_text()
    (d / "SKILL.md").write_text(text)
    short = tmp_path / "zz-short-skill-test"
    short.mkdir()
    (short / "SKILL.md").write_text("---\nname: zz-short-skill-test\ndescription: 短技能\n---\n\n## 用法\n\n短正文 SHORT_END\n")
    monkeypatch.setattr(S, "SKILLS_DIR", tmp_path)
    S._build_skill_index()
    yield d / "SKILL.md", text
    monkeypatch.undo()
    S._build_skill_index()


def _load(**args):
    from core.tools.handlers.skills import h_load_skill
    out, _ = asyncio.run(h_load_skill("test", args))
    return out


def _bodies_in(out: str, full: str) -> str:
    """去掉头尾提示行, 只留正文 (头 1 行, 尾部从 '\n---\n[Skill: ' 起)。"""
    body = out.split("\n", 1)[1]
    cut = body.rfind("\n---\n[Skill: ")
    return body[:cut] if cut >= 0 else body


def test_fixture_is_long():
    assert len(_long_skill_text()) > 10000


def test_index_preview_truncated_but_marked(skill_env):
    _, text = skill_env
    preview = next(c for n, _, c, _ in S._SKILL_INDEX if n == NAME)
    assert len(preview) < len(text)
    assert S.SUMMARY_MARK in preview and "完整内容" not in preview
    assert f"load_skill(name='{NAME}'" in preview
    assert S._SKILL_PATHS[NAME].name == "SKILL.md"


def test_short_skill_marked_complete(skill_env):
    out = _load(name="zz-short-skill-test")
    assert out.startswith("[Skill: zz-short-skill-test] (完整内容")
    assert "SHORT_END" in out


def test_explicit_load_reads_full_text_via_sections(skill_env):
    """从第 1 节开始按续读提示一路读到底, 拼起来 == 源文件全文, 且每段都不标完整。"""
    _, text = skill_env
    got, section, seen = "", None, 0
    while True:
        out = _load(name=NAME, section=section) if section else _load(name=NAME)
        seen += 1
        assert "完整内容" not in out.split("\n", 1)[0]
        assert "非完整" in out.split("\n", 1)[0]
        got += _bodies_in(out, text)
        m = re.search(r"续读: load_skill\(name='%s', section='(\d+)'\)" % re.escape(NAME), out)
        if not m:
            assert "已到末尾" in out
            break
        section = m.group(1)
        assert seen < 50
    assert _norm(got) == _norm(text)   # 分段边界只差空行
    assert "THE_END_MARKER" in got and "GIANT1199" in got and "SECMARK8-119" in got


def _norm(s: str) -> str:
    return re.sub(r"\n+", "\n", s).strip()


def test_page_reports_remaining_and_toc(skill_env):
    _, text = skill_env
    out = _load(name=NAME)
    head = out.split("\n", 1)[0]
    m = re.search(r"全文 (\d+) 字.*本次返回.*?(\d+) 字; 之前 0 字, 之后还剩 (\d+) 字", head)
    assert m, head
    total, size, remain = map(int, m.groups())
    assert total == len(text) and size + remain == total and size <= B.LOAD_PAGE_CHARS
    assert "章节目录" in out and "[未读]" in out and "续读: load_skill(" in out


def test_section_by_title_and_code_fence(skill_env):
    out = _load(name=NAME, section="代码章")
    assert "## 这不是标题" in out
    titles = [t for t, _ in B.split_sections(skill_env[1])]
    assert "这不是标题" not in titles and "代码章" in titles
    out2 = _load(name=NAME, section="结尾章")
    assert "THE_END_MARKER" in out2 and "已到末尾" in out2


def test_giant_section_split_into_parts(skill_env):
    us = B.units(skill_env[1])
    giant = [t for t, _ in us if t.startswith("巨型章")]
    assert len(giant) >= 2 and all("续" in t for t in giant)
    assert all(len(b) <= B.UNIT_CHARS for _, b in us)
    assert "".join(b for _, b in us) == skill_env[1]


def test_bad_section_lists_toc(skill_env):
    out = _load(name=NAME, section="不存在的章节xyz")
    assert "没有章节" in out and "第1章 标题1" in out and "完整内容" not in out


def test_explicit_load_reads_source_not_cache(skill_env):
    """源文件改了 (未重建索引), load_skill 也拿到新内容。"""
    path, _ = skill_env
    path.write_text("---\nname: x\ndescription: d\n---\n\nFRESH_CONTENT\n")
    out = _load(name=NAME)
    assert "FRESH_CONTENT" in out and "(完整内容" in out


def test_auto_load_label_not_complete_for_long(skill_env):
    from lib.prompt import matched_skills_block
    block = matched_skills_block("请用 longskilltest 超长测试技能 合成长文 写一个 report 文件", external=True)
    assert f"[Active Skill: {NAME}]" in block
    first = block.split("\n", 1)[0]
    assert "完整内容" not in first and "load_skill" in first
    assert S.SUMMARY_MARK in block


def test_render_complete_only_when_whole():
    txt = "## a\n" + "x\n" * 100
    out, ok = B.render("n", txt)
    assert ok and "(完整内容" in out
    out, ok = B.render("n", txt + "## b\n" + "y" * 20000, limit=5000)
    assert not ok and "完整内容" not in out
