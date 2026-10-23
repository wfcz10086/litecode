/* providers_panel.js — 「🔌 能力接入」面板 (全屏视图, 左右两栏)。
 * 入口: 侧栏左上角「🔌 能力接入」→ openProviders()。数据: /api/providers* (routers/providers_router.py)。
 * 左栏: 按类型分组 (可折叠, 组标题旁一句用途), 每项只显示 类型图标 + 启用圆点 + 标题;
 * 右栏: 详情 (基本字段 + agent 看到的明文卡片) + 测试/设为默认/启用/编辑/引用/删除, 或嵌入式编辑表单 (不用 prompt 弹窗)。
 * 窄屏 (<760px): 先列表, 点进去看详情, 顶部「← 返回」。引用 = provider_ref.js 的 @ chip。 */
(function () {
  'use strict';
  const ORDER = ['image', 'video', 'tts', 'asr', 'search', 'music', 'embedding', 'rerank', 'notify', 'storage', 'other', 'llm'];
  const COLLAPSE_KEY = 'lc.pv.collapsed';
  let D = {providers: [], builtins: [], kinds: {}, protocols: {}}, presets = [];
  let sel = null, mode = 'view', draftKind = null, testRes = {}, cardCache = {}, seq = 0;
  let collapsed = new Set();
  try { collapsed = new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '[]')); } catch (e) {}

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const $ = (q, el) => (el || document).querySelector(q);
  const toastMsg = m => { if (typeof toast === 'function') toast(m); };
  async function api(url, opt) {
    const o = Object.assign({headers: {}, credentials: 'same-origin'}, opt || {});
    if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers['Content-Type'] = 'application/json'; delete o.json; }
    const r = await fetch(url, o);
    let d = {};
    try { d = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error((d && (typeof d.detail === 'string' ? d.detail : d.error)) || ('HTTP ' + r.status));
    return d;
  }
  const kindOf = k => D.kinds[k] || {label: k, icon: '🧩', desc: ''};
  const all = () => D.providers.concat(D.builtins);
  const byId = id => all().find(p => p.id === id);

  /* ── 外壳 ── */
  function root() {
    let el = document.getElementById('pv-root');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'pv-root';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', '能力接入');
    el.innerHTML = `<div class="pv-wrap">
      <header class="pv-top">
        <div class="pv-title"><span aria-hidden="true">🔌</span> 能力接入
          <span class="pv-sub">接上生图、视频、语音、搜索等服务, LiteCode 就能直接调用</span></div>
        <button class="pv-btn pv-primary" id="pv-new">+ 新增</button>
        <button class="pv-x" id="pv-close" title="关闭 (Esc)" aria-label="关闭">×</button>
      </header>
      <div class="pv-body" id="pv-body"><nav class="pv-list" id="pv-list" aria-label="接入列表"></nav>
        <section class="pv-detail" id="pv-detail"></section></div></div>`;
    document.body.appendChild(el);
    $('#pv-close', el).onclick = closeProviders;
    $('#pv-new', el).onclick = () => startNew(null);
    $('#pv-list', el).addEventListener('click', onListClick);
    $('#pv-detail', el).addEventListener('click', onDetailClick);
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && el.classList.contains('on') && !e.target.closest('.pv-form')) closeProviders();
    });
    return el;
  }

  async function load(keepSel) {
    const my = ++seq;
    if (window.LCProviderRefs) LCProviderRefs.invalidate();
    try {
      const [d, pr] = await Promise.all([api('/api/providers'), presets.length ? null : api('/api/providers/presets')]);
      if (my !== seq) return;
      D = d;
      if (pr) presets = pr.presets || [];
    } catch (e) {
      $('#pv-list').innerHTML = `<div class="pv-empty">加载失败: ${esc(e.message)}</div>`;
      return;
    }
    if (!keepSel || (sel && !byId(sel))) sel = null;
    renderList();
    if (mode === 'view') renderDetail();
  }

  /* ── 左栏 ── */
  function renderList() {
    const list = $('#pv-list');
    const html = [];
    ORDER.forEach(k => {
      const kd = kindOf(k);
      const items = k === 'llm' ? [] : all().filter(p => p.kind === k);
      const closed = collapsed.has(k);
      html.push(`<div class="pv-group${closed ? ' closed' : ''}" data-kind="${k}">
        <button class="pv-gh" data-toggle="${k}" aria-expanded="${!closed}"><span class="pv-caret">▾</span>
          <span class="pv-gi">${kd.icon}</span><b>${esc(kd.label)}</b>${items.length ? `<span class="pv-cnt">${items.length}</span>` : ''}
          <span class="pv-gd">${esc(kd.desc)}</span></button><div class="pv-gb">`);
      if (k === 'llm') {
        html.push(`<button class="pv-item pv-link" data-llm="1"><span class="pv-ic">🧠</span><span class="t">去「模型管理」配置大模型 →</span></button>`);
      } else if (!items.length) {
        html.push(`<button class="pv-add" data-addkind="${k}">+ 添加${esc(kd.label)}服务</button>`);
      }
      items.forEach(p => {
        html.push(`<button class="pv-item${p.id === sel ? ' on' : ''}" data-id="${esc(p.id)}" title="${esc(p.note || p.name)}">
          <span class="pv-ic">${kd.icon}</span><span class="pv-dot ${p.enabled ? 'en' : 'dis'}" title="${p.enabled ? '已启用' : '已停用'}"></span>
          <span class="t">${esc(p.name)}</span>${p.default ? '<span class="pv-badge">默认</span>' : ''}${p.readonly ? '<span class="pv-badge ro">内置</span>' : ''}</button>`);
      });
      html.push('</div></div>');
    });
    list.innerHTML = html.join('');
  }

  function onListClick(e) {
    const t = e.target.closest('[data-toggle]');
    if (t) {
      const k = t.dataset.toggle;
      collapsed.has(k) ? collapsed.delete(k) : collapsed.add(k);
      try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsed])); } catch (err) {}
      t.parentElement.classList.toggle('closed');
      t.setAttribute('aria-expanded', !collapsed.has(k));
      return;
    }
    if (e.target.closest('[data-llm]')) { closeProviders(); if (typeof openP === 'function') openP('settings'); return; }
    const a = e.target.closest('[data-addkind]');
    if (a) { startNew(a.dataset.addkind); return; }
    const it = e.target.closest('[data-id]');
    if (it) select(it.dataset.id);
  }

  function select(id) {
    sel = id; mode = 'view';
    document.querySelectorAll('#pv-list .pv-item').forEach(b => b.classList.toggle('on', b.dataset.id === id));
    renderDetail();
  }

  /* ── 右栏: 详情 ── */
  function showDetailPane(on) { $('#pv-body').classList.toggle('detail-open', !!on); }

  function renderDetail() {
    const box = $('#pv-detail');
    const p = sel && byId(sel);
    if (!p) {
      showDetailPane(false);
      const n = D.providers.length;
      box.innerHTML = `<div class="pv-intro"><div class="pv-intro-ic">🔌</div>
        <h3>${n ? `已接入 ${n} 个服务` : '还没有接入外部服务'}</h3>
        <p>一个服务 = 一张卡片, 核心是一段明文用法 (地址、key、curl 示例)。agent 读了就能调用;
        生图 / 生视频 / 配乐 / 通知等常用协议还能一键测试、自动路由。</p>
        <p>在对话输入框打 <code>@</code> 可以引用某个接入, 把它的完整用法随消息发给 LiteCode。</p>
        <button class="pv-btn pv-primary" data-act="new">+ 新增接入</button></div>`;
      return;
    }
    showDetailPane(true);
    const kd = kindOf(p.kind), tr = testRes[p.id];
    const row = (k, v, cls) => v ? `<div class="pv-kv"><span>${k}</span><div class="${cls || ''}">${v}</div></div>` : '';
    const extra = Object.entries(p.extra || {}).map(([k, v]) => `${esc(k)} = ${esc(typeof v === 'object' ? JSON.stringify(v) : v)}`).join('<br>');
    box.innerHTML = `<button class="pv-back" data-act="back">← 返回列表</button>
      <div class="pv-dh"><span class="pv-dic">${kd.icon}</span><div class="pv-dn"><h3>${esc(p.name)}</h3>
        <div class="pv-tags"><span class="pv-tag">${esc(kd.label)}</span><span class="pv-tag mono">${esc(p.protocol)}</span>
        ${p.default ? '<span class="pv-tag acc">默认</span>' : ''}${p.enabled ? '<span class="pv-tag ok">已启用</span>' : '<span class="pv-tag off">已停用</span>'}
        ${p.readonly ? '<span class="pv-tag">内置 · 只读</span>' : ''}</div></div></div>
      <div class="pv-acts">
        <button class="pv-btn" data-act="test">测试</button>
        ${p.readonly ? '' : `<button class="pv-btn" data-act="default"${p.default ? ' disabled' : ''}>${p.default ? '已是默认' : '设为默认'}</button>
        <label class="pv-switch" title="启用 / 停用"><input type="checkbox" data-act="enable"${p.enabled ? ' checked' : ''}><span></span>启用</label>
        <button class="pv-btn" data-act="edit">编辑</button>`}
        <button class="pv-btn" data-act="ref" title="把这个接入作为 chip 插进对话输入框">引用</button>
        ${p.readonly ? '' : '<button class="pv-btn pv-danger" data-act="del">删除</button>'}
      </div>
      <div class="pv-test" id="pv-test">${tr ? testHtml(tr) : ''}</div>
      <div class="pv-fields">
        ${row('用途', esc(p.note || kd.desc))}
        ${row('协议', esc(D.protocols[p.protocol] || p.protocol))}
        ${row('地址', p.base_url ? `<code>${esc(p.base_url)}</code>` : '')}
        ${row('模型', p.model ? `<code>${esc(p.model)}</code>` : '')}
        ${row('密钥', p.has_key ? `<code>${esc(p.api_key)}</code> <small>(明文见下方用法)</small>` : '')}
        ${row('extra', extra, 'mono')}
      </div>
      <div class="pv-uh">明文用法 <small>agent 调用 / 对话引用时看到的就是这段 (含 key)</small>
        <button class="pv-link-btn" data-act="copy">复制</button></div>
      <blockquote class="pv-usage" id="pv-usage">${cardCache[p.id] ? esc(cardCache[p.id]) : '加载中…'}</blockquote>`;
    if (!cardCache[p.id]) {
      api('/api/providers/' + encodeURIComponent(p.id)).then(d => {
        cardCache[p.id] = d.card || '';
        const u = document.getElementById('pv-usage');
        if (u && sel === p.id) u.textContent = cardCache[p.id];
      }).catch(err => { const u = document.getElementById('pv-usage'); if (u) u.textContent = '加载失败: ' + err.message; });
    }
  }

  function testHtml(r) {
    if (r.running) return '<div class="pv-res run">测试中…</div>';
    return `<div class="pv-res ${r.ok ? 'ok' : 'bad'}">${r.ok ? '✅ 测试通过' : '❌ 测试失败'}<span class="ms">${r.ms != null ? r.ms + ' ms' : ''}</span>
      <div class="msg">${esc(r.ok ? (r.detail || '') : (r.error || ''))}</div></div>`;
  }

  let delArmed = 0;
  async function onDetailClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const act = b.dataset.act, p = sel && byId(sel);
    if (act === 'new') return startNew(null);
    if (act === 'back') { sel = null; mode = 'view'; renderList(); renderDetail(); return; }
    if (b.closest('.pv-form')) return;   /* 表单按钮各自绑定 */
    if (!p) return;
    try {
      if (act === 'test') {
        testRes[p.id] = {running: true}; $('#pv-test').innerHTML = testHtml(testRes[p.id]);
        testRes[p.id] = await api(`/api/providers/${encodeURIComponent(p.id)}/test`, {method: 'POST'});
        if (sel === p.id) $('#pv-test').innerHTML = testHtml(testRes[p.id]);
      } else if (act === 'default') {
        await api(`/api/providers/${encodeURIComponent(p.id)}/default`, {method: 'POST'});
        cardCache = {};
        toastMsg(`已设为${kindOf(p.kind).label}默认: ${p.name}`); await load(true);
      } else if (act === 'enable') {
        await api('/api/providers/' + encodeURIComponent(p.id), {method: 'PUT', json: {enabled: b.checked}});
        delete cardCache[p.id];
        toastMsg(b.checked ? '已启用' : '已停用'); await load(true);
      } else if (act === 'edit') {
        startEdit(p);
      } else if (act === 'ref') {
        if (window.LCProviderRefs) { LCProviderRefs.add({id: p.id, name: p.name, kind: p.kind}); closeProviders();
          const mi = document.getElementById('mi'); if (mi) mi.focus(); toastMsg('已插入输入框: ' + p.name); }
      } else if (act === 'copy') {
        try { await navigator.clipboard.writeText(cardCache[p.id] || ''); toastMsg('已复制'); } catch (err) { toastMsg('复制失败, 请手动选中'); }
      } else if (act === 'del') {
        if (Date.now() - delArmed > 4000) {
          delArmed = Date.now(); b.classList.add('arm'); b.textContent = '再点一次确认删除';
          setTimeout(() => { if (b.isConnected && Date.now() - delArmed >= 3900) { b.classList.remove('arm'); b.textContent = '删除'; } }, 4000);
          return;
        }
        delArmed = 0;
        await api('/api/providers/' + encodeURIComponent(p.id), {method: 'DELETE'});
        delete cardCache[p.id]; toastMsg('已删除: ' + p.name); sel = null; await load(false);
      }
    } catch (err) {
      toastMsg('❌ ' + err.message);
      if (act === 'test') { testRes[p.id] = {ok: false, error: err.message}; $('#pv-test').innerHTML = testHtml(testRes[p.id]); }
      if (act === 'enable') b.checked = !b.checked;
    }
  }

  /* ── 右栏: 嵌入式编辑 ── */
  function startNew(kind) { mode = 'new'; draftKind = kind; renderForm(null); }
  function startEdit(p) { mode = 'edit'; renderForm(p); }

  function presetOptions() {
    const groups = {};
    presets.forEach((x, i) => { (groups[x.kind] = groups[x.kind] || []).push([x, i]); });
    return '<option value="">— 选预设模板 (可选) —</option>' + ORDER.filter(k => groups[k]).map(k =>
      `<optgroup label="${esc(kindOf(k).icon + ' ' + kindOf(k).label)}">${groups[k].map(([x, i]) =>
        `<option value="${i}">${esc(x.label)}${x.verified ? '' : ' (需按官方文档填写)'}</option>`).join('')}</optgroup>`).join('');
  }

  function renderForm(p) {
    showDetailPane(true);
    const box = $('#pv-detail');
    const v = p || {name: '', kind: draftKind || 'image', protocol: 'http', base_url: '', api_key: '', model: '', note: '', usage: '', extra: {}, enabled: true};
    const kindSel = ORDER.filter(k => k !== 'llm').map(k => `<option value="${k}"${v.kind === k ? ' selected' : ''}>${esc(kindOf(k).icon + ' ' + kindOf(k).label)}</option>`).join('');
    const protoSel = Object.entries(D.protocols).filter(([k]) => k !== 'builtin').map(([k, t]) => `<option value="${k}"${v.protocol === k ? ' selected' : ''}>${esc(k)} · ${esc(t)}</option>`).join('');
    box.innerHTML = `<button class="pv-back" data-act="back">← 返回列表</button>
      <form class="pv-form" id="pv-form" autocomplete="off">
        <h3>${p ? '编辑接入' : '新增接入'}</h3>
        ${p ? '' : `<label class="pv-fl"><span class="pv-fl-t">预设模板</span><select id="pf-preset">${presetOptions()}</select></label>
          <div class="pv-pnote" id="pf-pnote"></div>`}
        <div class="pv-row2"><label class="pv-fl"><span class="pv-fl-t">名称</span><input id="pf-name" required maxlength="80" value="${esc(v.name)}" placeholder="如: 公司 ComfyUI / 博查搜索"></label>
          <label class="pv-fl"><span class="pv-fl-t">类型</span><select id="pf-kind">${kindSel}</select></label></div>
        <div class="pv-row2"><label class="pv-fl"><span class="pv-fl-t">协议</span><select id="pf-proto">${protoSel}</select></label>
          <label class="pv-fl"><span class="pv-fl-t">模型 <small>(可选)</small></span><input id="pf-model" value="${esc(v.model)}"></label></div>
        <label class="pv-fl"><span class="pv-fl-t">地址 base_url</span><input id="pf-url" value="${esc(v.base_url)}" placeholder="https://… 或 host:port"></label>
        <label class="pv-fl"><span class="pv-fl-t">API Key / 密钥 <small>${p && p.has_key ? '(已保存, 不改就保持打码)' : '(明文存 config.json)'}</small></span><input id="pf-key" value="${esc(v.api_key)}" spellcheck="false"></label>
        <label class="pv-fl"><span class="pv-fl-t">一句话用途 <small>(agent 列表里看到)</small></span><input id="pf-note" maxlength="200" value="${esc(v.note)}"></label>
        <label class="pv-fl"><span class="pv-fl-t">extra <small>(JSON, 协议附加参数, 如 {"size_field": "image_size"} / {"secret": "…"})</small></span>
          <textarea id="pf-extra" rows="3" class="mono" spellcheck="false">${esc(Object.keys(v.extra || {}).length ? JSON.stringify(v.extra, null, 1) : '')}</textarea></label>
        <label class="pv-fl"><span class="pv-fl-t">明文用法 <small>(地址、鉴权、curl 示例; 可用 {api_key} {base_url} {model} {prompt} {query} {text} 占位)</small></span>
          <textarea id="pf-usage" rows="9" class="mono" spellcheck="false">${esc(v.usage)}</textarea></label>
        <label class="pv-ck"><input type="checkbox" id="pf-en"${v.enabled ? ' checked' : ''}> 启用</label>
        <div class="pv-test" id="pf-test"></div>
        <div class="pv-facts"><button type="submit" class="pv-btn pv-primary" id="pf-save">保存</button>
          <button type="button" class="pv-btn" id="pf-test-btn">测试</button>
          <button type="button" class="pv-btn" id="pf-cancel">取消</button></div>
      </form>`;
    const f = $('#pv-form');
    const pre = $('#pf-preset');
    if (pre) {
      pre.onchange = () => {
        const x = presets[+pre.value];
        const note = $('#pf-pnote');
        if (!x) { note.innerHTML = ''; return; }
        $('#pf-kind').value = x.kind; $('#pf-proto').value = x.protocol; $('#pf-url').value = x.base_url || '';
        $('#pf-model').value = x.model || ''; $('#pf-usage').value = x.usage || ''; $('#pf-note').value = x.note || '';
        $('#pf-extra').value = Object.keys(x.extra || {}).length ? JSON.stringify(x.extra, null, 1) : '';
        if (!$('#pf-name').value.trim() || $('#pf-name').dataset.auto) { $('#pf-name').value = x.label; $('#pf-name').dataset.auto = '1'; }
        note.innerHTML = (x.verified ? '<span class="ok">✓ 地址与参数已对照官方文档</span>' : '<span class="warn">⚠ 未能确认官方接口, 请按官方文档填写</span>')
          + (x.doc ? ` · <a href="${esc(x.doc)}" target="_blank" rel="noopener">文档</a>` : '');
      };
      if (draftKind) { const first = presets.findIndex(x => x.kind === draftKind); if (first >= 0) { pre.value = first; pre.onchange(); } }
    }
    $('#pf-name').oninput = e => { delete e.target.dataset.auto; };
    $('#pf-cancel').onclick = () => { mode = 'view'; renderDetail(); };
    $('#pf-test-btn').onclick = async () => {
      const body = collect();
      if (!body) return;
      if (p) body.id = p.id;
      $('#pf-test').innerHTML = testHtml({running: true});
      try { $('#pf-test').innerHTML = testHtml(await api('/api/providers/test', {method: 'POST', json: body})); }
      catch (err) { $('#pf-test').innerHTML = testHtml({ok: false, error: err.message}); }
    };
    f.onsubmit = async ev => {
      ev.preventDefault();
      const body = collect();
      if (!body) return;
      $('#pf-save').disabled = true;
      try {
        const d = p ? await api('/api/providers/' + encodeURIComponent(p.id), {method: 'PUT', json: body})
          : await api('/api/providers', {method: 'POST', json: body});
        toastMsg(p ? '已保存' : '已添加: ' + d.provider.name);
        delete cardCache[d.provider.id];
        mode = 'view'; sel = d.provider.id;
        collapsed.delete(d.provider.kind);
        await load(true);
      } catch (err) {
        $('#pf-test').innerHTML = testHtml({ok: false, error: err.message});
      } finally { const s = $('#pf-save'); if (s) s.disabled = false; }
    };
    const nm = $('#pf-name'); if (nm && !p) nm.focus();
  }

  function collect() {
    let extra = {};
    const raw = $('#pf-extra').value.trim();
    if (raw) {
      try { extra = JSON.parse(raw); if (!extra || typeof extra !== 'object' || Array.isArray(extra)) throw new Error('要是 JSON 对象'); }
      catch (err) { $('#pf-test').innerHTML = testHtml({ok: false, error: 'extra 不是合法 JSON 对象: ' + err.message}); return null; }
    }
    const name = $('#pf-name').value.trim();
    if (!name) { $('#pf-test').innerHTML = testHtml({ok: false, error: '名称不能为空'}); $('#pf-name').focus(); return null; }
    return {name, kind: $('#pf-kind').value, protocol: $('#pf-proto').value, base_url: $('#pf-url').value.trim(),
      api_key: $('#pf-key').value.trim(), model: $('#pf-model').value.trim(), note: $('#pf-note').value.trim(),
      usage: $('#pf-usage').value, extra, enabled: $('#pf-en').checked};
  }

  /* ── 开关 ── */
  window.openProviders = function (id) {
    const el = root();
    if (typeof closeSB === 'function') { try { closeSB(); } catch (e) {} }
    el.classList.add('on');
    document.body.classList.add('pv-open');
    mode = 'view';
    if (id) sel = id;
    load(true);
  };
  window.closeProviders = function () {
    const el = document.getElementById('pv-root');
    if (el) el.classList.remove('on');
    document.body.classList.remove('pv-open');
  };
  window.LCProviders = {open: window.openProviders, close: window.closeProviders, cardOf: async id => {
    if (!cardCache[id]) cardCache[id] = (await api('/api/providers/' + encodeURIComponent(id))).card || '';
    return cardCache[id];
  }, list: async () => { const d = await api('/api/providers'); return d.providers.concat(d.builtins); }, kinds: () => D.kinds};
})();
