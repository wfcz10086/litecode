"""技能命中打分: 片段折叠 / 功能词 / 套话 / 英文别名整词匹配。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.skill_match import alias_hit, boilerplate, fold_nested, score  # noqa: E402


def test_nested_fragments_count_once():
    assert fold_nested({"写一", "一个", "写一个"}) == {"写一个"}
    assert fold_nested({"修真", "炼气", "筑基"}) == {"修真", "炼气", "筑基"}


def test_generic_phrase_alone_does_not_match():
    n, _ = score({"写一个", "写一", "一个", "报告"}, {"写一个", "写一", "一个", "svg"})
    assert n == 0
    n, _ = score({"帮我", "帮我写", "我写", "需求"}, {"帮我", "帮我写", "我写"})
    assert n == 0


def test_topic_words_still_match():
    n, hits = score({"修真", "炼气", "筑基", "小说"}, {"帮我", "修真", "炼气", "筑基", "小说"})
    assert n == 4 and "帮我" not in hits


def test_boilerplate_by_document_frequency():
    sets = [{"触发词", f"topic{i}"} for i in range(8)] + [{"修真"}]
    drop = boilerplate(sets)
    assert "触发词" in drop and "修真" not in drop
    assert score({"触发词", "修真"}, {"触发词", "修真"}, drop)[0] == 1


def test_ascii_alias_is_whole_word():
    assert not alias_hit("emo", "请调 save_memory 工具")
    assert not alias_hit("emo", "run the demo")
    assert alias_hit("emo", "我今天有点 emo")
    assert alias_hit("ppt", "做个 ppt 吧")
    assert not alias_hit("ppt", "pptx")


def test_extension_alias_and_chinese_alias():
    assert alias_hit(".docx", "打开 a.docx 看看")
    assert not alias_hit(".py", "改 main.pyc")
    assert alias_hit("幻灯片", "做一套幻灯片")
