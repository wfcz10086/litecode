#!/usr/bin/env node
/**
 * browser_server.js — Node + Puppeteer (复用容器内 google-chrome)
 * ==================================================================
 * 替换原 browser_server.py (Python + Playwright + 自带 chromium).
 *
 * 设计原则 (2026-05-25 用户拍板):
 *   1. **单例**: 全局 1 个 browser instance, 进程级别端口 19000 单监听
 *   2. **用完即关**: 每个 session 的 BrowserContext idle ≥ IDLE_MS (默认 5min)
 *      自动 close, 释放 chrome 进程
 *   3. **失败也清理**: action 异常路径同样走 ctx idle tracker
 *   4. **无 Python 依赖**: 不再 pip install playwright, 不下 chromium binary
 *
 * 复用容器内已装的 google-chrome (/usr/bin/google-chrome, Chrome 148.x).
 *
 * HTTP API (兼容老 Python 版, browser_search.py 不用改):
 *   POST /act    { action, params, session, wait, shot }
 *   POST /shot   { session }
 *   GET  /health
 *   POST /cleanup { session? }   — 主动关 session (用完即关)
 */

const express = require('express');
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');
const os = require('os');

// ── 配置 ────────────────────────────────────────────────────────
const PORT       = parseInt(process.env.BROWSER_AGENT_PORT || '19000', 10);
const CHROME_BIN = process.env.CHROME_BIN || '/usr/bin/google-chrome';
const DISPLAY    = process.env.DISPLAY || ':99';
const STATE_DIR  = process.env.SESSIONS_DIR || '/data/sessions';
const IDLE_MS    = parseInt(process.env.BROWSER_IDLE_MS || `${5 * 60 * 1000}`, 10);  // 5min
const HEADLESS   = (process.env.BROWSER_HEADLESS || 'false').toLowerCase() === 'true';

process.env.DISPLAY = DISPLAY;
if (!fs.existsSync(STATE_DIR)) fs.mkdirSync(STATE_DIR, { recursive: true });

// ── 全局单例 browser ────────────────────────────────────────────
let _browser = null;
const _ctxs  = new Map();   // session -> { ctx, page, lastUsed, idleTimer }

async function _getBrowser() {
  if (_browser && _browser.isConnected()) return _browser;
  _browser = await puppeteer.launch({
    executablePath: CHROME_BIN,
    headless: HEADLESS,
    args: [
      '--no-sandbox', '--disable-setuid-sandbox',
      '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled',
      '--disable-infobars', '--no-first-run', '--no-default-browser-check',
      '--disable-default-apps',
    ],
    defaultViewport: { width: 1280, height: 720 },
  });
  console.log(`[BAS-Node] ✅ 浏览器启动完成 (chrome=${CHROME_BIN}, headless=${HEADLESS}, PORT=${PORT})`);
  // 进程退出时清理
  _browser.on('disconnected', () => { _browser = null; _ctxs.clear(); });
  return _browser;
}

// ── 用完即关: idle timer ────────────────────────────────────────
function _refreshIdle(session) {
  const entry = _ctxs.get(session);
  if (!entry) return;
  if (entry.idleTimer) clearTimeout(entry.idleTimer);
  entry.lastUsed = Date.now();
  entry.idleTimer = setTimeout(() => _cleanupSession(session, 'idle-timeout'), IDLE_MS);
}

async function _cleanupSession(session, reason = 'manual') {
  const entry = _ctxs.get(session);
  if (!entry) return;
  if (entry.idleTimer) clearTimeout(entry.idleTimer);
  try { if (entry.page && !entry.page.isClosed()) await entry.page.close(); } catch (e) {}
  try { if (entry.ctx) await entry.ctx.close(); } catch (e) {}
  _ctxs.delete(session);
  console.log(`[BAS-Node] cleanup session=${session} reason=${reason} (active=${_ctxs.size})`);
}

// ── 按 session 取 ctx + page ────────────────────────────────────
async function _getSession(session) {
  if (_ctxs.has(session)) {
    _refreshIdle(session);
    return _ctxs.get(session);
  }
  const browser = await _getBrowser();
  // 用 incognito context 实现 session 隔离 (cookie/storage 分开)
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36');
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  });
  // storage_state 恢复 (cookie 持久化)
  const stateFile = path.join(STATE_DIR, `${session}_state.json`);
  if (fs.existsSync(stateFile)) {
    try {
      const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      if (Array.isArray(state.cookies)) await ctx.setCookie(...state.cookies);
    } catch (e) { console.log(`[BAS-Node] storage_state load failed: ${e.message}`); }
  }
  const entry = { ctx, page, lastUsed: Date.now(), idleTimer: null };
  _ctxs.set(session, entry);
  _refreshIdle(session);
  console.log(`[BAS-Node] new session=${session} (active=${_ctxs.size})`);
  return entry;
}

// ── 截图 (压缩超 200KB 的图) ────────────────────────────────────
async function _shot(page) {
  let buf = await page.screenshot({ type: 'png', fullPage: false });
  if (buf.length > 200_000) {
    // puppeteer 内建不能直接转 JPEG quality, 但可以走 type: 'jpeg', quality
    try {
      buf = await page.screenshot({ type: 'jpeg', quality: 70 });
    } catch (e) {}
  }
  return buf.toString('base64');
}

// ── 单 action 执行 ──────────────────────────────────────────────
async function _run(action, params, page, ctx, session) {
  const a = action;
  const r = { ok: true, msg: '', data: null };
  switch (a) {
    // ── 导航 ──
    case 'goto': {
      const timeout = params.timeout || 30000;
      const wait_until = params.wait_until || 'networkidle2';
      try {
        await page.goto(params.url, { waitUntil: wait_until, timeout });
        r.msg = `打开: ${params.url}`;
      } catch (e) {
        // 降级: 试 domcontentloaded
        try {
          await page.goto(params.url, { waitUntil: 'domcontentloaded', timeout: 15000 });
          r.msg = `打开(降级): ${params.url} (${wait_until} 超时, 降级 domcontentloaded)`;
        } catch (e2) {
          r.ok = false;
          r.msg = `打开失败: ${params.url} — ${String(e2).slice(0, 200)}`;
        }
      }
      break;
    }
    case 'reload':
      await page.reload({ waitUntil: 'networkidle2' });
      r.msg = '已刷新';
      break;
    case 'back':
      await page.goBack();
      r.msg = '后退';
      break;

    // ── 操作 ──
    case 'click':
      await page.waitForSelector(params.sel, { timeout: 8000 });
      await page.click(params.sel);
      r.msg = `点击: ${params.sel}`;
      break;
    case 'click_text': {
      const found = await page.evaluateHandle(
        (text) => {
          const xpath = `//*[contains(normalize-space(text()), ${JSON.stringify(text)})]`;
          const r = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
          return r.singleNodeValue;
        }, params.text
      );
      const el = found.asElement();
      if (el) { await el.click(); r.msg = `点击文字: ${params.text}`; }
      else { r.ok = false; r.msg = `未找到文字: ${params.text}`; }
      break;
    }
    case 'click_xy':
      await page.mouse.click(params.x, params.y);
      r.msg = `点击坐标 (${params.x},${params.y})`;
      break;
    case 'fill':
      await page.waitForSelector(params.sel, { timeout: 8000 });
      await page.focus(params.sel);
      // puppeteer 没有 fill, 用 evaluate 设 value 再 dispatch input
      await page.$eval(params.sel, (el, val) => {
        el.value = val;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }, params.val);
      r.msg = `填写: ${params.sel}`;
      break;
    case 'type':
      await page.waitForSelector(params.sel, { timeout: 8000 });
      await page.type(params.sel, params.val, { delay: params.delay || 80 });
      r.msg = `输入: ${params.sel}`;
      break;
    case 'press':
      await page.keyboard.press(params.key);
      r.msg = `按键: ${params.key}`;
      break;
    case 'hover':
      await page.waitForSelector(params.sel, { timeout: 8000 });
      await page.hover(params.sel);
      r.msg = `悬停: ${params.sel}`;
      break;
    case 'dblclick':
      await page.waitForSelector(params.sel, { timeout: 8000 });
      await page.click(params.sel, { clickCount: 2 });
      r.msg = `双击: ${params.sel}`;
      break;
    case 'select':
      await page.select(params.sel, params.val);
      r.msg = `选择: ${params.val}`;
      break;
    case 'upload':
      const el = await page.$(params.sel);
      if (el) { await el.uploadFile(params.file); r.msg = `上传: ${params.file}`; }
      else { r.ok = false; r.msg = `元素不存在: ${params.sel}`; }
      break;

    // ── 等待 ──
    case 'wait':
      await new Promise(res => setTimeout(res, params.ms || 2000));
      r.msg = '等待完成';
      break;
    case 'wait_for':
      await page.waitForSelector(params.sel, { timeout: params.timeout || 20000 });
      r.msg = `元素出现: ${params.sel}`;
      break;
    case 'wait_url':
      await page.waitForFunction(
        (pattern) => location.href.includes(pattern) || new RegExp(pattern).test(location.href),
        { timeout: 20000 }, params.pattern
      );
      r.msg = `URL 匹配: ${params.pattern}`;
      break;
    case 'smart_wait': {
      // 等容器 + 1s DOM 稳定
      const sel = params.sel;
      const timeout = params.timeout || 8000;
      try { await page.waitForSelector(sel, { timeout }); }
      catch (e) { r.ok = false; r.msg = `smart_wait timeout: ${sel}`; break; }
      await new Promise(res => setTimeout(res, 500));
      r.msg = `smart_wait OK: ${sel}`;
      break;
    }
    case 'wait_dom_stable': {
      const stable_ms = params.stable_ms || 800;
      const timeout   = params.timeout   || 8000;
      try {
        await page.waitForFunction(
          (stableMs) => {
            return new Promise((resolve) => {
              let timer = setTimeout(() => resolve(true), stableMs);
              const obs = new MutationObserver(() => {
                clearTimeout(timer);
                timer = setTimeout(() => { obs.disconnect(); resolve(true); }, stableMs);
              });
              obs.observe(document.body || document.documentElement,
                { childList: true, subtree: true, attributes: true });
              setTimeout(() => { obs.disconnect(); resolve(true); }, 6000);
            });
          },
          { timeout }, stable_ms
        );
      } catch (e) {}
      r.msg = 'DOM 稳定';
      break;
    }

    // ── 读取 ──
    case 'get_text': {
      const el2 = await page.$(params.sel);
      r.data = el2 ? (await page.evaluate(e => e.innerText, el2)) : null;
      r.msg = String(r.data || '').slice(0, 100);
      break;
    }
    case 'get_url':
      r.data = page.url();
      r.msg = page.url();
      break;
    case 'get_attr': {
      const el3 = await page.$(params.sel);
      if (el3) {
        r.data = await page.evaluate((e, at) => e.getAttribute(at), el3, params.attr || 'href');
        r.msg = String(r.data || '').slice(0, 200);
      } else { r.ok = false; r.msg = `元素不存在: ${params.sel}`; }
      break;
    }
    case 'get_html': {
      const sel = params.sel || 'body';
      const el4 = await page.$(sel);
      if (el4) {
        let html = await page.evaluate(e => e.innerHTML, el4);
        if (html.length > 5000) html = html.slice(0, 5000) + '...[truncated]';
        r.data = html;
        r.msg = `HTML (${html.length} chars)`;
      } else { r.ok = false; r.msg = `元素不存在: ${sel}`; }
      break;
    }
    case 'eval':
      try {
        r.data = await page.evaluate(params.script);
        r.msg = String(r.data).slice(0, 100);
      } catch (e) {
        r.ok = false;
        r.msg = `eval 失败: ${String(e).slice(0, 200)}`;
      }
      break;
    case 'count': {
      const els = await page.$$(params.sel);
      r.data = els.length;
      r.msg = `匹配 ${els.length} 个元素`;
      break;
    }

    // ── 滚动/截图/会话 ──
    case 'scroll':
      await page.evaluate((px) => window.scrollBy(0, px), params.px || 300);
      r.msg = '已滚动';
      break;
    case 'scroll_to': {
      const el5 = await page.$(params.sel);
      if (el5) {
        await page.evaluate(e => e.scrollIntoView({ behavior: 'smooth', block: 'center' }), el5);
        r.msg = `滚到: ${params.sel}`;
      }
      break;
    }
    case 'shot':
      r.msg = '截图完成';
      break;
    case 'save_session': {
      const stateFile2 = path.join(STATE_DIR, `${session}_state.json`);
      const cookies = await ctx.cookies();
      fs.writeFileSync(stateFile2, JSON.stringify({ cookies }, null, 2));
      r.msg = `session '${session}' 已保存`;
      break;
    }

    // ── scrape (browser_search 核心) ──
    case 'scrape': {
      const items = await page.$$(params.item_sel);
      const limit = params.limit || 50;
      const rows = [];
      for (const it of items.slice(0, limit)) {
        const row = {};
        for (const [k, sel] of Object.entries(params.fields || {})) {
          try {
            if (sel.includes('@')) {
              const [s, at] = sel.split('@');
              const fe = await it.$(s);
              row[k] = fe ? (await page.evaluate((e, a) => e.getAttribute(a), fe, at)) || '' : '';
            } else {
              const fe = await it.$(sel);
              row[k] = fe ? ((await page.evaluate(e => e.innerText, fe)) || '').trim() : '';
            }
          } catch (e) { row[k] = ''; }
        }
        rows.push(row);
      }
      r.data = rows;
      r.msg = `抓取 ${rows.length} 条`;
      break;
    }

    // ── 表单 (邮件任务可能用) ──
    case 'extract_form': {
      r.data = await page.evaluate(() => {
        const fields = [];
        document.querySelectorAll('input, textarea, select').forEach((el) => {
          fields.push({
            tag: el.tagName.toLowerCase(),
            name: el.name || '', id: el.id || '',
            type: el.type || '', value: el.value || '',
            placeholder: el.placeholder || '',
          });
        });
        return { fields, count: fields.length };
      });
      r.msg = `表单字段 ${r.data.count} 个`;
      break;
    }
    case 'fill_form': {
      const fields = params.fields || {};
      let filled = 0;
      for (const [sel, val] of Object.entries(fields)) {
        try {
          await page.waitForSelector(sel, { timeout: 3000 });
          await page.$eval(sel, (el, v) => {
            el.value = v;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
          }, val);
          filled++;
        } catch (e) {}
      }
      r.msg = `填充 ${filled}/${Object.keys(fields).length} 字段`;
      break;
    }

    // ── 简化版 inspect / deep_inspect / find_element / check_state ──
    case 'inspect':
    case 'deep_inspect': {
      const sel = params.sel || 'body';
      r.data = await page.evaluate((s) => {
        const el = document.querySelector(s);
        if (!el) return null;
        return {
          tag: el.tagName.toLowerCase(),
          text: (el.innerText || '').slice(0, 500),
          attrs: Array.from(el.attributes).reduce((o, a) => { o[a.name] = a.value; return o; }, {}),
          children: el.children.length,
        };
      }, sel);
      r.msg = r.data ? `inspected: ${sel}` : `not found: ${sel}`;
      break;
    }
    case 'find_element': {
      // 简单 fallback: CSS / text
      const css = params.css;
      const text = params.text;
      let found = null;
      if (css) {
        const el = await page.$(css);
        if (el) found = { strategy: 'css', sel: css };
      }
      if (!found && text) {
        const handle = await page.evaluateHandle((t) => {
          const xpath = `//*[contains(text(), ${JSON.stringify(t)})]`;
          return document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        }, text);
        if (handle.asElement()) found = { strategy: 'text', text };
      }
      r.data = found;
      r.msg = found ? `找到: ${JSON.stringify(found)}` : '未找到';
      r.ok = !!found;
      break;
    }
    case 'check_state': {
      const sel = params.sel;
      r.data = await page.evaluate((s) => {
        const el = document.querySelector(s);
        if (!el) return { exists: false };
        const rect = el.getBoundingClientRect();
        return {
          exists: true,
          visible: rect.width > 0 && rect.height > 0,
          value: el.value || null,
          disabled: el.disabled || false,
          checked: el.checked || false,
        };
      }, sel);
      r.msg = JSON.stringify(r.data);
      break;
    }

    // ── 未实现的高级动作 ──
    case 'extract_table':
    case 'intercept':
    case 'download':
      r.ok = false;
      r.msg = `action '${a}' 暂未在 Node 版实现 (复杂度高, 用 eval + JS 自己写)`;
      break;

    default:
      r.ok = false;
      r.msg = `未知动作: ${a}`;
  }
  return r;
}

// ── HTTP server ────────────────────────────────────────────────
const app = express();
app.use(express.json({ limit: '10mb' }));

app.post('/act', async (req, res) => {
  const { action = 'shot', params = {}, session = 'default', wait = 800, shot = true } = req.body || {};
  try {
    const { ctx, page } = await _getSession(session);
    const result = await _run(action, params, page, ctx, session);
    if (wait > 0) await new Promise(r => setTimeout(r, wait));
    let b64 = null;
    if (shot) {
      try { b64 = await _shot(page); } catch (e) {}
    }
    _refreshIdle(session);
    res.json({ ...result, b64, url: page.url() });
  } catch (e) {
    let b64 = null;
    try {
      const entry = _ctxs.get(session);
      if (entry && entry.page && !entry.page.isClosed()) b64 = await _shot(entry.page);
    } catch (_) {}
    const entry2 = _ctxs.get(session);
    res.json({
      ok: false, msg: String(e).slice(0, 500), data: null,
      b64, url: entry2 && entry2.page && !entry2.page.isClosed() ? entry2.page.url() : '',
    });
  }
});

app.post('/shot', async (req, res) => {
  const session = (req.body && req.body.session) || 'default';
  try {
    const { page } = await _getSession(session);
    _refreshIdle(session);
    res.json({ ok: true, b64: await _shot(page), url: page.url() });
  } catch (e) {
    res.json({ ok: false, msg: String(e).slice(0, 300) });
  }
});

app.get('/health', (req, res) => {
  res.json({
    ok: true,
    version: 'node-puppeteer',
    chrome: CHROME_BIN,
    headless: HEADLESS,
    sessions: Array.from(_ctxs.keys()),
    active_count: _ctxs.size,
    idle_ms: IDLE_MS,
  });
});

app.post('/cleanup', async (req, res) => {
  const session = (req.body && req.body.session) || '';
  if (session) {
    await _cleanupSession(session, 'manual');
    res.json({ ok: true, msg: `cleaned: ${session}` });
  } else {
    const sids = Array.from(_ctxs.keys());
    for (const s of sids) await _cleanupSession(s, 'cleanup-all');
    res.json({ ok: true, msg: `cleaned all: ${sids.length} sessions` });
  }
});

// ── 进程退出: 关 browser ─────────────────────────────────────────
async function _shutdown() {
  console.log('[BAS-Node] shutting down...');
  for (const s of Array.from(_ctxs.keys())) await _cleanupSession(s, 'shutdown');
  if (_browser) try { await _browser.close(); } catch (e) {}
  process.exit(0);
}
process.on('SIGTERM', _shutdown);
process.on('SIGINT', _shutdown);

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[BAS-Node] listening on http://0.0.0.0:${PORT}`);
  console.log(`[BAS-Node] chrome=${CHROME_BIN}  idle_timeout=${IDLE_MS}ms (${IDLE_MS / 60000}min)`);
});
