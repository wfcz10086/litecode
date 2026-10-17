/* subagent_panel.js — [subagent-panel 2026-10-18] 后台子代理面板
 * 主代理 spawn_agent(background=true) 派出的子代理, 用户在当前会话里可以:
 *   看列表 (id / 角色 / 任务摘要 / 状态 / 已运行时间 / 最近进度) · 看结果 · 叫停 · 发补充指令。
 * 接口: routers/subagents_router.py → gateway/subagents.py → lib/agent/subagent_registry.py
 * 入口: 聊天输入区 (#ia) 上方的 #sa-panel; 本会话没有子代理记录时整个面板隐藏。
 * 刷新: 1s 本地巡检 (不发请求) 看 cur / streaming 变化 → 切会话、轮次开始/结束各拉一次;
 *       有运行中的子代理 → 每 3s 拉一次; 主轮次进行中 → 每 10s 拉一次 (发现新派出的);
 *       页面隐藏时暂停; 全部结束且没有进行中的轮次 → 不再请求。
 * 交互全部内嵌 (停止 = 两步确认按钮, 补充指令 = 内嵌输入卡片), 不用 prompt/alert/confirm 弹窗。
 * 依赖 app.js 的全局 cur / streaming (只读)。
 */
(function () {
  const POLL_MS = 3000, STREAM_POLL_MS = 10000, WATCH_MS = 1000, STOP_ARM_MS = 4000;
  let sid = null, items = [], sendMax = 2000, open = false, busy = false, lastFetch = 0, wasStreaming = false;
  let root = null, entry = null, drawer = null, list = null;
  const els = new Map();   // id -> {box, ...子元素}, 增量更新, 不整块重绘 (保住正在输入的补充指令)

  const api = (rid, tail) => `/api/sessions/${encodeURIComponent(sid)}/subagents`
    + (rid ? `/${encodeURIComponent(rid)}` : '') + (tail || '');

  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function fmtSecs(s) {
    s = Math.max(0, s | 0);
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0') + 's';
    return Math.floor(s / 3600) + 'h' + String(Math.floor(s % 3600 / 60)).padStart(2, '0') + 'm';
  }

  function isStreaming() { return typeof streaming !== 'undefined' && !!streaming; }
  function curSid() { return (typeof cur !== 'undefined' && cur) ? cur : null; }

  function mount() {
    if (root) return true;
    const ia = document.getElementById('ia');
    if (!ia || !ia.parentNode) return false;
    root = h('div', 'sa-panel'); root.id = 'sa-panel'; root.hidden = true;
    entry = h('button', 'sa-entry'); entry.id = 'sa-entry'; entry.type = 'button';
    entry.addEventListener('click', () => { open = !open; renderShell(); if (open) refresh(); });
    drawer = h('div', 'sa-drawer'); drawer.id = 'sa-drawer'; drawer.hidden = true;
    list = h('div', 'sa-list');
    drawer.appendChild(list);
    root.appendChild(entry); root.appendChild(drawer);
    ia.parentNode.insertBefore(root, ia);
    return true;
  }

  function renderShell() {
    if (!mount()) return;
    const run = items.filter(i => i.status === 'running').length;
    root.hidden = items.length === 0;
    root.dataset.running = String(run);
    entry.textContent = '';
    entry.appendChild(h('span', 'sa-dot' + (run ? ' on' : '')));
    entry.appendChild(h('span', 'sa-entry-t', `子代理 ${items.length}`));
    entry.appendChild(h('span', 'sa-entry-n', run ? `${run} 运行中` : '已全部结束'));
    entry.appendChild(h('span', 'sa-caret', open ? '▾' : '▸'));
    entry.setAttribute('aria-expanded', open ? 'true' : 'false');
    drawer.hidden = !open;
  }

  function note(it, text, bad) {
    const e = els.get(it.id);
    if (!e) return;
    e.note.textContent = text || '';
    e.note.classList.toggle('bad', !!bad);
    e.note.hidden = !text;
  }

  function build(it) {
    const box = h('div', 'sa-item'); box.dataset.id = it.id;
    const head = h('div', 'sa-head');
    const badge = h('span', 'sa-st');
    const idEl = h('span', 'sa-id', it.id);
    const role = h('span', 'sa-role');
    const brief = h('span', 'sa-brief');
    const time = h('span', 'sa-time');
    head.append(badge, idEl, role, brief, time);
    const prog = h('div', 'sa-prog');
    const acts = h('div', 'sa-acts');
    const bDetail = h('button', 'sa-btn sa-detail', '详情'); bDetail.type = 'button';
    const bSend = h('button', 'sa-btn sa-send', '补充指令'); bSend.type = 'button';
    const bStop = h('button', 'sa-btn sa-stop', '停止'); bStop.type = 'button';
    acts.append(bDetail, bSend, bStop);
    const note = h('div', 'sa-note'); note.hidden = true;
    const detail = h('div', 'sa-detailbox'); detail.hidden = true;
    const card = buildSendCard(it.id); card.box.hidden = true;
    box.append(head, prog, acts, note, card.box, detail);
    const e = { box, badge, role, brief, time, prog, bDetail, bSend, bStop, note, detail, card, status: null, armed: 0 };
    bDetail.addEventListener('click', () => toggleDetail(it.id));
    bSend.addEventListener('click', () => {
      card.box.hidden = !card.box.hidden;
      if (!card.box.hidden) { card.err.textContent = ''; card.ta.focus(); }
    });
    bStop.addEventListener('click', () => stopClick(it.id));
    return e;
  }

  function buildSendCard(rid) {
    const box = h('div', 'sa-card');
    const ta = h('textarea', 'sa-ta'); ta.rows = 3; ta.placeholder = '补充指令: 子代理下一轮迭代开始前收到 (Ctrl+Enter 发送)';
    ta.maxLength = sendMax;
    const foot = h('div', 'sa-card-foot');
    const cnt = h('span', 'sa-cnt', `0/${sendMax}`);
    const err = h('span', 'sa-err');
    const cancel = h('button', 'sa-btn', '取消'); cancel.type = 'button';
    const ok = h('button', 'sa-btn sa-primary sa-send-go', '发送'); ok.type = 'button';
    foot.append(cnt, err, cancel, ok);
    box.append(ta, foot);
    const c = { box, ta, cnt, err, ok };
    ta.addEventListener('input', () => { cnt.textContent = `${ta.value.length}/${sendMax}`; });
    ta.addEventListener('keydown', ev => { if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); doSend(rid, c); } });
    cancel.addEventListener('click', () => { box.hidden = true; });
    ok.addEventListener('click', () => doSend(rid, c));
    return c;
  }

  function update(e, it) {
    const running = it.status === 'running';
    e.box.dataset.status = it.status;
    e.badge.textContent = it.stop_requested && running ? '停止中' : (it.status_cn || it.status);
    e.badge.className = 'sa-st st-' + it.status;
    e.role.textContent = (it.agent_type || '') + (it.background ? '' : ' · 阻塞');
    e.brief.textContent = it.brief || '';
    e.brief.title = it.task || '';
    e.time.textContent = fmtSecs(it.elapsed);
    const iter = it.max_iter ? `${it.iteration}/${it.max_iter}` : String(it.iteration || 0);
    let p = `迭代 ${iter} · 工具 ${it.tool_calls || 0} 次 · 最近: ${it.last_tool || '无'}`;
    if (it.inbox) p += ` · 待送指令 ${it.inbox}`;
    if (it.delivered) p += ` · 已送达 ${it.delivered}`;
    if (it.restored) p += ' · (重启前的记录)';
    e.prog.textContent = p;
    e.bDetail.textContent = running ? '详情' : '查看结果';
    const ctl = running && !it.stop_requested;
    e.bSend.hidden = !ctl; e.bStop.hidden = !ctl;
    if (!ctl) e.card.box.hidden = true;
    if (e.status && e.status !== it.status && !e.detail.hidden) loadDetail(it.id);   // 结束了 → 刷新已展开的结果
    e.status = it.status;
  }

  function renderList() {
    if (!mount()) return;
    const seen = new Set();
    items.forEach((it, idx) => {
      seen.add(it.id);
      let e = els.get(it.id);
      if (!e) { e = build(it); els.set(it.id, e); }
      update(e, it);
      if (list.children[idx] !== e.box) list.insertBefore(e.box, list.children[idx] || null);
    });
    for (const [id, e] of els) if (!seen.has(id)) { e.box.remove(); els.delete(id); }
  }

  function render() { renderShell(); renderList(); }

  function reset() {
    items = []; open = false;
    for (const e of els.values()) e.box.remove();
    els.clear();
    render();
  }

  async function refresh() {
    if (busy || !sid) return;
    busy = true; lastFetch = Date.now();
    const s = sid;
    try {
      const r = await fetch(api());
      if (s !== sid) return;
      if (!r.ok) { if (r.status === 403 || r.status === 404) { items = []; render(); } return; }
      const d = await r.json();
      if (s !== sid) return;
      items = Array.isArray(d.items) ? d.items : [];
      sendMax = d.send_max || sendMax;
      render();
    } catch (e) { /* server 不通: 下次巡检再试 */ }
    finally { busy = false; }
  }

  async function loadDetail(rid) {
    const e = els.get(rid);
    if (!e) return;
    e.detail.textContent = '加载中…';
    try {
      const r = await fetch(api(rid));
      if (!r.ok) { e.detail.textContent = `加载失败 (HTTP ${r.status})`; return; }
      const d = await r.json();
      e.detail.textContent = '';
      e.detail.appendChild(h('div', 'sa-dl', '任务'));
      e.detail.appendChild(h('pre', 'sa-pre', d.task || ''));
      e.detail.appendChild(h('div', 'sa-dl', d.status === 'running' ? '结果 (运行中, 结束后可看)' : '结果'));
      if (d.status !== 'running') {
        e.detail.appendChild(h('pre', 'sa-pre sa-result', d.result || '(无输出)'));
        if (d.result_truncated) e.detail.appendChild(h('div', 'sa-dl', '落盘时结果过长已截断'));
      }
    } catch (err) { e.detail.textContent = '加载失败 (网络)'; }
  }

  function toggleDetail(rid) {
    const e = els.get(rid);
    if (!e) return;
    e.detail.hidden = !e.detail.hidden;
    if (!e.detail.hidden) loadDetail(rid);
  }

  async function post(rid, tail, body) {
    const r = await fetch(api(rid, tail), { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}) });
    let d = {};
    try { d = await r.json(); } catch (e) { /* 非 JSON */ }
    if (!r.ok) return { ok: false, error: (d && d.detail) || `HTTP ${r.status}` };
    return d;
  }

  /* 停止: 第一次点只"上膛" (按钮变成确认态), STOP_ARM_MS 内再点才真正发请求 —— 代替 confirm() */
  async function stopClick(rid) {
    const e = els.get(rid);
    if (!e) return;
    if (Date.now() - e.armed > STOP_ARM_MS) {
      e.armed = Date.now();
      e.bStop.textContent = '确认停止?'; e.bStop.classList.add('armed');
      setTimeout(() => { if (Date.now() - e.armed >= STOP_ARM_MS) { e.bStop.textContent = '停止'; e.bStop.classList.remove('armed'); } }, STOP_ARM_MS + 50);
      return;
    }
    e.armed = 0; e.bStop.textContent = '停止'; e.bStop.classList.remove('armed');
    e.bStop.disabled = true;
    try {
      const d = await post(rid, '/stop');
      note({ id: rid }, d.ok ? '已请求停止: 子代理会在下一轮迭代开始时结束' : (d.error || '停止失败'), !d.ok);
    } catch (err) { note({ id: rid }, '停止失败 (网络)', true); }
    finally { e.bStop.disabled = false; refresh(); }
  }

  async function doSend(rid, c) {
    const msg = c.ta.value.trim();
    if (!msg) { c.err.textContent = '请输入补充指令'; return; }
    if (msg.length > sendMax) { c.err.textContent = `过长: ${msg.length}/${sendMax} 字`; return; }
    c.ok.disabled = true; c.err.textContent = '';
    try {
      const d = await post(rid, '/send', { message: msg });
      if (d.ok) {
        c.ta.value = ''; c.cnt.textContent = `0/${sendMax}`; c.box.hidden = true;
        note({ id: rid }, '已投递: 子代理下一轮迭代开始前收到');
      } else c.err.textContent = d.error || '发送失败';
    } catch (err) { c.err.textContent = '发送失败 (网络)'; }
    finally { c.ok.disabled = false; refresh(); }
  }

  function tick() {
    const c = curSid();
    if (c !== sid) { sid = c; wasStreaming = isStreaming(); reset(); if (sid) refresh(); return; }
    if (!sid || document.hidden) return;
    const st = isStreaming();
    if (st !== wasStreaming) { wasStreaming = st; refresh(); return; }   // 轮次开始 / 结束
    const running = items.some(i => i.status === 'running');
    const every = running ? POLL_MS : (st ? STREAM_POLL_MS : 0);
    if (every && Date.now() - lastFetch >= every) refresh();
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && sid && items.some(i => i.status === 'running')) refresh();
  });
  setInterval(tick, WATCH_MS);
  window.LCSubagents = { refresh, state: () => ({ sid, items, open }) };
})();
