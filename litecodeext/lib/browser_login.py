"""
browser_login.py — 自动登录工具 (P34-d-1 真集成)
================================================
goto → extract_form → 智能匹配 selector → fill → click → check_state.

依赖: browser_server (Node + puppeteer @ :19000), 由 core/browser_search 自动起.
密码型登录 (LiteCode WebUI) 和用户名+密码型 (大多 SaaS) 都覆盖.

attempt_login(workspace, url, username, password, ...) -> dict
"""
from __future__ import annotations
import time
from typing import Optional

from core.browser_search import _post_act, _ensure_browser_server, _save_b64_png


_PWD_TYPES = {"password"}
_USER_TYPES = {"email", "text"}
_USER_HINTS = ("user", "email", "login", "name", "account", "phone", "mobile", "uid")
_SUBMIT_HINTS = ("登录", "sign in", "login", "submit", "确定", "进入")


def _pick_selectors(fields: list, prefer_user_hint: Optional[str] = None) -> dict:
    """从 extract_form 返回的 fields 里挑出 user / pwd selector.

    返回 {"user": sel?, "pwd": sel?} — 缺哪个就 None.
    密码挑 type=password 第一个; 用户名按 type=email>text + 关键词命中,
    未命中关键词时退回最近一个 text 字段.
    """
    user_sel: Optional[str] = None
    pwd_sel: Optional[str] = None
    last_text_sel: Optional[str] = None

    for f in fields:
        ftype = (f.get("type") or "").lower()
        fid = f.get("id") or ""
        fname = f.get("name") or ""
        fph = (f.get("placeholder") or "").lower()
        sel = f"#{fid}" if fid else (f"[name=\"{fname}\"]" if fname else None)
        if not sel:
            continue

        if ftype in _PWD_TYPES and pwd_sel is None:
            pwd_sel = sel
            continue
        if ftype in _USER_TYPES:
            hit = any(h in fid.lower() or h in fname.lower() or h in fph for h in _USER_HINTS)
            if prefer_user_hint and prefer_user_hint.lower() in (fid + fname + fph).lower():
                hit = True
            if hit and user_sel is None:
                user_sel = sel
            elif ftype == "text" and last_text_sel is None:
                last_text_sel = sel

    if user_sel is None and last_text_sel is not None:
        user_sel = last_text_sel

    return {"user": user_sel, "pwd": pwd_sel}


def attempt_login(workspace, url: str, username: str = "", password: str = "",
                  domain: Optional[str] = None, timeout: int = 30,
                  user_hint: Optional[str] = None,
                  submit_selector: Optional[str] = None,
                  session: str = "login") -> dict:
    """打开 url, 自动找登录表单填值并提交.

    返回 {ok, msg, url, screenshot, fields_filled, url_changed, err_text, selectors}.
    """
    if not _ensure_browser_server(timeout=10.0):
        return {"ok": False, "msg": "browser_server 未起 (19000)", "url": url,
                "screenshot": None, "fields_filled": 0}

    # 1. goto
    r1 = _post_act("goto", {"url": url}, session=session, wait_ms=1500,
                   want_shot=False, timeout=float(timeout))
    if not r1.get("ok"):
        return {"ok": False, "msg": f"goto 失败: {r1.get('msg')}", "url": url,
                "screenshot": None, "fields_filled": 0}
    url_after_goto = r1.get("url") or url

    # 2. extract_form → 选 selector
    r2 = _post_act("extract_form", {}, session=session, wait_ms=400, want_shot=False)
    fields = (r2.get("data") or {}).get("fields") or []
    sels = _pick_selectors(fields, prefer_user_hint=user_hint)
    if sels["pwd"] is None:
        return {"ok": False, "msg": f"未找到 password 字段 (form fields={len(fields)})",
                "url": url_after_goto, "screenshot": None, "fields_filled": 0,
                "selectors": sels}

    # 3. fill_form
    to_fill: dict = {}
    if sels["user"] and username:
        to_fill[sels["user"]] = username
    to_fill[sels["pwd"]] = password
    r3 = _post_act("fill_form", {"fields": to_fill}, session=session,
                   wait_ms=200, want_shot=False)
    filled = 0
    msg3 = r3.get("msg", "")
    if "填充" in msg3:
        try:
            filled = int(msg3.split()[1].split("/")[0])
        except Exception:
            filled = len(to_fill)

    # 4. click submit — 优先按文本找登录按钮 (click_text), 失败退回 button[type=submit]
    sub_via_text: Optional[str] = None
    if not submit_selector:
        for hint in _SUBMIT_HINTS:
            rfind = _post_act("find_element", {"text": hint}, session=session,
                              wait_ms=100, want_shot=False)
            if rfind.get("ok"):
                sub_via_text = hint
                break

    if submit_selector:
        r4 = _post_act("click", {"sel": submit_selector}, session=session,
                       wait_ms=2000, want_shot=False)
    elif sub_via_text:
        r4 = _post_act("click_text", {"text": sub_via_text}, session=session,
                       wait_ms=2000, want_shot=False)
    else:
        r4 = _post_act("click", {"sel": "button[type=submit]"}, session=session,
                       wait_ms=2000, want_shot=False)

    if not r4.get("ok"):
        return {"ok": False, "msg": f"click submit 失败: {r4.get('msg')}",
                "url": url_after_goto, "screenshot": None, "fields_filled": filled,
                "selectors": sels}

    # 5. wait + check_state — 等 URL 变 / 主 UI 元素出现
    time.sleep(1.0)
    r5 = _post_act("smart_wait", {"timeout": 5000}, session=session,
                   wait_ms=400, want_shot=True)
    url_after = r5.get("url") or url_after_goto
    url_changed = url_after.rstrip("/") != url_after_goto.rstrip("/")

    shot_path = None
    b64 = r5.get("b64")
    if b64:
        shot_path = _save_b64_png(b64, prefix="login")

    # 检查错误文本 — body 含 "密码错误" / "incorrect" 视为失败
    r6 = _post_act("get_text", {"sel": "body"}, session=session,
                   wait_ms=100, want_shot=False)
    raw6 = r6.get("data")
    if isinstance(raw6, dict):
        body_text = (raw6.get("text") or "").lower()
    elif isinstance(raw6, str):
        body_text = raw6.lower()
    else:
        body_text = ""
    err_hits = [w for w in ("密码错误", "incorrect", "invalid password",
                            "登录失败", "login failed") if w in body_text]
    ok = (not err_hits) and (url_changed or filled >= 1)

    return {
        "ok": ok,
        "msg": ("登录成功" if ok
                else ("登录可能失败: " + ", ".join(err_hits or ["URL 未变化"]))),
        "url": url_after,
        "url_changed": url_changed,
        "screenshot": shot_path,
        "fields_filled": filled,
        "selectors": sels,
        "err_text": err_hits[0] if err_hits else "",
    }
