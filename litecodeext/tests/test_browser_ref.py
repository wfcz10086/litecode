"""编号式浏览器操作: 快照格式化 + 动作构造 (纯函数部分; 真浏览器联调见 reports/)。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from tools.browser_ref import build_action, format_snapshot, parse_snapshot, SNAPSHOT_JS  # noqa: E402


def test_format_lists_refs_with_kinds():
    snap = {"title": "登录页", "url": "http://x/login", "text": "欢迎", "items": [
        {"ref": 1, "tag": "input", "type": "text", "name": "用户名", "value": "bob", "in_view": True},
        {"ref": 2, "tag": "input", "type": "checkbox", "name": "记住我", "checked": True, "in_view": True},
        {"ref": 3, "tag": "button", "name": "登录", "disabled": True, "in_view": True},
        {"ref": 4, "tag": "a", "name": "帮助", "href": "/help", "in_view": False},
        {"ref": 5, "tag": "select", "name": "语言", "options": ["中文", "English"], "in_view": True},
    ]}
    out = format_snapshot(snap)
    assert '[1] textbox "用户名" 值="bob"' in out
    assert '[2] checkbox "记住我" 已勾选' in out
    assert '[3] button "登录" 禁用' in out
    assert '[4] link "帮助" → /help (需滚动)' in out
    assert '[5] select "语言" 选项=中文/English' in out
    assert "欢迎" in out


def test_empty_page():
    assert "(没有可交互元素)" in format_snapshot({"title": "", "url": "about:blank", "items": []})


def test_build_action_maps_to_server_actions():
    assert build_action("click", 3, None) == ("click", {"sel": '[data-lc-ref="3"]'})
    assert build_action("fill", "2", "hi") == ("fill", {"sel": '[data-lc-ref="2"]', "val": "hi"})
    assert build_action("press", None, "Enter") == ("press", {"key": "Enter"})
    assert build_action("goto", None, "http://a") == ("goto", {"url": "http://a"})
    assert build_action("scroll", None, None) == ("scroll", {"px": 600})


def test_build_action_errors_are_actionable():
    assert "需要 ref" in build_action("click", None, None)
    assert "需要 value" in build_action("fill", 1, "")
    assert "不支持" in build_action("drag", 1, None)


def test_parse_snapshot_tolerates_junk():
    assert parse_snapshot('{"items": []}') == {"items": []}
    assert parse_snapshot("not json") == {}
    assert parse_snapshot(None) == {}


def test_snapshot_js_is_iife():
    s = SNAPSHOT_JS.strip()
    assert s.startswith("(() =>") and s.endswith(")()")
    assert "%(" not in s
