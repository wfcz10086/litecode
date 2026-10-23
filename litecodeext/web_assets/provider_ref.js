/* provider_ref.js — 输入框 `@` 引用能力接入 (与 skill_picker.js 的 `/` 选技能同一套交互)。
 * 打 `@` (行首或空格后) 弹出接入选择器: 实时过滤 (名字/类型/用途), ↑↓ 选择, 回车/Tab 选中, Esc 关闭。
 * 选中后去掉输入框里的 `@查询词`, 输入框上方加 chip [🔌 名字 ×]; 发送时 chat_stream.js 调 LCProviderRefs.take()
 * 把 id 放进 body.providers —— server 端 lib/turn_sources.providers_ref 把完整明文 (名称/类型/地址/key/用法) 挂进本轮上下文。
 * 消息气泡只显示标题 chip (user 消息的 providers 字段), 点 chip 展开实时明文。详情页「引用」= LCProviderRefs.add()。 */
(function () {
  'use strict';
  const sel = [];               // [{id, name, kind}]
  let pop = null, rows = [], idx = 0, token = null, cache = null, cacheAt = 0, qSeq = 0;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const h = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const mi = () => document.getElementById('mi');
  const ICON = {image: '🎨', video: '🎬', tts: '🔊', asr: '🎙️', search: '🔎', music: '🎵', embedding: '🧮', rerank: '📶',
    notify: '📣', storage: '🗄️', other: '🧩'};

  async function items() {
    if (cache && Date.now() - cacheAt < 15000) return cache;
    const r = await fetch('/api/providers', {credentials: 'same-origin'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    cache = (d.providers || []).concat(d.builtins || []).map(p => Object.assign(p, {label: ((d.kinds || {})[p.kind] || {}).label || p.kind}));
    cacheAt = Date.now();
    return cache;
  }

  /* ── chip (输入框上方) ── */
  function chipsEl() {
    let c = document.getElementById('pv-chips');
    if (!c) {
      const ia = document.getElementById('ia'), irow = document.getElementById('irow');
      if (!ia || !irow) return null;
      c = h('div', 'sk-chips pv-chips'); c.id = 'pv-chips';
      ia.insertBefore(c, irow);
    }
    return c;
  }
  function renderChips() {
    const c = chipsEl();
    if (!c) return;
    c.innerHTML = '';
    c.style.display = sel.length ? 'flex' : 'none';
    sel.forEach(s => {
      const chip = h('span', 'sk-chip pv-chip');
      chip.dataset.id = s.id;
      chip.title = `本条消息引用接入: ${s.name} (完整明文随消息发给 LiteCode)`;
      chip.appendChild(h('span', '', '🔌 ' + s.name));
      const x = h('button', 'sk-chip-x', '×');
      x.type = 'button'; x.title = '移除';
      x.onclick = () => { const i = sel.findIndex(z => z.id === s.id); if (i >= 0) sel.splice(i, 1); renderChips(); };
      chip.appendChild(x);
      c.appendChild(chip);
    });
  }
  function add(it) {
    if (!it || !it.id) return;
    if (!sel.some(s => s.id === it.id) && sel.length < 5) sel.push({id: it.id, name: it.name || it.id, kind: it.kind || ''});
    renderChips();
  }
  function take() {
    const out = sel.slice();
    sel.length = 0;
    renderChips();
    return out;
  }

  /* ── 消息气泡里的引用 chip (点开看明文) ── */
  function tagMsg(el, refs) {
    if (!el || !refs || !refs.length) return;
    const mb = el.classList && el.classList.contains('mb') ? el : (el.querySelector && el.querySelector('.mb')) || el;
    if (mb.querySelector(':scope > .pv-used')) return;
    const box = h('div', 'pv-used');
    box.appendChild(h('span', 'pv-used-h', '🔌 引用'));
    refs.forEach(r => {
      const b = h('button', 'pv-ref', (ICON[r.kind] || '🔌') + ' ' + (r.name || r.id));
      b.type = 'button'; b.dataset.id = r.id; b.title = '点开看引用的明文';
      b.onclick = () => toggleCard(box, b, r);
      box.appendChild(b);
    });
    mb.insertBefore(box, mb.firstChild);
  }
  async function toggleCard(box, btn, r) {
    let pre = box.querySelector(`.pv-ref-card[data-id="${CSS.escape(r.id)}"]`);
    if (pre) { pre.remove(); btn.classList.remove('on'); return; }
    pre = h('blockquote', 'pv-ref-card pv-usage', '加载中…');
    pre.dataset.id = r.id;
    box.appendChild(pre);
    btn.classList.add('on');
    try {
      const d = await (await fetch('/api/providers/' + encodeURIComponent(r.id), {credentials: 'same-origin'})).json();
      pre.textContent = d.card || ('接入「' + (r.name || r.id) + '」已被删除');
    } catch (e) { pre.textContent = '加载失败: ' + e.message; }
  }

  /* ── 选择器 ── */
  function atToken() {
    const t = mi();
    if (!t) return null;
    const pos = t.selectionStart, before = t.value.slice(0, pos);
    const m = before.match(/(^|\s)@([^\s@]*)$/);
    if (!m) return null;
    return {start: pos - m[2].length - 1, end: pos, q: m[2]};
  }
  function close() { if (pop) { pop.remove(); pop = null; } rows = []; token = null; }
  function isOpen() { return !!pop; }

  async function refresh() {
    token = atToken();
    if (!token) { close(); return; }
    const my = ++qSeq;
    let list = [];
    try { list = await items(); } catch (e) { list = []; }
    if (my !== qSeq || !atToken()) return;
    const q = token.q.toLowerCase();
    const hit = !q ? list : list.filter(p => [p.name, p.label, p.kind, p.note, p.protocol].some(x => String(x || '').toLowerCase().includes(q)));
    render(hit.slice(0, 40), list.length);
  }

  function render(list, total) {
    const ia = document.getElementById('ia');
    if (!ia) return;
    if (!pop) {
      pop = h('div', 'sk-pop pv-pop'); pop.id = 'pv-pop';
      pop.addEventListener('mousedown', e => e.preventDefault());
      ia.appendChild(pop);
    }
    pop.innerHTML = '';
    const hd = h('div', 'sk-pop-h');
    hd.innerHTML = '<b>引用能力接入</b><span>↑↓ 选择 · 回车选中 · Esc 关闭 · 完整明文随消息发送</span>';
    pop.appendChild(hd);
    const box = h('div', 'sk-pop-list');
    rows = [];
    list.forEach(p => {
      const r = h('div', 'sk-pop-i');
      r.innerHTML = `<div class="t">${ICON[p.kind] || '🧩'} ${esc(p.name)}<small>${esc(p.label)} · ${esc(p.protocol)}${p.enabled ? '' : ' · 已停用'}</small></div>
        <div class="d">${esc(p.note || p.base_url || '')}</div>`;
      r.onclick = () => choose(rows.indexOf(r));
      r._item = p;
      rows.push(r);
      box.appendChild(r);
    });
    if (!list.length) box.appendChild(h('div', 'sk-pop-empty', total ? '没有匹配的接入' : '还没有接入, 点下面去添加'));
    pop.appendChild(box);
    const mg = h('div', 'sk-pop-new', '🔌 管理能力接入');
    mg.onclick = () => { close(); if (typeof openProviders === 'function') openProviders(); };
    mg._manage = true;
    rows.push(mg);
    pop.appendChild(mg);
    idx = 0;
    mark();
  }
  function mark() {
    rows.forEach((r, i) => r.classList.toggle('on', i === idx));
    const r = rows[idx];
    if (r && r.scrollIntoView) r.scrollIntoView({block: 'nearest'});
  }
  function choose(i) {
    const r = rows[i];
    if (!r) return;
    if (r._manage) { close(); if (typeof openProviders === 'function') openProviders(); return; }
    const t = mi(), tk = token || atToken();
    if (t && tk) {
      t.value = t.value.slice(0, tk.start) + t.value.slice(tk.end);
      t.setSelectionRange(tk.start, tk.start);
      if (typeof arx === 'function') arx(t);
    }
    close();
    add(r._item);
    t && t.focus();
  }

  document.addEventListener('keydown', ev => {
    if (!ev.target || ev.target.id !== 'mi' || ev.isComposing || !isOpen()) return;
    if (ev.key === 'ArrowDown') { idx = (idx + 1) % rows.length; mark(); }
    else if (ev.key === 'ArrowUp') { idx = (idx - 1 + rows.length) % rows.length; mark(); }
    else if ((ev.key === 'Enter' || ev.key === 'Tab') && rows.length <= 1) { close(); return; }   /* 没匹配 (如邮箱/@某人) 照常发送 */
    else if (ev.key === 'Enter' || ev.key === 'Tab') { choose(idx); }
    else if (ev.key === 'Escape') { close(); }
    else return;
    ev.preventDefault(); ev.stopImmediatePropagation();
  }, true);
  document.addEventListener('click', ev => {
    if (pop && !(ev.target.closest && (ev.target.closest('#pv-pop') || ev.target.id === 'mi'))) close();
  }, true);
  document.addEventListener('input', ev => { if (ev.target && ev.target.id === 'mi') refresh(); }, true);

  window.LCProviderRefs = {add, take, tagMsg, selected: () => sel.slice(), close, invalidate: () => { cache = null; }};
})();
