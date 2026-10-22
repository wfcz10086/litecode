/* skill_picker.js — 输入框 `/` 技能选择器 + 输入框上方的技能 chip + 消息里的"本轮用到的技能"。
 * 打 `/` (行首或空格后) 弹出: 实时搜索 (GET /api/skills?enabled=true&q=), 分组, ↑↓ 选择, 回车/Tab 选中, Esc 关闭。
 * 选中后去掉输入框里的 `/查询词`, 在输入框上方加 chip [技能名 ×], 可多选; 发送时 chat_stream.js 调
 * LCSkills.take() 把名字放进 body.skills (本轮强制使用, 见 lib/skill_turn.py)。
 * `/新建技能 <描述>` 回车 或 选择器底部「+ 用一句话新建技能」→ skill_create.js 的对话区卡片。
 * 键盘在 document 捕获阶段处理, 先于 #mi 的 onkeydown (onK 发送)。共用小工具挂在 window.LCSk。
 */
(function () {
  const NEW_CMD = '/新建技能';
  const sel = [];                 // [{name, title, draft}]
  let pop = null, rows = [], idx = 0, qSeq = 0, token = null, hasItems = false;

  /* ── 共用小工具 ── */
  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }
  async function api(url, opt) {
    const o = Object.assign({headers: {}}, opt || {});
    if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers['Content-Type'] = 'application/json'; delete o.json; }
    const r = await fetch(url, o);
    let d = null;
    try { d = await r.json(); } catch (e) { d = {}; }
    if (!r.ok && !(d && (d.skills || d.scan))) {
      const msg = (d && (typeof d.detail === 'string' ? d.detail : d.error)) || ('HTTP ' + r.status);
      throw new Error(msg);
    }
    return d;
  }
  const LEVEL = {ok: '✅ 安全扫描通过', warn: '⚠️ 有需要确认的风险项', block: '⛔ 安全扫描未通过 (已拒绝)'};
  /* 扫描结果块: {level, findings:[{rule,level,title,file,line,snippet}]} */
  function scanBox(scan) {
    const lv = (scan && scan.level) || 'ok';
    const box = h('div', 'sk-scan ' + lv, LEVEL[lv] || lv);
    const fs = (scan && scan.findings) || [];
    if (fs.length) {
      const ul = h('ul');
      fs.slice(0, 12).forEach(f => {
        const li = h('li');
        li.innerHTML = `<b>${esc(f.level === 'block' ? '拒绝' : '注意')}</b> ${esc(f.title)} — <code>${esc(f.file)}${f.line ? ':' + f.line : ''}</code> <code>${esc(f.snippet)}</code>`;
        ul.appendChild(li);
      });
      if (fs.length > 12) ul.appendChild(h('li', '', `… 还有 ${fs.length - 12} 项`));
      box.appendChild(ul);
    }
    return box;
  }
  /* 两步确认按钮: 第一次点变成"再点一次确认", 4 秒内再点才执行 (不用 confirm 弹窗) */
  function armButton(btn, label, fn) {
    let armed = 0;
    btn.onclick = () => {
      if (Date.now() - armed < 4000) { armed = 0; btn.classList.remove('arm'); btn.textContent = label; fn(); return; }
      armed = Date.now(); btn.classList.add('arm'); btn.textContent = '再点一次确认';
      setTimeout(() => { if (armed && Date.now() - armed >= 3900) { armed = 0; btn.classList.remove('arm'); btn.textContent = label; } }, 4000);
    };
  }
  window.LCSk = {h, esc, api, scanBox, armButton};

  /* ── chip ── */
  function chipsEl() {
    let c = document.getElementById('sk-chips');
    if (!c) {
      const ia = document.getElementById('ia'), irow = document.getElementById('irow');
      if (!ia || !irow) return null;
      c = h('div', 'sk-chips'); c.id = 'sk-chips';
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
      const chip = h('span', 'sk-chip' + (s.draft ? ' draft' : ''));
      chip.dataset.name = s.name;
      chip.title = s.draft ? `草稿试运行: ${s.name}` : `本轮强制使用技能: ${s.name}`;
      chip.appendChild(h('span', '', '🧩 ' + (s.title || s.name) + (s.draft ? ' (草稿)' : '')));
      const x = h('button', 'sk-chip-x', '×');
      x.type = 'button'; x.title = '移除';
      x.onclick = () => { const i = sel.findIndex(z => z.name === s.name); if (i >= 0) sel.splice(i, 1); renderChips(); };
      chip.appendChild(x);
      c.appendChild(chip);
    });
  }
  function add(item) {
    if (!item || !item.name) return;
    if (!sel.some(s => s.name === item.name)) sel.push({name: item.name, title: item.title || item.name, draft: !!item.draft});
    renderChips();
  }
  function take() {
    const out = sel.map(s => s.name);
    sel.length = 0;
    renderChips();
    return out;
  }

  /* ── 消息里的"本轮用到的技能" ── */
  function tagMsg(el, names) {
    if (!el || !names || !names.length) return;
    const mb = el.classList && el.classList.contains('mb') ? el : (el.querySelector && el.querySelector('.mb')) || el;
    if (mb.querySelector(':scope > .sk-used')) return;
    const box = h('div', 'sk-used', '🧩 本轮技能');
    names.forEach(n => box.appendChild(h('span', 'sk-tag', n)));
    const mac = mb.querySelector(':scope > .mac');
    mb.insertBefore(box, mac || null);
  }

  /* ── 选择器 ── */
  function mi() { return document.getElementById('mi'); }
  function slashToken() {
    const t = mi();
    if (!t) return null;
    const pos = t.selectionStart, before = t.value.slice(0, pos);
    const m = before.match(/(^|\s)\/([^\s/]*)$/);
    if (!m) return null;
    return {start: pos - m[2].length - 1, end: pos, q: m[2]};
  }
  function close() { if (pop) { pop.remove(); pop = null; } rows = []; token = null; }
  function isOpen() { return !!pop; }

  async function refresh() {
    token = slashToken();
    if (!token || token.q.startsWith('新建技能')) { close(); return; }
    const my = ++qSeq;
    let items = [];
    try {
      const d = await api('/api/skills?enabled=true&q=' + encodeURIComponent(token.q));
      items = d.items || [];
    } catch (e) { items = []; }
    if (my !== qSeq || !slashToken()) return;
    render(items.slice(0, token.q ? 30 : 200), !token.q);
  }

  function render(items, grouped) {
    const ia = document.getElementById('ia');
    if (!ia) return;
    if (!pop) {
      pop = h('div', 'sk-pop'); pop.id = 'sk-pop';
      pop.addEventListener('mousedown', e => e.preventDefault());   // 点选时不让输入框失焦
      ia.appendChild(pop);
    }
    pop.innerHTML = '';
    const hd = h('div', 'sk-pop-h');
    hd.innerHTML = `<b>选择技能</b><span>↑↓ 选择 · 回车选中 · Esc 关闭 · 可多选</span>`;
    pop.appendChild(hd);
    const list = h('div', 'sk-pop-list');
    rows = [];
    let lastG = null;
    items.forEach(it => {
      if (grouped && it.group !== lastG) { lastG = it.group; list.appendChild(h('div', 'sk-pop-g', it.group)); }
      const r = h('div', 'sk-pop-i');
      r.innerHTML = `<div class="t">${esc(it.title || it.name)}<small>${esc(it.name)}</small></div><div class="d">${esc(it.description || '')}</div>`;
      r.onclick = () => choose(rows.indexOf(r));
      r._item = it;
      rows.push(r);
      list.appendChild(r);
    });
    hasItems = items.length > 0;
    if (!items.length) list.appendChild(h('div', 'sk-pop-empty', '没有匹配的技能'));
    pop.appendChild(list);
    const nw = h('div', 'sk-pop-new', '+ 用一句话新建技能');
    nw.onclick = () => choose(rows.indexOf(nw));
    nw._new = true;
    rows.push(nw);
    pop.appendChild(nw);
    idx = 0;
    mark();
  }
  function mark() {
    rows.forEach((r, i) => r.classList.toggle('on', i === idx));
    const r = rows[idx];
    if (r && r.scrollIntoView) r.scrollIntoView({block: 'nearest'});
  }
  function stripToken() {
    const t = mi(), tk = token || slashToken();
    if (!t || !tk) return;
    t.value = t.value.slice(0, tk.start) + t.value.slice(tk.end);
    t.setSelectionRange(tk.start, tk.start);
    if (typeof arx === 'function') arx(t);
  }
  function choose(i) {
    const r = rows[i];
    if (!r) return;
    if (r._new) {
      const t = mi();
      stripToken(); close();
      t.value = NEW_CMD + ' ' + t.value.trimStart();
      t.focus();
      t.setSelectionRange(t.value.length, t.value.length);
      if (typeof arx === 'function') arx(t);
      return;
    }
    stripToken();
    close();
    add(r._item);
    mi() && mi().focus();
  }

  function newCmdText() {
    const t = mi();
    const v = t ? t.value.trim() : '';
    return v.startsWith(NEW_CMD) ? v.slice(NEW_CMD.length).trim() : null;
  }
  function runNewCmd() {
    const desc = newCmdText();
    if (desc === null) return false;
    if (!window.LCSkillCreate) return false;
    if (desc.length < 4) { if (typeof toast === 'function') toast('在 /新建技能 后面用一句话描述要做什么'); return true; }
    const t = mi();
    t.value = ''; if (typeof arx === 'function') arx(t);
    LCSkillCreate.start(desc);
    return true;
  }

  document.addEventListener('keydown', ev => {
    if (!ev.target || ev.target.id !== 'mi' || ev.isComposing) return;
    if (isOpen()) {
      if (ev.key === 'ArrowDown') { idx = (idx + 1) % rows.length; mark(); }
      else if (ev.key === 'ArrowUp') { idx = (idx - 1 + rows.length) % rows.length; mark(); }
      else if ((ev.key === 'Enter' || ev.key === 'Tab') && !hasItems) { close(); return; }   /* 没匹配 (如 /opt 路径) 照常发送 */
      else if (ev.key === 'Enter' || ev.key === 'Tab') { choose(idx); }
      else if (ev.key === 'Escape') { close(); }
      else return;
      ev.preventDefault(); ev.stopImmediatePropagation();
      return;
    }
    if (ev.key === 'Enter' && !ev.shiftKey && newCmdText() !== null) {
      if (runNewCmd()) { ev.preventDefault(); ev.stopImmediatePropagation(); }
    }
  }, true);
  document.addEventListener('click', ev => {
    const b = ev.target && ev.target.closest && ev.target.closest('#sdb');
    if (b && newCmdText() !== null && runNewCmd()) { ev.preventDefault(); ev.stopImmediatePropagation(); }
    if (pop && !(ev.target.closest && (ev.target.closest('#sk-pop') || ev.target.id === 'mi'))) close();
  }, true);
  document.addEventListener('input', ev => { if (ev.target && ev.target.id === 'mi') refresh(); }, true);

  window.LCSkills = {add, take, tagMsg, selected: () => sel.map(s => s.name), close};
})();
