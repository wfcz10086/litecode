"""Unit tests for _CN_SKILL_MAP alias boundary — P0-2.

The bare 1-char alias "累" in the companion skill's trigger map matched as a
literal substring of unrelated words (劳累/连累/累计/日积月累/...), silently
injecting the companion persona into ordinary conversations. Fixed by
dropping "累" (already covered by the 2-char "心累"/"疲惫" aliases).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext"))

from lib.skills import _CN_SKILL_MAP


def _companion_alias_hit(msg: str) -> bool:
    """复刻 auto_load_skills() 里对 companion 别名的判定逻辑 (纯子串匹配),
    不依赖 _SKILL_INDEX (companion skill 内容只在 docker 挂载路径下才有,
    host 侧跑测试本来就读不到, 跟这里要测的别名边界问题无关)."""
    msg_lower = msg.lower()
    return any(alias in msg_lower for alias, name in _CN_SKILL_MAP.items()
               if name == "companion")


def test_bare_single_char_alias_removed():
    assert "累" not in _CN_SKILL_MAP


def test_true_positive_still_matches_companion():
    """真实情绪求安慰场景必须仍能触发 companion (走 心累/撑不下去 别名)."""
    assert _companion_alias_hit("最近心累, 撑不下去了, 不知道找谁说")


def test_false_positive_daily_accumulation_no_longer_matches():
    """'日积月累' 含字面'累', 修复前会误触发陪伴人格, 修复后不该触发."""
    assert not _companion_alias_hit("这个项目做了三年, 日积月累攒了不少经验, 想写个技术复盘")


def test_false_positive_workload_accumulation_no_longer_matches():
    """'累计'/'劳累' 含字面'累', 同样不该误触发."""
    assert not _companion_alias_hit("请帮我统计一下累计销售额, 生成一份报表")


def test_other_companion_aliases_unaffected():
    """确认这次改动只删了 '累' 这一条, 其余情绪别名没被误删."""
    for alias in ("心累", "疲惫", "崩溃", "焦虑", "失眠", "撑不下去"):
        assert alias in _CN_SKILL_MAP
        assert _CN_SKILL_MAP[alias] == "companion"
