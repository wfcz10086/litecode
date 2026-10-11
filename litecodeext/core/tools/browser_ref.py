"""browser_ref.py — 编号式浏览器操作: 快照列出可交互元素 [n], 按编号点击/填写。

模型不用再写 Python 脚本、也不用猜 CSS 选择器: 先 browser_snapshot 看到
`[3] button "登录"`, 再 browser_act(ref=3, action="click")。

只用 browser_server 已有的通用动作 (eval/click/fill/select/hover/press/goto/back),
Node 版与 Python 版都能跑; 快照时给元素打 data-lc-ref 属性, 编号即选择器。
"""
from __future__ import annotations

import json

MAX_ITEMS = 150
TEXT_CHARS = 1500

# IIFE 字符串: puppeteer 的 evaluate(string) 按表达式求值, 不会调用函数字面量
SNAPSHOT_JS = r"""(() => {
  const MAX = %(max)d, TEXT = %(text)d;
  document.querySelectorAll('[data-lc-ref]').forEach(e => e.removeAttribute('data-lc-ref'));
  const SEL = 'a[href],button,input,textarea,select,summary,[contenteditable="true"],[onclick],' +
    '[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],' +
    '[role=option],[role=switch],[role=combobox],[role=textbox]';
  const vh = window.innerHeight, out = [];
  for (const el of document.querySelectorAll(SEL)) {
    if (out.length >= MAX) break;
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (el.type === 'hidden') continue;
    const n = out.length + 1;
    el.setAttribute('data-lc-ref', String(n));
    const txt = (el.innerText || '').replace(/\s+/g, ' ').trim();
    const label = el.labels && el.labels[0] ? el.labels[0].innerText.replace(/\s+/g, ' ').trim() : '';
    out.push({
      ref: n,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      type: el.getAttribute('type') || '',
      name: (el.getAttribute('aria-label') || label || (el.tagName === 'SELECT' ? '' : txt) ||
             el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') ||
             el.getAttribute('name') || '').slice(0, 60),
      value: !('value' in el) || el.tagName === 'BUTTON' || /^(checkbox|radio|submit|button)$/.test(el.type) ? ''
             : el.type === 'password' ? (el.value ? '***' : '') : String(el.value).slice(0, 40),
      checked: !!el.checked,
      disabled: !!el.disabled,
      href: el.getAttribute('href') || '',
      options: el.tagName === 'SELECT' ? Array.from(el.options).slice(0, 8).map(o => o.text.trim()) : [],
      in_view: r.bottom > 0 && r.top < vh,
    });
  }
  return JSON.stringify({title: document.title, url: location.href, items: out,
                         text: (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, TEXT)});
})()""" % {"max": MAX_ITEMS, "text": TEXT_CHARS}


def _kind(it: dict) -> str:
    if it.get("role"):
        return it["role"]
    tag, typ = it.get("tag", ""), it.get("type", "")
    if tag == "a":
        return "link"
    if tag == "input":
        return {"checkbox": "checkbox", "radio": "radio", "submit": "button", "button": "button",
                "file": "file"}.get(typ, "textbox")
    if tag == "textarea":
        return "textbox"
    return tag


def format_snapshot(snap: dict) -> str:
    """快照 dict → 给模型看的紧凑文本。"""
    lines = [f"页面: {snap.get('title') or '(无标题)'}", f"URL: {snap.get('url', '')}"]
    items = snap.get("items") or []
    if not items:
        lines.append("(没有可交互元素)")
    for it in items:
        s = f"[{it['ref']}] {_kind(it)}" + (f" \"{it['name']}\"" if it.get("name") else "")
        if it.get("value"):
            s += f" 值=\"{it['value']}\""
        if it.get("options"):
            s += " 选项=" + "/".join(it["options"])
        if it.get("checked"):
            s += " 已勾选"
        if it.get("disabled"):
            s += " 禁用"
        if it.get("href") and not it["href"].startswith("javascript"):
            s += f" → {it['href'][:80]}"
        if not it.get("in_view"):
            s += " (需滚动)"
        lines.append(s)
    if len(items) >= MAX_ITEMS:
        lines.append(f"…只列前 {MAX_ITEMS} 个")
    text = (snap.get("text") or "").strip()
    if text:
        lines += ["--- 页面文字 (节选) ---", text]
    lines.append("用 browser_act(ref=编号, action=...) 操作; 页面变化后编号会重排, 以最新快照为准。")
    return "\n".join(lines)


def ref_selector(ref) -> str:
    return f'[data-lc-ref="{int(ref)}"]'


def parse_snapshot(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


# action → (browser_server 动作, 是否需要 ref, 是否需要 value)
ACTIONS = {
    "click":  ("click", True, False),
    "fill":   ("fill", True, True),
    "select": ("select", True, True),
    "hover":  ("hover", True, False),
    "press":  ("press", False, True),
    "goto":   ("goto", False, True),
    "back":   ("back", False, False),
    "scroll": ("scroll", False, False),
}


def build_action(action: str, ref, value) -> tuple[str, dict] | str:
    """返回 (server 动作, params) 或错误文本。"""
    spec = ACTIONS.get(action)
    if not spec:
        return f"ERROR: 不支持的 action={action!r}, 可选: {', '.join(ACTIONS)}, close"
    srv, need_ref, need_val = spec
    if need_ref and ref in (None, ""):
        return f"ERROR: action={action} 需要 ref (先 browser_snapshot 拿编号)"
    if need_val and value in (None, ""):
        return f"ERROR: action={action} 需要 value"
    if srv == "goto":
        return srv, {"url": str(value)}
    if srv == "press":
        return srv, {"key": str(value)}
    if srv == "scroll":
        return srv, {"px": int(value or 600)}
    params = {"sel": ref_selector(ref)}
    if need_val:
        params["val"] = str(value)
    return srv, params
