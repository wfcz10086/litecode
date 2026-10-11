"""技能索引分组: frontmatter parent 的子技能收进父技能一行。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.skills import _frontmatter, render_grouped_index  # noqa: E402

ENTRIES = [("docx", "Word"), ("novel-common", "通用规则"), ("novel-wuxia", "武侠题材"), ("novel-xiuzhen", "修真题材")]
GROUP = {"novel-wuxia": ("novel-common", "武侠"), "novel-xiuzhen": ("novel-common", "修真")}


def test_children_fold_into_parent_line():
    out = render_grouped_index(ENTRIES, GROUP).splitlines()
    assert out == [
        "- docx: Word",
        "- novel-common: 通用规则 子技能: novel-wuxia(武侠) / novel-xiuzhen(修真)",
    ]


def test_orphan_child_still_listed():
    out = render_grouped_index([e for e in ENTRIES if e[0] != "novel-common"], GROUP)
    assert "- novel-wuxia: 武侠题材" in out and "- novel-xiuzhen: 修真题材" in out


def test_no_group_is_plain_index():
    assert render_grouped_index(ENTRIES, {}).count("\n") == len(ENTRIES) - 1


def test_frontmatter_reads_parent_and_label():
    fm = _frontmatter("---\nname: novel-wuxia\nparent: novel-common\nlabel: 武侠\ndescription: >\n  武侠\n---\n# x")
    assert fm["parent"] == "novel-common" and fm["label"] == "武侠"


def test_all_genre_skills_declare_parent():
    skills = Path(__file__).resolve().parent.parent / "skills"
    genres = [p for p in skills.glob("novel-*/SKILL.md") if p.parent.name not in ("novel-common", "novel-writing")]
    assert len(genres) == 21
    for p in genres:
        fm = _frontmatter(p.read_text())
        assert fm.get("parent") == "novel-common" and fm.get("label"), p.parent.name
