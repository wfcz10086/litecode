/* skills_panel.js — 侧栏「🧩 技能」面板: 页签「已安装」「技能市场」(市场页见 skill_market.js)。
 * 已安装: 搜索 + 来源筛选, 按分组折叠; 卡片显示中文名/描述/来源/版本/使用次数/启用开关,
 *         「查看」展开正文与文件列表, 「导出」下载 zip, 「卸载」卡片内二次点击确认 (内置只能停用)。
 * 草稿区: 列出待审核的技能草稿 (模型 create_skill 或「新建技能」生成), 卡片同 skill_create.js。
 * 红点: 有草稿时侧栏入口显示红点; 每 60 秒和每轮对话结束后查一次 GET /api/skills/drafts。
 */
(function () {
  const {h, esc, api, armButton} = window.LCSk;
  const SRC = {builtin: '内置', installed: '已安装', created: '自建', draft: '草稿'};
  let tab = 'installed', q = '', source = '', timer = null, built = false, wasStreaming = false;

  function panel() {
    let p = document.getElementById('pnl-skills');
    if (p) return p;
    p = h('div', 'pnl'); p.id = 'pnl-skills';
    p.addEventListener('click', e => { if (e.target === p) closeP('skills'); });
    p.innerHTML = `<div class="pb_"><div class="ph_"><span class="pt_">🧩 技能</span>
      <div class="sk-tabs"><button class="sk-tab on" data-tab="installed" type="button">已安装</button>
      <button class="sk-tab" data-tab="market" type="button">技能市场</button></div>
      <button class="px_" type="button" onclick="closeP('skills')">×</button></div>
      <div class="pbd_" id="sk-bd"><div id="sk-installed"></div><div id="sk-market" style="display:none"></div></div></div>`;
    document.body.appendChild(p);
    p.querySelectorAll('.sk-tab').forEach(b => b.onclick = () => switchTab(b.dataset.tab));
    return p;
  }

  function switchTab(t) {
    tab = t;
    const p = panel();
    p.querySelectorAll('.sk-tab').forEach(b => b.classList.toggle('on', b.dataset.tab === t));
    p.querySelector('#sk-installed').style.display = t === 'installed' ? '' : 'none';
    p.querySelector('#sk-market').style.display = t === 'market' ? '' : 'none';
    if (t === 'market' && window.LCSkillMarket) LCSkillMarket.mount(p.querySelector('#sk-market'));
    if (t === 'installed') load();
  }

  function open(t) {
    panel();
    if (typeof openP === 'function') openP('skills');
    switchTab(t || tab);
  }

  function buildInstalled(root) {
    root.innerHTML = '';
    const bar = h('div', 'sk-bar');
    const inp = h('input'); inp.placeholder = '搜索技能 (名称 / 中文名 / 别名 / 描述)'; inp.id = 'sk-q'; inp.value = q;
    const selSrc = h('select'); selSrc.id = 'sk-src';
    [['', '全部来源'], ['builtin', '内置'], ['installed', '已安装'], ['created', '自建']].forEach(([v, t]) => {
      const o = h('option', '', t); o.value = v; selSrc.appendChild(o);
    });
    selSrc.value = source;
    let deb = null;
    inp.oninput = () => { clearTimeout(deb); deb = setTimeout(() => { q = inp.value.trim(); load(); }, 220); };
    selSrc.onchange = () => { source = selSrc.value; load(); };
    bar.appendChild(inp); bar.appendChild(selSrc);
    root.appendChild(bar);
    root.appendChild(h('div', '', null)).id = 'sk-drafts-box';
    root.appendChild(h('div', '', null)).id = 'sk-list';
    built = true;
  }

  async function load() {
    const root = panel().querySelector('#sk-installed');
    if (!built) buildInstalled(root);
    const list = root.querySelector('#sk-list');
    list.innerHTML = '<div class="sk-empty">加载中…</div>';
    try {
      const params = new URLSearchParams({q, source});
      const [d, dr] = await Promise.all([api('/api/skills?' + params), api('/api/skills/drafts')]);
      renderDrafts(root.querySelector('#sk-drafts-box'), dr.items || []);
      renderList(list, d.items || [], !!q);
      setDot((dr.items || []).length);
    } catch (e) {
      list.innerHTML = '';
      list.appendChild(h('div', 'sk-err', '加载失败: ' + e.message));
    }
  }

  function renderDrafts(box, drafts) {
    box.innerHTML = '';
    if (!drafts.length) return;
    const wrap = h('div', 'sk-drafts');
    wrap.appendChild(h('div', 'sk-sec-h', `📝 待审核草稿 (${drafts.length})`));
    drafts.forEach(dft => {
      const row = h('div', 'sk-card');
      row.innerHTML = `<div class="sk-row"><span class="sk-title">${esc(dft.title || dft.name)}</span><span class="sk-name">${esc(dft.name)}</span>` +
        `<span class="sk-badge draft">草稿</span><span class="sk-badge">${esc({ok: '扫描通过', warn: '有风险项', block: '扫描未通过'}[dft.scan && dft.scan.level] || '')}</span><span class="sk-sp"></span></div>` +
        `<div class="sk-desc">${esc(dft.description || '')}</div>`;
      const b = h('button', 'sk-btn', '审核'); b.type = 'button';
      row.querySelector('.sk-row').appendChild(b);
      const holder = h('div', 'sk-detail');
      row.appendChild(holder);
      b.onclick = async () => {
        if (holder.classList.toggle('on') && !holder.childNodes.length) {
          try {
            const full = await api('/api/skills/' + encodeURIComponent(dft.name));
            holder.appendChild(LCSkillCreate.draftCard(full, {onChange: () => setTimeout(load, 800)}));
          } catch (e) { holder.appendChild(h('div', 'sk-err', e.message)); }
        }
      };
      wrap.appendChild(row);
    });
    box.appendChild(wrap);
  }

  function renderList(list, items, flat) {
    list.innerHTML = '';
    if (!items.length) { list.appendChild(h('div', 'sk-empty', '没有匹配的技能')); return; }
    const groups = new Map();
    items.forEach(it => {
      const g = flat ? '搜索结果' : it.group;
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(it);
    });
    groups.forEach((arr, g) => {
      const det = h('details', 'sk-group');
      det.open = flat || groups.size <= 3;
      const sm = h('summary');
      sm.innerHTML = `${esc(g)}<small>${arr.length} 个 · 已启用 ${arr.filter(x => x.enabled).length}</small>`;
      det.appendChild(sm);
      arr.forEach(it => det.appendChild(card(it)));
      list.appendChild(det);
    });
  }

  function card(it) {
    const c = h('div', 'sk-card' + (it.enabled ? '' : ' off'));
    c.dataset.name = it.name;
    const row = h('div', 'sk-row');
    row.innerHTML = `<span class="sk-title">${esc(it.title || it.name)}</span><span class="sk-name">${esc(it.name)}</span>` +
      `<span class="sk-badge ${esc(it.source)}">${esc(SRC[it.source] || it.source)}</span>` +
      (it.version ? `<span class="sk-badge">v${esc(it.version)}</span>` : '') +
      `<span class="sk-badge" title="对话里实际用到的次数">用过 ${it.use_count || 0} 次</span><span class="sk-sp"></span>`;
    const sw = h('label', 'sk-sw'); sw.title = it.enabled ? '已启用 (点击停用)' : '已停用 (点击启用)';
    const cb = h('input'); cb.type = 'checkbox'; cb.checked = !!it.enabled; cb.className = 'sk-toggle';
    sw.appendChild(cb); sw.appendChild(h('span'));
    cb.onchange = async () => {
      cb.disabled = true;
      try {
        const d = await api(`/api/skills/${encodeURIComponent(it.name)}/toggle`, {method: 'POST', json: {enabled: cb.checked}});
        it.enabled = d.enabled; c.classList.toggle('off', !d.enabled);
        if (typeof toast === 'function') toast(`${it.title || it.name} 已${d.enabled ? '启用' : '停用'}`);
      } catch (e) { cb.checked = !cb.checked; if (typeof toast === 'function') toast('操作失败: ' + e.message); }
      cb.disabled = false;
    };
    row.appendChild(sw);
    c.appendChild(row);
    c.appendChild(h('div', 'sk-desc', it.description || ''));
    const acts = h('div', 'sk-row');
    const bView = h('button', 'sk-btn', '查看'); bView.type = 'button';
    const aExp = h('a', 'sk-btn', '导出'); aExp.href = `/api/skills/${encodeURIComponent(it.name)}/export`; aExp.download = it.name + '.zip';
    aExp.style.textDecoration = 'none';
    acts.appendChild(bView); acts.appendChild(aExp);
    if (it.origin_url) {
      const a = h('a', 'sk-btn', '来源'); a.href = it.origin_url; a.target = '_blank'; a.rel = 'noopener'; a.style.textDecoration = 'none';
      acts.appendChild(a);
    }
    if (it.source !== 'builtin') {
      const bDel = h('button', 'sk-btn dng', '卸载'); bDel.type = 'button';
      armButton(bDel, '卸载', async () => {
        try {
          await api(`/api/skills/${encodeURIComponent(it.name)}`, {method: 'DELETE'});
          c.remove();
          if (typeof toast === 'function') toast(`已卸载 ${it.title || it.name}`);
        } catch (e) { if (typeof toast === 'function') toast('卸载失败: ' + e.message); }
      });
      acts.appendChild(bDel);
    }
    c.appendChild(acts);
    const det = h('div', 'sk-detail');
    c.appendChild(det);
    bView.onclick = async () => {
      if (!det.classList.toggle('on') || det.childNodes.length) return;
      det.textContent = '加载中…';
      try {
        const d = await api('/api/skills/' + encodeURIComponent(it.name));
        det.textContent = '';
        if ((d.aliases || []).length) det.appendChild(h('div', 'sk-files', '触发词: ' + d.aliases.join(' / ')));
        det.appendChild(h('pre', 'sk-pre', d.body || ''));
        det.appendChild(h('div', 'sk-files', '文件: ' + (d.files || []).map(f => f.path).join('  ')));
      } catch (e) { det.textContent = '加载失败: ' + e.message; }
    };
    return c;
  }

  /* ── 红点 ── */
  function setDot(n) {
    const dot = document.getElementById('sk-dot');
    if (dot) { dot.classList.toggle('on', n > 0); dot.title = n ? `${n} 个技能草稿待审核` : ''; }
  }
  async function refreshDot() {
    try { const d = await api('/api/skills/drafts'); setDot((d.items || []).length); } catch (e) { /* 未登录等 */ }
  }
  function watch() {
    const s = typeof streaming !== 'undefined' && !!streaming;
    if (wasStreaming && !s) refreshDot();          // 一轮结束: 模型可能刚建了草稿
    wasStreaming = s;
  }
  if (!timer) {
    timer = setInterval(refreshDot, 60000);
    setInterval(watch, 2000);
    setTimeout(refreshDot, 1500);
  }

  window.openSkills = open;
  window.LCSkillsPanel = {open, load, refreshDot, switchTab};
})();
