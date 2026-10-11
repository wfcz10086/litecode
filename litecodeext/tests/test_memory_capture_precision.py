"""自动偏好捕获不误抓聊天/任务说明; 跨会话索引不存重复行。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from core.memory.auto import _RE_PREFER  # noqa: E402
from core.memory_index import MemoryIndex  # noqa: E402


def _pref(msg):
    m = _RE_PREFER.search(msg)
    return f"{m.group(1)}: {m.group(2)}" if m else None


def test_chat_and_task_specs_not_captured():
    assert _pref("不要回老家 烦死了 票已经买好") is None
    assert _pref("必须: 给出文件行号或原始代码证据, 不得修改任何文件") is None
    assert _pref("不要: 空说, 至少 300 字.") is None


def test_real_preferences_captured():
    assert _pref("以后不要做一半就停下等用户说继续") == "以后不要: 做一半就停下等用户说继续"
    assert _pref("你必须先跑测试再说完成了") == "你必须: 先跑测试再说完成了"
    assert _pref("每次都先跑测试再提交代码") == "每次都: 先跑测试再提交代码"
    assert _pref("我希望部署到 production 前必须先跑 e2e").startswith("我希望: 部署")


def test_ascii_comma_separator_stripped():
    assert _pref("记住, 看加密合约一律走 tradingview") == "记住: 看加密合约一律走 tradingview"


def test_index_dedupes_same_category_content(tmp_path):
    idx = MemoryIndex(tmp_path / "m.db")
    a = idx.index_memory("s1", "preference", "以后不要: 做一半就停")
    b = idx.index_memory("s2", "preference", "以后不要: 做一半就停")
    c = idx.index_memory("s2", "milestone", "以后不要: 做一半就停")
    assert a == b and c != a
    assert idx._get_conn().execute("SELECT count(*) FROM memories").fetchone()[0] == 2
    assert len(idx.search("做一半就停", limit=5)) >= 1
