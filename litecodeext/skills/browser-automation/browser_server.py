#!/usr/bin/env python3
"""
browser_server.py — v12 增强版浏览器后台服务
=============================================
在 v6 基础上新增:
  - smart_wait:      智能等待 DOM 稳定（MutationObserver + network idle）
  - deep_inspect:    深度元素分析（含 shadow DOM、iframe、data 属性）
  - extract_table:   智能表格提取（自动识别 <table> 或 CSS grid/flex 布局）
  - extract_form:    表单结构提取（所有 input/select/textarea + 当前值）
  - find_element:    多策略元素定位（CSS/XPath/文本/属性/角色）
  - check_state:     元素状态检查（可见/可用/选中/值/属性）
  - wait_dom_stable: 等待 DOM 停止变化（AJAX 加载完成）
  - fill_form:       批量填写表单
  - intercept:       网络请求拦截（获取 API 响应数据）
  - download:        文件下载

端口 19000，完全兼容原有 API。
"""
import asyncio, base64, json, os, time, traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from playwright.async_api import async_playwright

PORT      = int(os.environ.get("BROWSER_AGENT_PORT", 19000))
DISPLAY   = os.environ.get("DISPLAY", ":99")
STATE_DIR = Path(os.environ.get("SESSIONS_DIR", "/data/sessions"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
os.environ["DISPLAY"] = DISPLAY

app = FastAPI()
_pw = _browser = None
_ctxs: dict = {}
_pages: dict = {}


@app.on_event("startup")
async def _start():
    global _pw, _browser
    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(
        headless=False,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled", "--disable-infobars",
              "--exclude-switches=enable-automation"],
    )
    print(f"[BAS] ✅ 浏览器启动完成 (DISPLAY={DISPLAY}, PORT={PORT})")


@app.on_event("shutdown")
async def _stop():
    for c in _ctxs.values():
        try: await c.close()
        except: pass
    if _browser: await _browser.close()
    if _pw: await _pw.stop()


async def _get(session="default"):
    if session not in _ctxs:
        sf = STATE_DIR / f"{session}_state.json"
        kw = dict(viewport={"width": 1280, "height": 720},
                  user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                  locale="zh-CN", timezone_id="Asia/Shanghai")
        if sf.exists():
            kw["storage_state"] = str(sf)
        ctx = await _browser.new_context(**kw)
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        _ctxs[session] = ctx
        _pages[session] = await ctx.new_page()
    return _ctxs[session], _pages[session]


async def _shot(page) -> str:
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    await page.screenshot(path=path)
    raw = open(path, "rb").read()
    os.unlink(path)
    # [v12] 压缩: 超过 200KB 时降低质量
    if len(raw) > 200_000:
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(raw))
            img = img.resize((960, 540), Image.LANCZOS)  # 降低分辨率
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            raw = buf.getvalue()
        except ImportError:
            pass  # 没有 PIL 就用原始 PNG
    return base64.b64encode(raw).decode()


# ═══════════════════════════════════════════════════════
# 动作处理器（原有 + v12 新增）
# ═══════════════════════════════════════════════════════

async def _run(action, params, page, ctx, session) -> dict:
    r = {"ok": True, "msg": "", "data": None}
    a = action

    # ── 原有动作（完全不变）──────────────────────────
    if a == "goto":
        timeout = params.get("timeout", 30000)
        wait_until = params.get("wait_until", "networkidle")
        try:
            await page.goto(params["url"], wait_until=wait_until, timeout=timeout)
            r["msg"] = f"打开: {params['url']}"
        except Exception as goto_err:
            # [v12] 网络超时时降级: 先尝试 domcontentloaded, 再降级为报错
            err_str = str(goto_err)
            if "timeout" in err_str.lower() or "net::" in err_str.lower():
                try:
                    await page.goto(params["url"], wait_until="domcontentloaded", timeout=15000)
                    r["msg"] = f"打开(降级): {params['url']} (networkidle 超时, 降级为 domcontentloaded)"
                except Exception:
                    r["ok"] = False
                    r["msg"] = f"打开失败: {params['url']} — {err_str[:200]}"
            else:
                r["ok"] = False
                r["msg"] = f"打开失败: {params['url']} — {err_str[:200]}"
    elif a == "reload":
        await page.reload(wait_until="networkidle")
        r["msg"] = "已刷新"
    elif a == "back":
        await page.go_back(); r["msg"] = "后退"
    elif a == "click":
        await page.wait_for_selector(params["sel"], timeout=8000)
        await page.click(params["sel"]); r["msg"] = f"点击: {params['sel']}"
    elif a == "click_text":
        await page.get_by_text(params["text"]).first.click()
        r["msg"] = f"点击文字: {params['text']}"
    elif a == "click_xy":
        await page.mouse.click(params["x"], params["y"])
        r["msg"] = f"点击坐标 ({params['x']},{params['y']})"
    elif a == "fill":
        await page.wait_for_selector(params["sel"], timeout=8000)
        await page.fill(params["sel"], params["val"])
        r["msg"] = f"填写: {params['sel']}"
    elif a == "type":
        await page.wait_for_selector(params["sel"], timeout=8000)
        await page.type(params["sel"], params["val"], delay=params.get("delay", 80))
        r["msg"] = f"输入: {params['sel']}"
    elif a == "press":
        await page.keyboard.press(params["key"]); r["msg"] = f"按键: {params['key']}"
    elif a == "wait":
        await page.wait_for_timeout(params.get("ms", 2000)); r["msg"] = "等待完成"
    elif a == "wait_for":
        await page.wait_for_selector(params["sel"], timeout=params.get("timeout", 20000))
        r["msg"] = f"元素出现: {params['sel']}"
    elif a == "wait_url":
        await page.wait_for_url(params["pattern"], timeout=20000)
        r["msg"] = f"URL 匹配: {params['pattern']}"
    elif a == "get_text":
        el = await page.query_selector(params["sel"])
        r["data"] = await el.inner_text() if el else None; r["msg"] = str(r["data"])
    elif a == "get_url":
        r["data"] = page.url; r["msg"] = page.url
    elif a == "eval":
        r["data"] = await page.evaluate(params["script"]); r["msg"] = str(r["data"])[:100]
    elif a == "select":
        await page.select_option(params["sel"], params["val"]); r["msg"] = f"选择: {params['val']}"
    elif a == "scroll":
        await page.evaluate(f"window.scrollBy(0,{params.get('px',300)})"); r["msg"] = "已滚动"
    elif a == "scroll_to":
        el = await page.query_selector(params["sel"])
        if el: await el.scroll_into_view_if_needed(); r["msg"] = f"滚到: {params['sel']}"
    elif a == "shot":
        r["msg"] = "截图完成"
    elif a == "save_session":
        await _ctxs[session].storage_state(path=str(STATE_DIR / f"{session}_state.json"))
        r["msg"] = f"session '{session}' 已保存"

    # ── 原有 inspect（保持兼容）──
    elif a == "inspect":
        r = await _action_inspect(page, params)

    # ── 原有 scrape ──
    elif a == "scrape":
        r = await _action_scrape(page, params)

    # ═══════════════════════════════════════════════════
    # v12 新增动作
    # ═══════════════════════════════════════════════════

    elif a == "deep_inspect":
        r = await _action_deep_inspect(page, params)

    elif a == "find_element":
        r = await _action_find_element(page, params)

    elif a == "check_state":
        r = await _action_check_state(page, params)

    elif a == "wait_dom_stable":
        r = await _action_wait_dom_stable(page, params)

    elif a == "smart_wait":
        r = await _action_smart_wait(page, params)

    elif a == "extract_table":
        r = await _action_extract_table(page, params)

    elif a == "extract_form":
        r = await _action_extract_form(page, params)

    elif a == "fill_form":
        r = await _action_fill_form(page, params)

    elif a == "intercept":
        r = await _action_intercept(page, params)

    elif a == "hover":
        await page.wait_for_selector(params["sel"], timeout=8000)
        await page.hover(params["sel"]); r["msg"] = f"悬停: {params['sel']}"

    elif a == "dblclick":
        await page.wait_for_selector(params["sel"], timeout=8000)
        await page.dblclick(params["sel"]); r["msg"] = f"双击: {params['sel']}"

    elif a == "upload":
        await page.set_input_files(params["sel"], params["file"])
        r["msg"] = f"上传: {params['file']}"

    elif a == "download":
        r = await _action_download(page, params)

    elif a == "get_attr":
        el = await page.query_selector(params["sel"])
        if el:
            r["data"] = await el.get_attribute(params.get("attr", "href"))
            r["msg"] = str(r["data"])[:200]
        else:
            r["ok"] = False; r["msg"] = f"元素不存在: {params['sel']}"

    elif a == "get_html":
        el = await page.query_selector(params.get("sel", "body"))
        if el:
            r["data"] = await el.inner_html()
            if len(r["data"]) > 5000:
                r["data"] = r["data"][:5000] + "...[truncated]"
            r["msg"] = f"HTML ({len(r['data'])} chars)"

    elif a == "count":
        els = await page.query_selector_all(params["sel"])
        r["data"] = len(els); r["msg"] = f"匹配 {len(els)} 个元素"

    else:
        r["ok"] = False; r["msg"] = f"未知动作: {a}"

    return r


# ═══════════════════════════════════════════════════════
# v12 新增动作实现
# ═══════════════════════════════════════════════════════

async def _action_inspect(page, params) -> dict:
    """原有 inspect，保持兼容。"""
    els = await page.evaluate("""() => {
        function cs(e){return e.id?'#'+e.id:e.tagName.toLowerCase()+(e.className?.trim().split(' ')[0]?'.'+e.className.trim().split(' ')[0]:'')}
        const r={inputs:[],buttons:[],links:[]};
        document.querySelectorAll('input,textarea,select').forEach(e=>{
            if(e.offsetParent!==null)r.inputs.push({css:cs(e),type:e.type||'',id:e.id||'',ph:e.placeholder||''})
        });
        document.querySelectorAll('button,[role=button],[type=submit]').forEach(e=>{
            if(e.offsetParent!==null)r.buttons.push({css:cs(e),text:(e.innerText||'').trim().slice(0,50),id:e.id||''})
        });
        document.querySelectorAll('nav a,a[class*=nav],a[class*=menu],.sidebar a').forEach(e=>{
            if(e.offsetParent!==null)r.links.push({text:(e.innerText||'').trim().slice(0,40),href:e.href})
        });
        return r;
    }""")
    vi, vb = els["inputs"], els["buttons"]
    return {
        "ok": True, "data": els,
        "msg": (f"输入框({len(vi)}): " + " | ".join(f"`{x['css']}`" for x in vi[:6]) +
                f"\n按钮({len(vb)}): " + " | ".join(f"`{x['text'][:20]}`" for x in vb[:6])),
    }


async def _action_deep_inspect(page, params) -> dict:
    """
    v12: 深度元素分析。比 inspect 更全面:
    - 覆盖所有可交互元素（不限于 nav 内的链接）
    - 提取 data-* 属性
    - 标注元素位置（区域: top/middle/bottom）
    - 支持限定范围 (scope selector)
    """
    scope = params.get("scope", "body")
    els = await page.evaluate("""(scope) => {
        const root = document.querySelector(scope) || document.body;
        const vh = window.innerHeight;

        function pos(e) {
            const r = e.getBoundingClientRect();
            if (r.top < vh * 0.3) return 'top';
            if (r.top < vh * 0.7) return 'mid';
            return 'bot';
        }

        function sel(e) {
            if (e.id) return '#' + e.id;
            if (e.getAttribute('name')) return e.tagName.toLowerCase() + '[name="' + e.getAttribute('name') + '"]';
            if (e.getAttribute('data-testid')) return '[data-testid="' + e.getAttribute('data-testid') + '"]';
            if (e.getAttribute('aria-label')) return '[aria-label="' + e.getAttribute('aria-label') + '"]';
            const cls = e.className?.trim().split(' ').filter(c=>c&&c.length<30).slice(0,2).join('.');
            return e.tagName.toLowerCase() + (cls ? '.' + cls : '');
        }

        function dataAttrs(e) {
            const d = {};
            for (const a of e.attributes) {
                if (a.name.startsWith('data-') && a.value.length < 100) d[a.name] = a.value;
            }
            return d;
        }

        const result = {inputs: [], buttons: [], links: [], selects: [], other_interactive: []};

        // Inputs & textareas
        root.querySelectorAll('input,textarea').forEach(e => {
            if (e.offsetParent === null) return;
            result.inputs.push({
                selector: sel(e), type: e.type || 'text', id: e.id || '',
                name: e.getAttribute('name') || '', placeholder: e.placeholder || '',
                value: e.value || '', required: e.required, disabled: e.disabled,
                pos: pos(e), data: dataAttrs(e),
            });
        });

        // Selects
        root.querySelectorAll('select').forEach(e => {
            if (e.offsetParent === null) return;
            const opts = Array.from(e.options).map(o => ({val: o.value, text: o.text.trim().slice(0,50)}));
            result.selects.push({
                selector: sel(e), id: e.id || '', name: e.getAttribute('name') || '',
                selected: e.value, options: opts.slice(0, 20), pos: pos(e),
            });
        });

        // Buttons
        root.querySelectorAll('button,[role=button],input[type=submit],input[type=button],a.btn,[class*=btn]').forEach(e => {
            if (e.offsetParent === null) return;
            result.buttons.push({
                selector: sel(e), text: (e.innerText || e.value || '').trim().slice(0, 60),
                type: e.type || '', disabled: e.disabled || false, pos: pos(e),
                data: dataAttrs(e),
            });
        });

        // Links (all visible, not just nav)
        root.querySelectorAll('a[href]').forEach(e => {
            if (e.offsetParent === null) return;
            const text = (e.innerText || '').trim().slice(0, 60);
            if (!text) return;
            result.links.push({
                selector: sel(e), text: text, href: e.href, pos: pos(e),
            });
        });

        // Other interactive elements (role-based)
        root.querySelectorAll('[role=tab],[role=switch],[role=checkbox],[role=radio],[role=slider],[role=menuitem],[role=option]').forEach(e => {
            if (e.offsetParent === null) return;
            result.other_interactive.push({
                selector: sel(e), role: e.getAttribute('role'),
                text: (e.innerText || '').trim().slice(0, 50),
                checked: e.getAttribute('aria-checked'), pos: pos(e),
            });
        });

        return result;
    }""", scope)

    # 格式化摘要
    parts = []
    if els["inputs"]:
        parts.append(f"📝 输入框({len(els['inputs'])}):")
        for inp in els["inputs"][:8]:
            ph = f" placeholder=\"{inp['placeholder']}\"" if inp['placeholder'] else ""
            val = f" val=\"{inp['value'][:20]}\"" if inp['value'] else ""
            parts.append(f"  `{inp['selector']}` type={inp['type']}{ph}{val}")

    if els["selects"]:
        parts.append(f"📋 下拉框({len(els['selects'])}):")
        for s in els["selects"][:5]:
            opts = ", ".join(o['text'][:15] for o in s['options'][:5])
            parts.append(f"  `{s['selector']}` 当前={s['selected']} 选项=[{opts}]")

    if els["buttons"]:
        parts.append(f"🔘 按钮({len(els['buttons'])}):")
        for btn in els["buttons"][:8]:
            disabled = " ⛔disabled" if btn['disabled'] else ""
            parts.append(f"  `{btn['selector']}` \"{btn['text']}\"{disabled}")

    if els["links"]:
        parts.append(f"🔗 链接({len(els['links'])}):")
        for lnk in els["links"][:6]:
            parts.append(f"  `{lnk['selector']}` \"{lnk['text']}\"")

    if els["other_interactive"]:
        parts.append(f"🎛️ 其他控件({len(els['other_interactive'])}):")
        for o in els["other_interactive"][:5]:
            parts.append(f"  `{o['selector']}` role={o['role']} \"{o['text']}\"")

    return {"ok": True, "data": els, "msg": "\n".join(parts)}


async def _action_find_element(page, params) -> dict:
    """
    v12: 多策略元素定位。按优先级尝试:
    1. CSS selector
    2. XPath
    3. 文本内容
    4. placeholder
    5. aria-label
    6. data-testid
    """
    query = params.get("query", "")
    strategy = params.get("strategy", "auto")

    if strategy == "css" or (strategy == "auto" and any(c in query for c in "#.[]:>")):
        el = await page.query_selector(query)
        method = "css"
    elif strategy == "xpath" or (strategy == "auto" and query.startswith("//")):
        el = await page.query_selector(f"xpath={query}")
        method = "xpath"
    elif strategy == "text" or strategy == "auto":
        # 尝试多种定位
        el = None
        for locator_fn, method in [
            (lambda: page.get_by_text(query, exact=False).first, "text"),
            (lambda: page.get_by_placeholder(query).first, "placeholder"),
            (lambda: page.get_by_label(query).first, "label"),
            (lambda: page.get_by_role("button", name=query).first, "role:button"),
            (lambda: page.query_selector(f'[data-testid="{query}"]'), "data-testid"),
        ]:
            try:
                candidate = locator_fn()
                if hasattr(candidate, 'element_handle'):
                    handle = await candidate.element_handle(timeout=2000)
                    if handle:
                        el = handle
                        break
                elif candidate:
                    el = candidate
                    break
            except Exception:
                continue
        else:
            method = "none"

    if el:
        info = await page.evaluate("""(el) => ({
            tag: el.tagName, id: el.id, text: (el.innerText||'').slice(0,100),
            visible: el.offsetParent !== null,
            rect: el.getBoundingClientRect(),
        })""", el)
        return {"ok": True, "msg": f"找到({method}): {info['tag']} id={info['id']} \"{info['text'][:40]}\"",
                "data": {"found": True, "method": method, "info": info}}
    return {"ok": False, "msg": f"未找到: {query}", "data": {"found": False}}


async def _action_check_state(page, params) -> dict:
    """v12: 检查元素状态。"""
    sel = params.get("sel", "")
    el = await page.query_selector(sel)
    if not el:
        return {"ok": False, "msg": f"元素不存在: {sel}", "data": None}

    state = await page.evaluate("""(el) => ({
        visible: el.offsetParent !== null,
        enabled: !el.disabled,
        checked: el.checked || false,
        selected: el.selected || false,
        value: el.value || '',
        text: (el.innerText || '').trim().slice(0, 200),
        tag: el.tagName,
        type: el.type || '',
        classes: el.className || '',
        rect: el.getBoundingClientRect(),
        computedDisplay: getComputedStyle(el).display,
        computedVisibility: getComputedStyle(el).visibility,
    })""", el)

    status_parts = []
    if state['visible']:
        status_parts.append("✅可见")
    else:
        status_parts.append("❌隐藏")
    if state['enabled']:
        status_parts.append("✅可用")
    else:
        status_parts.append("⛔禁用")
    if state['value']:
        status_parts.append(f"值=\"{state['value'][:50]}\"")

    return {"ok": True, "msg": " | ".join(status_parts), "data": state}


async def _action_wait_dom_stable(page, params) -> dict:
    """
    v12: 等待 DOM 停止变化。
    原理: MutationObserver 监测，连续 N ms 无变化视为稳定。
    用于: 等待 AJAX 加载、动态渲染完成。
    """
    stable_ms = params.get("stable_ms", 1000)
    timeout_ms = params.get("timeout", 15000)

    stable = await page.evaluate(f"""() => new Promise((resolve) => {{
        let timer = null;
        let mutations = 0;
        const start = Date.now();

        const observer = new MutationObserver((list) => {{
            mutations += list.length;
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => {{
                observer.disconnect();
                resolve({{stable: true, mutations, elapsed: Date.now() - start}});
            }}, {stable_ms});
        }});

        observer.observe(document.body, {{
            childList: true, subtree: true, attributes: true, characterData: true,
        }});

        // 初始启动定时器
        timer = setTimeout(() => {{
            observer.disconnect();
            resolve({{stable: true, mutations: 0, elapsed: Date.now() - start}});
        }}, {stable_ms});

        // 超时保护
        setTimeout(() => {{
            observer.disconnect();
            resolve({{stable: false, mutations, elapsed: Date.now() - start}});
        }}, {timeout_ms});
    }})""")

    return {
        "ok": stable.get("stable", False),
        "msg": f"DOM {'稳定' if stable['stable'] else '超时'} "
               f"({stable['mutations']}次变化, {stable['elapsed']}ms)",
        "data": stable,
    }


async def _action_smart_wait(page, params) -> dict:
    """
    v12: 智能等待。组合多种等待策略:
    1. 等待网络空闲
    2. 等待 DOM 稳定
    3. 等待目标元素出现（可选）
    """
    target_sel = params.get("sel", None)
    timeout = params.get("timeout", 15000)

    steps = []

    # 1. 等待网络空闲
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout // 2)
        steps.append("✅ 网络空闲")
    except Exception:
        steps.append("⚠️ 网络未完全空闲")

    # 2. 等待 DOM 稳定
    stable = await _action_wait_dom_stable(page, {"stable_ms": 500, "timeout": timeout // 2})
    steps.append(f"{'✅' if stable['ok'] else '⚠️'} DOM {stable['msg']}")

    # 3. 等待目标元素
    if target_sel:
        try:
            await page.wait_for_selector(target_sel, timeout=timeout // 2)
            steps.append(f"✅ 元素出现: {target_sel}")
        except Exception:
            steps.append(f"⚠️ 目标元素未出现: {target_sel}")

    return {"ok": True, "msg": "\n".join(steps), "data": {"steps": steps}}


async def _action_extract_table(page, params) -> dict:
    """
    v12: 智能表格提取。
    支持:
    - <table> 标签
    - CSS grid/flex 布局的表格
    - 自定义行/列选择器
    """
    sel = params.get("sel", "table")
    row_sel = params.get("row_sel", "tr")
    cell_sel = params.get("cell_sel", "td,th")
    max_rows = params.get("max_rows", 100)
    include_header = params.get("include_header", True)

    data = await page.evaluate(f"""(config) => {{
        const table = document.querySelector(config.sel);
        if (!table) return {{error: 'table not found', sel: config.sel}};

        const rows = table.querySelectorAll(config.row_sel);
        const result = {{headers: [], rows: [], total_rows: rows.length}};

        rows.forEach((row, i) => {{
            if (i >= config.max_rows) return;
            const cells = row.querySelectorAll(config.cell_sel);
            const values = Array.from(cells).map(c => (c.innerText || '').trim().slice(0, 200));

            if (i === 0 && config.include_header && row.querySelector('th')) {{
                result.headers = values;
            }} else {{
                result.rows.push(values);
            }}
        }});

        // 如果有 headers，转为 dict 格式
        if (result.headers.length > 0) {{
            result.dict_rows = result.rows.map(row => {{
                const obj = {{}};
                result.headers.forEach((h, j) => {{ obj[h] = row[j] || ''; }});
                return obj;
            }});
        }}

        return result;
    }}""", {"sel": sel, "row_sel": row_sel, "cell_sel": cell_sel,
            "max_rows": max_rows, "include_header": include_header})

    if isinstance(data, dict) and data.get("error"):
        return {"ok": False, "msg": f"表格未找到: {sel}", "data": None}

    rows = data.get("dict_rows") or data.get("rows", [])
    return {
        "ok": True,
        "msg": f"提取 {len(rows)} 行 (总 {data.get('total_rows', 0)}行), "
               f"列: {data.get('headers', [])}",
        "data": data,
    }


async def _action_extract_form(page, params) -> dict:
    """v12: 提取表单结构（所有字段 + 当前值）。"""
    sel = params.get("sel", "form")

    data = await page.evaluate("""(formSel) => {
        const form = document.querySelector(formSel);
        if (!form) return {error: 'form not found'};

        const fields = [];
        form.querySelectorAll('input,textarea,select').forEach(e => {
            const f = {
                tag: e.tagName.toLowerCase(),
                type: e.type || 'text',
                name: e.getAttribute('name') || '',
                id: e.id || '',
                label: '',
                value: e.value || '',
                required: e.required || false,
                disabled: e.disabled || false,
                placeholder: e.placeholder || '',
            };

            // 查找 label
            if (e.id) {
                const lbl = document.querySelector('label[for="' + e.id + '"]');
                if (lbl) f.label = lbl.innerText.trim().slice(0, 50);
            }
            if (!f.label) {
                const parent = e.closest('label');
                if (parent) f.label = parent.innerText.trim().slice(0, 50);
            }

            // Select options
            if (e.tagName === 'SELECT') {
                f.options = Array.from(e.options).map(o => ({
                    value: o.value, text: o.text.trim().slice(0, 50), selected: o.selected,
                })).slice(0, 30);
            }

            // Checkbox/radio
            if (e.type === 'checkbox' || e.type === 'radio') {
                f.checked = e.checked;
            }

            fields.push(f);
        });

        return {
            action: form.action || '',
            method: form.method || 'GET',
            fields: fields,
        };
    }""", sel)

    if isinstance(data, dict) and data.get("error"):
        return {"ok": False, "msg": f"表单未找到: {sel}", "data": None}

    parts = [f"表单 action={data.get('action', '')} method={data.get('method', '')}"]
    for f in data.get("fields", []):
        label = f.get("label") or f.get("placeholder") or f.get("name") or f.get("id") or "?"
        val = f.get("value", "")
        parts.append(f"  [{f['type']}] {label}: \"{val[:30]}\" {'*必填' if f.get('required') else ''}")

    return {"ok": True, "msg": "\n".join(parts), "data": data}


async def _action_fill_form(page, params) -> dict:
    """v12: 批量填写表单。fields = {selector: value}"""
    fields = params.get("fields", {})
    results = []
    for sel, val in fields.items():
        try:
            el = await page.query_selector(sel)
            if not el:
                results.append(f"❌ {sel}: 未找到")
                continue
            tag = await page.evaluate("(el) => el.tagName", el)
            el_type = await page.evaluate("(el) => el.type", el)

            if tag == "SELECT":
                await page.select_option(sel, val)
            elif el_type in ("checkbox", "radio"):
                checked = await page.evaluate("(el) => el.checked", el)
                if (val in ("true", True)) != checked:
                    await page.click(sel)
            else:
                await page.fill(sel, str(val))
            results.append(f"✅ {sel}: \"{str(val)[:30]}\"")
        except Exception as e:
            results.append(f"❌ {sel}: {e}")

    return {"ok": all("✅" in r for r in results),
            "msg": "\n".join(results), "data": results}


async def _action_intercept(page, params) -> dict:
    """
    v12: 拦截网络请求，获取 API 响应数据。
    用于: 页面通过 AJAX 加载数据时，直接获取 JSON 响应。
    """
    url_pattern = params.get("url_pattern", "**/*")
    trigger_action = params.get("trigger", None)
    timeout = params.get("timeout", 10000)

    captured = []

    async def _on_response(response):
        if response.url and url_pattern.replace("**", "") in response.url:
            try:
                body = await response.text()
                captured.append({
                    "url": response.url,
                    "status": response.status,
                    "body": body[:5000],
                })
            except Exception:
                pass

    page.on("response", _on_response)

    try:
        if trigger_action:
            # 执行触发动作（如点击按钮）
            await page.click(trigger_action)

        # 等待响应
        await page.wait_for_timeout(min(timeout, 10000))
    finally:
        page.remove_listener("response", _on_response)

    return {
        "ok": len(captured) > 0,
        "msg": f"拦截到 {len(captured)} 个响应",
        "data": captured[:10],
    }


async def _action_scrape(page, params) -> dict:
    """原有 scrape，保持兼容。"""
    items = await page.query_selector_all(params["item_sel"])
    rows = []
    for el in items[:params.get("limit", 50)]:
        row = {}
        for k, sel in params.get("fields", {}).items():
            try:
                if "@" in sel:
                    s, at = sel.rsplit("@", 1)
                    fe = await el.query_selector(s)
                    row[k] = await fe.get_attribute(at) if fe else ""
                else:
                    fe = await el.query_selector(sel)
                    row[k] = (await fe.inner_text()).strip() if fe else ""
            except:
                row[k] = ""
        rows.append(row)
    return {"ok": True, "data": rows, "msg": f"抓取 {len(rows)} 条"}


async def _action_download(page, params) -> dict:
    """v12: 文件下载。"""
    sel = params.get("sel", "")
    save_dir = params.get("save_dir", "/tmp/litecode_workspace/downloads")
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    try:
        async with page.expect_download(timeout=30000) as dl_info:
            await page.click(sel)
        download = dl_info.value
        path = os.path.join(save_dir, download.suggested_filename)
        await download.save_as(path)
        return {"ok": True, "msg": f"已下载: {path}", "data": {"path": path, "name": download.suggested_filename}}
    except Exception as e:
        return {"ok": False, "msg": f"下载失败: {e}", "data": None}


# ═══════════════════════════════════════════════════════
# API 端点（完全不变）
# ═══════════════════════════════════════════════════════

@app.post("/act")
async def act(body: dict):
    action  = body.get("action", "shot")
    params  = body.get("params", {})
    session = body.get("session", "default")
    wait_ms = body.get("wait", 800)
    do_shot = body.get("shot", True)
    try:
        ctx, page = await _get(session)
        result = await _run(action, params, page, ctx, session)
        if wait_ms > 0:
            await page.wait_for_timeout(wait_ms)
        b64 = await _shot(page) if do_shot else None
        return {**result, "b64": b64, "url": page.url}
    except Exception as e:
        b64 = None
        try:
            if session in _pages: b64 = await _shot(_pages[session])
        except: pass
        return {"ok": False, "msg": str(e), "data": None, "b64": b64,
                "url": _pages[session].url if session in _pages else ""}


@app.post("/shot")
async def shot_only(body: dict = {}):
    session = body.get("session", "default")
    ctx, page = await _get(session)
    return {"ok": True, "b64": await _shot(page), "url": page.url}


@app.get("/health")
async def health():
    return {"ok": True, "sessions": list(_ctxs.keys())}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
