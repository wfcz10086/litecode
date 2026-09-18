import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "litecodeext" / "web_assets"


def test_zh_json_valid():
    with open(ASSETS / "messages.zh.json", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert len(data) >= 25


def test_en_json_valid():
    with open(ASSETS / "messages.en.json", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert len(data) >= 25


def test_zh_en_keys_match():
    with open(ASSETS / "messages.zh.json", encoding="utf-8") as f:
        zh = json.load(f)
    with open(ASSETS / "messages.en.json", encoding="utf-8") as f:
        en = json.load(f)
    zh_keys = set(zh.keys())
    en_keys = set(en.keys())
    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"keys missing in en: {only_zh}"
    assert not only_en, f"keys missing in zh: {only_en}"


def test_critical_keys_exist():
    with open(ASSETS / "messages.zh.json", encoding="utf-8") as f:
        zh = json.load(f)
    critical = [
        "login.password", "login.sign_in",
        "nav.memory", "nav.timer", "nav.dag", "nav.settings",
        "btn.new_chat", "status.loading",
    ]
    missing = [k for k in critical if k not in zh]
    assert not missing, f"missing: {missing}"


def test_i18n_js_exists():
    js = ASSETS / "i18n.js"
    assert js.exists()
    txt = js.read_text(encoding="utf-8")
    for fn in ["window.I18N", "setLocale", "applyDOM", "t("]:
        assert fn in txt, f"i18n.js missing: {fn}"


def test_html_loads_i18n_js():
    html = (ASSETS.parent / "web_ui.html").read_text(encoding="utf-8")
    assert "/assets/i18n.js" in html or "i18n.js" in html


def test_html_has_data_i18n_attributes():
    html = (ASSETS.parent / "web_ui.html").read_text(encoding="utf-8")
    count = html.count('data-i18n=')
    assert count >= 5, f"only {count} data-i18n attrs"
