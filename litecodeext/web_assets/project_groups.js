// 会话按项目归组 (对标 ChatGPT Projects): 侧栏会话列表按项目分段, 项目带指令 / 工作目录,
// 进了项目的会话由后端 lib/turn_sources.py 的 project 来源把这些内容附给模型。
// renderSL() 渲染完调 LCProjects.decorate(#sl); 删掉本文件 <script> 即回到平铺列表。
(function () {
  const COLLAPSE_KEY = 'lc.projects.collapsed';
  let projects = [];      // 未归档项目, 最近活跃在前
  let map = {};           // session_id → project_id
  let loaded = false, loading = false;

  const store = {
    get() { try { return JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '{}'); } catch (_) { return {}; } },
    set(v) { try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(v)); } catch (_) {} },
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  async function api(method, url, body) {
    const r = await fetch(url, {
      method, headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    return d;
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const [p, m] = await Promise.all([api('GET', '/api/projects'), api('GET', '/api/project-sessions')]);
      projects = (p.projects || []).filter(x => x.status !== 'archived')
        .sort((a, b) => (b.last_active || 0) - (a.last_active || 0));
      map = m.map || {};
      loaded = true;
    } catch (_) { /* 未登录或接口不可用: 保持平铺 */ }
    loading = false;
  }

  async function refresh() {
    await load();
    if (typeof window.renderSL === 'function') renderSL();
  }

  // app.js 里 cur 是顶层 let (全局词法作用域, 不在 window 上)
  const curSid = () => (typeof cur !== 'undefined' ? cur : null);
  const note = msg => (typeof window.toast === 'function' ? toast(msg) : null);
  const byId = pid => projects.find(p => p.project_id === pid);

  // ── 侧栏分组 ───────────────────────────────────────────
  function section(title, key, count, actions) {
    const sec = el('div', 'pg');
    const hd = el('div', 'pg-hd');
    const collapsed = !!store.get()[key];
    if (collapsed) sec.classList.add('pg-closed');
    hd.append(el('span', 'pg-arr', '▾'), el('span', 'pg-name', title), el('span', 'pg-n', String(count)));
    actions.forEach(a => hd.appendChild(a));
    hd.addEventListener('click', e => {
      if (e.target.closest('button')) return;
      const st = store.get();
      st[key] = !sec.classList.toggle('pg-closed') ? 0 : 1;
      store.set(st);
    });
    const body = el('div', 'pg-bd');
    sec.append(hd, body);
    return { sec, body };
  }

  function iconBtn(text, title, onClick) {
    const b = el('button', 'pg-btn', text);
    b.type = 'button';
    b.title = title;
    b.addEventListener('click', e => { e.stopPropagation(); onClick(e); });
    return b;
  }

  function decorate(listEl) {
    if (!loaded) { load().then(() => { if (loaded) renderSL(); }); return; }
    const items = Array.from(listEl.querySelectorAll(':scope > .si[data-sid]'));
    items.forEach(si => si.insertBefore(moveBtn(si.dataset.sid), si.querySelector('.sdl')));
    updateChip();
    const searching = !!(document.getElementById('sess-search')?.value || '').trim();
    if (searching) return;

    const frag = document.createDocumentFragment();
    /* [ui-v2 2026-10-21] 项目区整体可折叠 (默认展开), 空项目不再显示「还没有会话」占一行;
       项目外的会话按 今天 / 昨天 / 7 天内 / 30 天内 / 更早 分组 (置顶在最前) */
    const st0 = store.get();
    const zone = el('div', 'pg-zone' + (st0.__zone ? ' pg-zone-closed' : ''));
    const zh = el('div', 'pg-zone-hd');
    zh.append(el('span', 'pg-arr', '▾'), el('span', 'pg-name', '项目'), el('span', 'pg-n', String(projects.length)));
    zh.appendChild(iconBtn('＋', '新建项目', () => openCard(null)));
    zh.addEventListener('click', e => {
      if (e.target.closest('button')) return;
      const st = store.get();
      st.__zone = zone.classList.toggle('pg-zone-closed') ? 1 : 0;
      store.set(st);
    });
    const zb = el('div', 'pg-zone-bd');
    zone.append(zh, zb);
    frag.appendChild(zone);

    const curPid = map[curSid()];
    projects.forEach(p => {
      const mine = items.filter(si => map[si.dataset.sid] === p.project_id);
      const { sec, body } = section('📁 ' + p.name, 'p:' + p.project_id, mine.length, [
        iconBtn('＋', '在这个项目里新建会话', () => newChatIn(p.project_id)),
        iconBtn('⋯', '项目设置: 名称 / 指令 / 工作目录', () => openCard(p)),
      ]);
      sec.dataset.pid = p.project_id;
      if (!mine.length) sec.classList.add('pg-noitems');
      if (p.project_id === curPid) { sec.classList.remove('pg-closed'); zone.classList.remove('pg-zone-closed'); }
      mine.forEach(si => body.appendChild(si));
      zb.appendChild(sec);
    });
    const rest = items.filter(si => !byId(map[si.dataset.sid]));
    appendDated(frag, rest);
    listEl.textContent = '';
    listEl.appendChild(frag);
  }

  function dayBucket(ts) {
    if (!ts) return '更早';
    const now = new Date();
    const d0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
    if (ts >= d0) return '今天';
    if (ts >= d0 - 86400) return '昨天';
    if (ts >= d0 - 6 * 86400) return '7 天内';
    if (ts >= d0 - 29 * 86400) return '30 天内';
    return '更早';
  }

  function appendDated(frag, list) {
    const sortKey = document.getElementById('sess-sort')?.value || 'recent';
    if (sortKey !== 'recent') { list.forEach(si => frag.appendChild(si)); return; }
    let pinned = new Set();
    try { pinned = new Set(JSON.parse(localStorage.getItem('lc_pinned_sessions') || '[]')); } catch (_) {}
    const all = (typeof sess !== 'undefined' && Array.isArray(sess)) ? sess : [];
    const byIdS = new Map(all.map(s => [s.id, s]));
    let last = null;
    list.forEach(si => {
      const s = byIdS.get(si.dataset.sid) || {};
      const b = pinned.has(si.dataset.sid) ? '已置顶' : dayBucket(s.last_used || s.created);
      if (b !== last) { frag.appendChild(el('div', 'sl-date', b)); last = b; }
      frag.appendChild(si);
    });
  }

  function updateChip() {
    const ct = document.getElementById('ct');
    if (!ct) return;
    let chip = document.getElementById('pj-chip');
    const p = byId(map[curSid()]);
    if (!p) { chip?.remove(); return; }
    if (!chip) {
      chip = el('button', 'pj-chip');
      chip.id = 'pj-chip';
      chip.type = 'button';
      ct.insertAdjacentElement('beforebegin', chip);
    }
    chip.textContent = '📁 ' + p.name;
    chip.title = '本会话属于项目「' + p.name + '」, 点击查看项目设置';
    chip.onclick = () => openCard(byId(map[curSid()]));
  }

  // ── 移动会话 ───────────────────────────────────────────
  function moveBtn(sid) {
    const b = el('button', 'sdl pg-mv', '📁');
    b.type = 'button';
    b.title = '移到项目';
    b.addEventListener('click', e => { e.stopPropagation(); openMenu(b, sid); });
    return b;
  }

  function closeMenu() { document.querySelector('.pg-menu')?.remove(); }

  function openMenu(anchor, sid) {
    closeMenu();
    const menu = el('div', 'pg-menu');
    const add = (label, fn, cls) => {
      const it = el('button', 'pg-mi' + (cls ? ' ' + cls : ''), label);
      it.type = 'button';
      it.addEventListener('click', async e => { e.stopPropagation(); closeMenu(); await fn(); });
      menu.appendChild(it);
    };
    menu.appendChild(el('div', 'pg-mh', '移到项目'));
    projects.forEach(p => add((map[sid] === p.project_id ? '✓ ' : '') + p.name, () => moveTo(sid, p.project_id)));
    if (byId(map[sid])) add('移出项目', () => moveOut(sid), 'pg-mi-out');
    add('＋ 新建项目并移入…', () => openCard(null, sid), 'pg-mi-new');
    document.body.appendChild(menu);
    const r = anchor.getBoundingClientRect();
    menu.style.left = Math.min(r.left, window.innerWidth - menu.offsetWidth - 8) + 'px';
    menu.style.top = Math.min(r.bottom + 4, window.innerHeight - menu.offsetHeight - 8) + 'px';
    setTimeout(() => document.addEventListener('click', closeMenu, { once: true }), 0);
  }

  async function moveTo(sid, pid) {
    try { await api('POST', `/api/projects/${pid}/sessions`, { session_id: sid }); note('已移到「' + byId(pid).name + '」'); }
    catch (e) { note('移动失败: ' + e.message); }
    await refresh();
  }

  async function moveOut(sid) {
    try { await api('DELETE', `/api/project-sessions/${encodeURIComponent(sid)}`); note('已移出项目'); }
    catch (e) { note('移出失败: ' + e.message); }
    await refresh();
  }

  async function newChatIn(pid) {
    await newChat();   // 进行中也可新建: newChat 会先断开当前会话的订阅 (后台轮次照跑)
    if (curSid()) await moveTo(curSid(), pid);
  }

  window.LCProjects = { decorate, refresh, moveTo, moveOut, newChatIn, state: () => ({ projects, map }) };
  if (window.ProjectCard) ProjectCard.bind({ api, refresh, note, moveTo });
  const openCard = (p, sid) => window.ProjectCard && ProjectCard.open(p, sid);
})();
