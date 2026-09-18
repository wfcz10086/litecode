from pathlib import Path


def test_no_core_english_strings_in_html():
    p = Path(__file__).resolve().parent.parent / "litecodeext/web_ui.html"
    txt = p.read_text(encoding="utf-8")
    forbidden = [
        ">Password<", "Sign In", "Incorrect password",
        ">Token Stats<", "Token Usage", ">Memory<", ">WeChat<",
        ">Timer<", ">Workspace<", ">Desktop<", ">Projects<", ">Settings<",
        "checking...", ">New Chat<", ">Stop<", ">Export<",
        "Loading...", "Select a session.",
        "AI-powered assistant with tools",
    ]
    found = [s for s in forbidden if s in txt]
    assert not found, f"Still contains English: {found}"


def test_brand_name_preserved():
    p = Path(__file__).resolve().parent.parent / "litecodeext/web_ui.html"
    txt = p.read_text(encoding="utf-8")
    assert "LiteCode" in txt, "Brand LiteCode lost"


def test_chinese_present():
    p = Path(__file__).resolve().parent.parent / "litecodeext/web_ui.html"
    txt = p.read_text(encoding="utf-8")
    expected_zh = ["密码", "登录", "用量统计", "记忆", "微信",
                   "定时任务", "工作区", "桌面", "项目", "设置",
                   "新建会话", "加载中"]
    missing = [s for s in expected_zh if s not in txt]
    assert not missing, f"Missing Chinese: {missing}"
