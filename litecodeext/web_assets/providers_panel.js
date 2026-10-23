/* providers_panel.js — 「🔌 能力接入」面板 (全屏视图, 左右两栏), 面向不懂技术的普通用户。
 * 入口: 侧栏左上角「🔌 能力接入」→ openProviders()。数据: /api/providers* (routers/providers_router.py)。
 * 一张卡 = 标题 + 一段明文 (一两句大白话 + Key/地址 + curl 示例)。其余字段 (类型/协议/地址/Key) 放进折叠的「高级 (可不填)」,
 * 不填时后端从明文自动识别 (lib/provider_infer.py)。
 * 左栏: 按类型分组 (组标题下一句大白话), 每项 = ✅/⚪ + 标题 (✅ = 内置/自动发现, 或最近一次测试通过)。
 * 右栏: 详情 / 「+ 新增」= 按类别列出所有预设标题, 点一个就地展开「去哪拿」说明 + 明文框 + 「测试并保存」。
 * 测试失败显示 hint (后端 lib/provider_hints.py 翻译的人话原因), 可「仍然保存」。不用 prompt 弹窗。
 * 窄屏 (<760px): 先列表, 点进去看详情, 顶部「← 返回」。引用 = provider_ref.js 的 @ chip。 */
(function () {
  'use strict';
  const ORDER = ['image', 'video', 'tts', 'asr', 'music', 'search', 'notify', 'storage', 'embedding', 'rerank', 'other'];
  const COLLAPSE_KEY = 'lc.pv.collapsed2';
  let D = {providers: [], builtins: [], kinds: {}, protocols: {}}, presets = [];
  let sel = null, mode = 'view', openPreset = null, newKind = null, testRes = {}, full = {}, seq = 0;
  let collapsed = new Set(['embedding', 'rerank']);
  try { const c = localStorage.getItem(COLLAPSE_KEY); if (c) collapsed = new Set(JSON.parse(c)); } catch (e) {}

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
  const presetOf = key => presets.find(x => x.key === key);
  const isOk = p => !!(p.readonly || (p.last_test && p.last_test.ok));
  const enc = encodeURIComponent;

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
          <span class="pv-sub">接上画图、做视频、配音、搜索、通知等服务, LiteCode 就能直接用</span></div>
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
    const html = [];
    ORDER.forEach(k => {
      const kd = kindOf(k);
      const items = all().filter(p => p.kind === k);
      const closed = collapsed.has(k) && !items.some(p => p.id === sel);
      html.push(`<div class="pv-group${closed ? ' closed' : ''}" data-kind="${k}">
        <button class="pv-gh" data-toggle="${k}" aria-expanded="${!closed}"><span class="pv-caret">▾</span>
          <span class="pv-gi">${kd.icon}</span><b>${esc(kd.label)}</b>${items.length ? `<span class="pv-cnt">${items.length}</span>` : ''}
          <span class="pv-gd">${esc(kd.desc)}</span></button><div class="pv-gb">`);
      items.forEach(p => {
        const ok = isOk(p);
        html.push(`<button class="pv-item${p.id === sel ? ' on' : ''}${p.enabled ? '' : ' dis'}" data-id="${esc(p.id)}" title="${esc(p.note || p.name)}">
          <span class="pv-mark" title="${ok ? '已接好' : '还没测试通过'}">${ok ? '✅' : '⚪'}</span>
          <span class="t">${esc(p.name)}</span>${p.default && !p.readonly ? '<span class="pv-badge">默认</span>' : ''}${p.auto ? '<span class="pv-badge ro">自动发现</span>' : (p.readonly ? '<span class="pv-badge ro">内置</span>' : '')}</button>`);
      });
      html.push(`<button class="pv-add" data-addkind="${k}">+ 添加${esc(kd.label)}服务</button></div></div>`);
    });
    $('#pv-list').innerHTML = html.join('');
  }

  function onListClick(e) {
    const t = e.target.closest('[data-toggle]');
    if (t) {
      const k = t.dataset.toggle;
      collapsed.has(k) ? collapsed.delete(k) : collapsed.add(k);
      try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsed])); } catch (err) {}
      t.parentElement.classList.toggle('closed', collapsed.has(k));
      t.setAttribute('aria-expanded', !collapsed.has(k));
      return;
    }
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

  function intro() {
    showDetailPane(false);
    $('#pv-detail').innerHTML = `<div class="pv-intro"><div class="pv-intro-ic">🔌</div>
      <h3>每个能力就是一张卡: 标题 + 一段话</h3>
      <p>这段话写清楚「是什么服务、Key 或地址是什么、怎么调用」, LiteCode 读了就会用。</p>
      <p>点「+ 新增」, 选一个服务, 照着说明把 Key / 地址贴进去, 再点「测试并保存」就好了。</p>
      <p class="pv-intro-s">左边带 ✅ 的是已经接好的 (包括 LiteCode 自带、自动发现的); ⚪ 是还没测试通过的。</p>
      <button class="pv-btn pv-primary" data-act="new">+ 新增接入</button></div>`;
  }

  async function getFull(id) {
    if (!full[id]) full[id] = await api('/api/providers/' + enc(id));
    return full[id];
  }

  function renderDetail() {
    const box = $('#pv-detail');
    const p = sel && byId(sel);
    if (!p) return intro();
    showDetailPane(true);
    const kd = kindOf(p.kind), tr = testRes[p.id], pre = presetOf(p.preset), lt = p.last_test;
    const status = p.readonly ? `<span class="pv-st ok">✅ 已接好${p.auto ? ' · 自动发现' : ' · 内置'}</span>`
      : (lt && lt.ok ? '<span class="pv-st ok">✅ 已接好 (最近一次测试通过)</span>'
        : `<span class="pv-st">⚪ ${lt ? '最近一次测试没通过' : '还没测试过'}</span>`);
    box.innerHTML = `<button class="pv-back" data-act="back">← 返回列表</button>
      <div class="pv-dh"><span class="pv-dic">${kd.icon}</span><div class="pv-dn"><h3>${esc(p.name)}</h3>
        <div class="pv-tags">${status}<span class="pv-tag">${esc(kd.label)}</span>
        ${p.default && !p.readonly ? '<span class="pv-tag acc">默认</span>' : ''}${p.enabled ? '' : '<span class="pv-tag off">已停用</span>'}</div></div></div>
      ${p.note ? `<p class="pv-note">${esc(p.note)}</p>` : ''}
      <div class="pv-acts">
        <button class="pv-btn" data-act="test">测试</button>
        ${p.readonly ? '<button class="pv-btn" data-act="adopt" title="复制成一张普通卡片, 之后可以随意修改">转为可编辑卡片</button>'
          : `<button class="pv-btn" data-act="edit">修改</button>
        <button class="pv-btn" data-act="default"${p.default ? ' disabled' : ''}>${p.default ? '已是默认' : '设为默认'}</button>
        <label class="pv-switch" title="启用 / 停用"><input type="checkbox" data-act="enable"${p.enabled ? ' checked' : ''}><span></span>启用</label>`}
        <button class="pv-btn" data-act="ref" title="把这张卡插进对话输入框, 随消息发给 LiteCode">引用到对话</button>
        ${p.readonly ? '' : '<button class="pv-btn pv-danger" data-act="del">删除</button>'}
      </div>
      <div class="pv-test" id="pv-test">${tr ? testHtml(tr) : (lt && !lt.ok && lt.msg ? testHtml({ok: false, hint: lt.msg}) : '')}</div>
      ${pre && pre.how ? `<div class="pv-how"><b>怎么接入</b><p>${esc(pre.how)}</p>${docLink(pre)}</div>` : ''}
      <div class="pv-uh">这段话 <small>LiteCode 调用时看到的就是它</small><button class="pv-link-btn" data-act="copy">复制</button></div>
      <pre class="pv-usage" id="pv-usage">${esc(p.usage || '(空)')}</pre>
      <details class="pv-adv"><summary>高级</summary><div class="pv-fields" id="pv-fields">加载中…</div></details>`;
    getFull(p.id).then(d => {
      if (sel !== p.id) return;
      const q = d.provider || {};
      if (!p.readonly) $('#pv-usage').textContent = q.usage || '(空)';
      const row = (k, v) => v ? `<div class="pv-kv"><span>${k}</span><div><code>${esc(v)}</code></div></div>` : '';
      const ex = Object.entries(q.extra || {}).map(([k, v]) => `${k} = ${typeof v === 'object' ? JSON.stringify(v) : v}`).join('\n');
      $('#pv-fields').innerHTML = row('协议', (D.protocols[q.protocol] || q.protocol) + ` (${q.protocol})`) + row('地址', q.base_url)
        + row('Key', p.has_key ? p.api_key : '') + row('模型', q.model) + row('extra', ex)
        + (q.inferred && q.inferred.length ? `<div class="pv-kv"><span>自动识别</span><div>${esc(q.inferred.join(', '))}</div></div>` : '')
        + `<div class="pv-kv"><span>完整明文</span><div><pre class="pv-usage sm">${esc(d.card || '')}</pre></div></div>`;
    }).catch(err => { const f = $('#pv-fields'); if (f) f.textContent = '加载失败: ' + err.message; });
  }

  function docLink(pre) {
    if (!pre || !pre.doc) return '';
    return /^https?:/.test(pre.doc) ? `<a class="pv-doc" href="${esc(pre.doc)}" target="_blank" rel="noopener">官方文档 ↗</a>`
      : `<span class="pv-doc">说明见 ${esc(pre.doc)}</span>`;
  }

  function testHtml(r) {
    if (r.running) return '<div class="pv-res run">测试中…</div>';
    if (r.ok && r.untested) return `<div class="pv-res ok">💾 ${esc(r.detail || '已保存')}</div>`;
    if (r.ok) return `<div class="pv-res ok">✅ 测试通过<span class="ms">${r.ms != null ? r.ms + ' ms' : ''}</span>
      <div class="msg">${esc(r.detail || '')}</div></div>`;
    return `<div class="pv-res bad">❌ ${esc(r.hint || '测试没通过')}${r.ms != null ? `<span class="ms">${r.ms} ms</span>` : ''}
      ${r.error && r.error !== r.hint ? `<div class="msg raw">原始返回: ${esc(r.error)}</div>` : ''}</div>`;
  }

  let delArmed = 0;
  async function onDetailClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const act = b.dataset.act, p = sel && byId(sel);
    if (act === 'new') return startNew(null);
    if (act === 'back') { sel = null; mode = 'view'; renderList(); renderDetail(); return; }
    if (b.closest('.pv-form') || b.closest('.pv-pick')) return;   /* 新增/修改表单各自绑定 */
    if (!p) return;
    try {
      if (act === 'test') {
        testRes[p.id] = {running: true}; $('#pv-test').innerHTML = testHtml(testRes[p.id]);
        testRes[p.id] = await api(`/api/providers/${enc(p.id)}/test`, {method: 'POST'});
        if (sel === p.id) { await load(true); }
      } else if (act === 'adopt') {
        const d = await api(`/api/providers/${enc(p.id)}/adopt`, {method: 'POST'});
        toastMsg('已转为可编辑卡片: ' + d.provider.name);
        sel = d.provider.id; await load(true);
        startEdit(byId(sel));
      } else if (act === 'default') {
        await api(`/api/providers/${enc(p.id)}/default`, {method: 'POST'});
        toastMsg(`已设为${kindOf(p.kind).label}默认: ${p.name}`); await load(true);
      } else if (act === 'enable') {
        await api('/api/providers/' + enc(p.id), {method: 'PUT', json: {enabled: b.checked}});
        delete full[p.id];
        toastMsg(b.checked ? '已启用' : '已停用'); await load(true);
      } else if (act === 'edit') {
        startEdit(p);
      } else if (act === 'ref') {
        if (window.LCProviderRefs) { LCProviderRefs.add({id: p.id, name: p.name, kind: p.kind}); closeProviders();
          const mi = document.getElementById('mi'); if (mi) mi.focus(); toastMsg('已插入输入框: ' + p.name); }
      } else if (act === 'copy') {
        const d = await getFull(p.id);
        try { await navigator.clipboard.writeText((d.provider || {}).usage || ''); toastMsg('已复制'); } catch (err) { toastMsg('复制失败, 请手动选中'); }
      } else if (act === 'del') {
        if (Date.now() - delArmed > 4000) {
          delArmed = Date.now(); b.classList.add('arm'); b.textContent = '再点一次确认删除';
          setTimeout(() => { if (b.isConnected && Date.now() - delArmed >= 3900) { b.classList.remove('arm'); b.textContent = '删除'; } }, 4000);
          return;
        }
        delArmed = 0;
        await api('/api/providers/' + enc(p.id), {method: 'DELETE'});
        delete full[p.id]; toastMsg('已删除: ' + p.name); sel = null; await load(false);
      }
    } catch (err) {
      toastMsg('❌ ' + err.message);
      if (act === 'test') { testRes[p.id] = {ok: false, error: err.message}; $('#pv-test').innerHTML = testHtml(testRes[p.id]); }
      if (act === 'enable') b.checked = !b.checked;
    }
  }

  /* ── 新增: 按类别列出所有预设标题, 点一个就地展开 ── */
  function startNew(kind) {
    mode = 'new'; newKind = kind; openPreset = null;
    showDetailPane(true);
    const groups = {};
    presets.forEach(x => { (groups[x.kind] = groups[x.kind] || []).push(x); });
    const kinds = ORDER.filter(k => groups[k]);
    $('#pv-detail').innerHTML = `<button class="pv-back" data-act="back">← 返回列表</button>
      <div class="pv-pick"><h3>新增接入</h3><p class="pv-pick-s">点一个服务, 照说明把 Key / 地址贴进那段话里, 再点「测试并保存」。</p>
      ${kinds.map(k => `<section class="pv-pg" data-kind="${k}"><h4>${kindOf(k).icon} ${esc(kindOf(k).label)}
        <small>${esc(kindOf(k).desc)}</small></h4>${groups[k].map(x => `<div class="pv-pi" data-key="${esc(x.key)}">
          <button type="button" class="pv-pih" data-preset="${esc(x.key)}" aria-expanded="false"><span class="t">${esc(x.label)}</span>
          <span class="pv-caret">▸</span></button><div class="pv-pib"></div></div>`).join('')}</section>`).join('')}</div>`;
    const pick = $('.pv-pick');
    pick.addEventListener('click', ev => {
      const h = ev.target.closest('[data-preset]');
      if (h) togglePreset(h.dataset.preset);
    });
    if (kind) {
      const s = pick.querySelector(`.pv-pg[data-kind="${kind}"]`);
      if (s) { s.classList.add('hl'); s.scrollIntoView({block: 'start'}); }
    }
  }

  function togglePreset(key) {
    document.querySelectorAll('.pv-pi').forEach(it => {
      const on = it.dataset.key === key && openPreset !== key;
      it.classList.toggle('open', on);
      it.querySelector('.pv-pih').setAttribute('aria-expanded', on);
      const body = it.querySelector('.pv-pib');
      body.innerHTML = on ? formHtml(presetOf(key), null) : '';
      if (on) { bindForm(body, presetOf(key), null); }
    });
    openPreset = openPreset === key ? null : key;
    const it = openPreset && document.querySelector(`.pv-pi[data-key="${CSS.escape(openPreset)}"]`);
    if (it) it.scrollIntoView({block: 'nearest'});
  }

  /* ── 修改: 同一张表单, 先取明文 (列表里的 Key 是打码的) ── */
  async function startEdit(p) {
    mode = 'edit';
    showDetailPane(true);
    const box = $('#pv-detail');
    box.innerHTML = '<div class="pv-empty">加载中…</div>';
    let q;
    try { delete full[p.id]; q = (await getFull(p.id)).provider; } catch (e) { box.innerHTML = `<div class="pv-empty">加载失败: ${esc(e.message)}</div>`; return; }
    box.innerHTML = `<button class="pv-back" data-act="back">← 返回列表</button><div class="pv-editw"><h3>修改「${esc(p.name)}」</h3>${formHtml(presetOf(q.preset), q)}</div>`;
    bindForm($('.pv-editw'), presetOf(q.preset), q);
  }

  function formHtml(pre, q) {
    const v = q || {name: pre ? pre.label : '', usage: pre ? pre.usage : '', kind: pre ? pre.kind : (newKind || 'other'),
      protocol: pre ? pre.protocol : 'http', base_url: '', api_key: '', model: '', extra: pre ? pre.extra : {}};
    const kindSel = ORDER.map(k => `<option value="${k}"${v.kind === k ? ' selected' : ''}>${esc(kindOf(k).icon + ' ' + kindOf(k).label)}</option>`).join('');
    const protoSel = Object.entries(D.protocols).filter(([k]) => k !== 'builtin' || v.protocol === 'builtin')
      .map(([k, t]) => `<option value="${k}"${v.protocol === k ? ' selected' : ''}>${esc(t)}</option>`).join('');
    const ex = Object.keys(v.extra || {}).length ? JSON.stringify(v.extra, null, 1) : '';
    const inp = (id, label, val, ph) => `<label class="pv-fl"><span class="pv-fl-t">${label}</span><input data-f="${id}" value="${esc(val)}" data-orig="${esc(val)}" placeholder="${esc(ph || '')}" spellcheck="false"></label>`;
    return `<form class="pv-form" autocomplete="off">
      ${pre && pre.how ? `<div class="pv-how"><b>去哪拿</b><p>${esc(pre.how)}</p>${docLink(pre)}${pre.verified ? '' : '<p class="pv-warn">⚠ 接口细节没能从官方文档确认, 请以官方页面为准。</p>'}</div>` : ''}
      <label class="pv-fl"><span class="pv-fl-t">标题</span><input data-f="name" required maxlength="80" value="${esc(v.name)}" placeholder="如: 运营群钉钉机器人"></label>
      <label class="pv-fl"><span class="pv-fl-t">这段话 <small>把「粘贴…」换成你自己的 Key / 地址; 也可以整段自己写</small></span>
        <textarea data-f="usage" rows="${Math.min(16, Math.max(6, (v.usage || '').split('\n').length + 1))}" class="mono" spellcheck="false">${esc(v.usage)}</textarea></label>
      <details class="pv-adv"><summary>高级 (可不填, 留空会从上面这段话自动识别)</summary>
        <div class="pv-row2"><label class="pv-fl"><span class="pv-fl-t">类型</span><select data-f="kind" data-orig="${esc(v.kind)}">${kindSel}</select></label>
          <label class="pv-fl"><span class="pv-fl-t">调用方式</span><select data-f="protocol" data-orig="${esc(v.protocol)}">${protoSel}</select></label></div>
        ${inp('base_url', '地址', v.base_url, '留空 = 自动识别')}
        <div class="pv-row2">${inp('api_key', 'Key / 密钥', v.api_key, '留空 = 自动识别')}${inp('model', '模型', v.model, '留空 = 自动识别')}</div>
        <label class="pv-fl"><span class="pv-fl-t">extra <small>(JSON, 附加参数)</small></span>
          <textarea data-f="extra" rows="3" class="mono" spellcheck="false" data-orig="${esc(ex)}">${esc(ex)}</textarea></label>
      </details>
      <div class="pv-test pf-test"></div>
      <div class="pv-facts"><button type="submit" class="pv-btn pv-primary pf-save">测试并保存</button>
        <button type="button" class="pv-btn pf-force" hidden>仍然保存</button>
        <button type="button" class="pv-btn pf-cancel">取消</button></div>
    </form>`;
  }

  function bindForm(box, pre, q) {
    const f = $('.pv-form', box), out = $('.pf-test', box), F = n => f.querySelector(`[data-f="${n}"]`);
    function collect() {
      const name = F('name').value.trim();
      if (!name) { out.innerHTML = testHtml({ok: false, hint: '请先写个标题'}); F('name').focus(); return null; }
      const body = {name, usage: F('usage').value};
      ['kind', 'protocol', 'base_url', 'api_key', 'model'].forEach(k => {
        const el = F(k), val = el.value.trim();
        /* 新建: 预设带来的类型/调用方式照发, 其余留空不发 (交给自动识别); 修改: 只发动过的高级字段 */
        if (q ? val !== el.dataset.orig : (val && (k === 'kind' || k === 'protocol' || val !== el.dataset.orig))) body[k] = val;
      });
      const raw = F('extra').value.trim();
      if (!q || raw !== F('extra').dataset.orig) {
        if (raw) {
          try { const x = JSON.parse(raw); if (!x || typeof x !== 'object' || Array.isArray(x)) throw new Error('要是 {…} 对象'); body.extra = x; }
          catch (err) { out.innerHTML = testHtml({ok: false, hint: '高级里的 extra 不是合法 JSON: ' + err.message}); return null; }
        } else if (q) body.extra = {};
      }
      if (!q && pre) body.preset = pre.key;
      return body;
    }
    async function save(body, res) {
      if (res) body.last_test = {ok: !!res.ok && !res.untested, ts: Math.floor(Date.now() / 1000), msg: res.hint || res.detail || res.error || ''};
      const d = q ? await api('/api/providers/' + enc(q.id), {method: 'PUT', json: body})
        : await api('/api/providers', {method: 'POST', json: body});
      toastMsg((q ? '已保存: ' : '已添加: ') + d.provider.name);
      delete full[d.provider.id];
      if (res) testRes[d.provider.id] = res;
      mode = 'view'; sel = d.provider.id; collapsed.delete(d.provider.kind);
      await load(true);
      const on = document.querySelector('#pv-list .pv-item.on');
      if (on) on.scrollIntoView({block: 'nearest'});
    }
    f.onsubmit = async ev => {
      ev.preventDefault();
      const body = collect();
      if (!body) return;
      const btn = $('.pf-save', f), force = $('.pf-force', f);
      btn.disabled = true; force.hidden = true;
      out.innerHTML = testHtml({running: true});
      try {
        const res = await api('/api/providers/test', {method: 'POST', json: q ? Object.assign({id: q.id}, body) : body});
        if (res.ok) { await save(body, res); return; }
        out.innerHTML = testHtml(res);
        force.hidden = false;
        force.onclick = async () => { force.disabled = true; try { await save(collect() || body, res); } catch (err) { out.innerHTML = testHtml({ok: false, hint: err.message}); } finally { force.disabled = false; } };
      } catch (err) {
        out.innerHTML = testHtml({ok: false, hint: err.message});
      } finally { if (btn.isConnected) btn.disabled = false; }
    };
    $('.pf-cancel', f).onclick = () => { if (q) { mode = 'view'; renderDetail(); } else togglePreset(openPreset); };
    if (!q) { const u = F('usage'); u.focus(); const i = u.value.indexOf('粘贴'); if (i >= 0) u.setSelectionRange(i, i); }
  }

  /* ── 开关 ── */
  window.openProviders = function (id) {
    const el = root();
    if (typeof closeSB === 'function') { try { closeSB(); } catch (e) {} }
    el.classList.add('on');
    document.body.classList.add('pv-open');
    mode = 'view';
    full = {};
    if (id) sel = id;
    load(true);
  };
  window.closeProviders = function () {
    const el = document.getElementById('pv-root');
    if (el) el.classList.remove('on');
    document.body.classList.remove('pv-open');
  };
  window.LCProviders = {open: window.openProviders, close: window.closeProviders, cardOf: async id => {
    full[id] = full[id] || await api('/api/providers/' + enc(id));
    return full[id].card || '';
  }, list: async () => { const d = await api('/api/providers'); return d.providers.concat(d.builtins); }, kinds: () => D.kinds};
})();
