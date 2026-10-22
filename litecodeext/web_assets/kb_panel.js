// 📚 知识库面板 (接口 /api/kb/*, 后端 plugins/kb) + 回答里 kb_search 出处 [n] 的悬停/点击预览。
// 入口: 侧栏工具箱「📚 知识库」按钮 → openKB()。库列表 (新建/重命名/删除, 卡片内表单与二次确认, 不用浏览器弹窗)
// → 库详情: 拖拽多文件上传 (逐个上传带进度) / 文档状态 (排队/解析/向量化/完成/失败可重试) / 分块预览 / 检索测试。
// 删掉本文件的 <script> 即卸下前端; 后端未启用时面板只显示部署说明。
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const E = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ST = { status: null, bases: [], cur: null, docs: [], poll: null, uploading: 0 };
  const LABEL = { queued: '排队中', parsing: '解析中', embedding: '向量化中', done: '完成', failed: '失败' };
  const ACCEPT = '.pdf,.docx,.pptx,.xlsx,.txt,.md,.markdown,.csv,.json,.html,.htm,.png,.jpg,.jpeg,.webp,.bmp,.gif';

  async function api(path, opt = {}) {
    const o = { credentials: 'same-origin', ...opt };
    if (o.body && typeof o.body === 'string') o.headers = { 'Content-Type': 'application/json', ...(o.headers || {}) };
    const r = await fetch(path, o);
    let j = {};
    try { j = await r.json(); } catch (e) { /* 非 JSON */ }
    if (!r.ok || (j && j.ok === false)) throw new Error((j && (j.error || j.detail)) || ('HTTP ' + r.status));
    return j;
  }
  const say = m => { if (typeof window.toast === 'function') window.toast(m); };
  const size = n => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n > 1024 ? Math.round(n / 1024) + ' KB' : (n || 0) + ' B';
  const bd = () => $('#kb-bd');
  const isOpen = () => !!$('#pnl-kb')?.classList.contains('open');

  // ── 面板骨架 ──────────────────────────────────────────────
  function ensurePanel() {
    if ($('#pnl-kb')) return;
    const p = document.createElement('div');
    p.className = 'pnl'; p.id = 'pnl-kb';
    p.innerHTML = '<div class="pb_"><div class="ph_"><span class="pt_">📚 知识库</span>' +
      '<button class="abtn" data-act="refresh" style="font-size:11px;padding:2px 10px;margin-left:auto;margin-right:8px">🔄 刷新</button>' +
      '<button class="px_" data-act="close" title="关闭">×</button></div><div class="pbd_" id="kb-bd"></div></div>';
    p.addEventListener('click', e => { if (e.target === p) closeKB(); });
    p.addEventListener('click', onClick);
    p.addEventListener('keydown', onKey);
    p.addEventListener('change', onChange);
    document.body.appendChild(p);
  }
  function closeKB() { stopPoll(); if (typeof window.closeP === 'function') window.closeP('kb'); else $('#pnl-kb')?.classList.remove('open'); }
  window.openKB = function () {
    ensurePanel();
    if (typeof window.openP === 'function') window.openP('kb'); else $('#pnl-kb').classList.add('open');
    ST.cur ? openBase(ST.cur.id) : loadList();
  };

  // ── 库列表 ────────────────────────────────────────────────
  async function loadList() {
    stopPoll(); ST.cur = null;
    bd().innerHTML = '<div class="kb-empty">加载中…</div>';
    try { ST.status = await api('/api/kb/status'); } catch (e) { ST.status = { enabled: false, error: e.message }; }
    const s = ST.status;
    if (!s.enabled) {
      bd().innerHTML = `<div class="kb-note"><b>知识库未启用</b><br>${E(s.hint || s.error || '')}<br>
        服务器版: <code>bash scripts/kb_setup.sh</code> 一键起 PostgreSQL + pgvector 并写入配置; 详见 <code>docs/KNOWLEDGE_BASE.md</code>。</div>`;
      return;
    }
    if (!s.db_ok) { bd().innerHTML = `<div class="kb-note err"><b>连不上知识库数据库</b><br>${E(s.error)}</div>`; return; }
    try { ST.bases = (await api('/api/kb/bases')).bases || []; } catch (e) { bd().innerHTML = `<div class="kb-note err">${E(e.message)}</div>`; return; }
    renderList();
  }

  function renderList() {
    const s = ST.status || {};
    let h = s.model_ok === false ? '<div class="kb-note err">未找到本地向量模型 (model.onnx), 文档无法向量化, 部署见 docs/KNOWLEDGE_BASE.md</div>' : '';
    h += `<div class="kb-bar"><span class="kb-title">全部知识库 (${ST.bases.length})</span>
      <button class="abtn kb-primary" data-act="new">+ 新建知识库</button></div>
      <div class="kb-form" id="kb-new-form"><input id="kb-new-name" maxlength="80" placeholder="知识库名称, 如: 员工手册">
      <input id="kb-new-desc" maxlength="500" placeholder="说明 (可选): 放哪些资料、给谁用">
      <div class="kb-btns"><button class="abtn" data-act="new-cancel">取消</button><button class="abtn kb-primary" data-act="new-save">创建</button></div></div>`;
    if (!ST.bases.length) h += '<div class="kb-empty">还没有知识库。新建一个, 把手册、制度、产品资料拖进去, 对话里问「根据知识库…」即可。</div>';
    for (const b of ST.bases) {
      h += `<div class="kb-card" data-id="${b.id}">
        <div class="kb-row"><span class="kb-name" data-act="open">${E(b.name)}</span>
          <button class="abtn" data-act="open">打开</button><button class="abtn" data-act="rename">重命名</button>
          <button class="abtn dng" data-act="del">删除</button></div>
        ${b.description ? `<div class="kb-desc">${E(b.description)}</div>` : ''}
        <div class="kb-meta"><span class="kb-pill">文档 ${b.doc_count}</span><span class="kb-pill">分块 ${b.chunk_count}</span>
          ${b.pending ? `<span class="kb-pill run">处理中 ${b.pending}</span>` : ''}${b.failed ? `<span class="kb-pill bad">失败 ${b.failed}</span>` : ''}</div>
        <div class="kb-form"><input class="kb-rn-name" maxlength="80" value="${E(b.name)}"><input class="kb-rn-desc" maxlength="500" value="${E(b.description)}" placeholder="说明 (可选)">
          <div class="kb-btns"><button class="abtn" data-act="rename-cancel">取消</button><button class="abtn kb-primary" data-act="rename-save">保存</button></div></div>
        <div class="kb-confirm"><span>删除「${E(b.name)}」? 库内 ${b.doc_count} 份文档和原始文件会一并删除, 不可恢复。</span>
          <button class="abtn" data-act="del-cancel">取消</button><button class="abtn kb-danger" data-act="del-yes">确认删除</button></div>
      </div>`;
    }
    bd().innerHTML = h;
  }

  // ── 库详情 ────────────────────────────────────────────────
  async function openBase(id) {
    stopPoll();
    const b = ST.bases.find(x => x.id === id) || ST.cur || { id, name: '' };
    ST.cur = b;
    bd().innerHTML = `<div class="kb-bar"><button class="abtn" data-act="back">← 全部</button><span class="kb-title" id="kb-cur-title">${E(b.name)}</span></div>
      <div class="kb-drop" id="kb-drop" data-act="pick"><b>拖拽文件到这里</b>, 或点击选择 (可多选)
        <small>PDF / Word / PPT / Excel / TXT / Markdown / 网页 / 图片 (扫描件自动识别文字)</small></div>
      <input type="file" id="kb-file" multiple accept="${ACCEPT}" style="display:none">
      <div id="kb-ups"></div>
      <div class="kb-sec">检索测试</div>
      <div class="kb-search"><input id="kb-q" placeholder="输入问题, 看看能检索到哪些片段"><select id="kb-k"><option>3</option><option selected>5</option><option>8</option></select>
        <button class="abtn kb-primary" data-act="search">检索</button></div>
      <div id="kb-res"></div>
      <div class="kb-sec">文档 <span id="kb-doc-n"></span></div><div id="kb-docs"><div class="kb-empty">加载中…</div></div>`;
    const drop = $('#kb-drop');
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => upload([...(e.dataTransfer?.files || [])]));
    await refreshDocs();
  }

  async function refreshDocs() {
    if (!ST.cur) return;
    const id = ST.cur.id;
    let docs;
    try { docs = (await api(`/api/kb/bases/${id}/docs`)).docs || []; }
    catch (e) { const el = $('#kb-docs'); if (el) el.innerHTML = `<div class="kb-note err">${E(e.message)}</div>`; return; }
    if (!ST.cur || ST.cur.id !== id) return;
    ST.docs = docs;
    renderDocs();
    stopPoll();
    if (isOpen() && (ST.uploading || docs.some(d => d.status !== 'done' && d.status !== 'failed'))) ST.poll = setTimeout(refreshDocs, 2000);
  }
  function stopPoll() { if (ST.poll) { clearTimeout(ST.poll); ST.poll = null; } }

  function docPill(d) {
    const cls = d.status === 'done' ? 'ok' : d.status === 'failed' ? 'bad' : 'run';
    const pct = d.status === 'embedding' ? ` ${Math.round((d.progress || 0) * 100)}%` : '';
    return `<span class="kb-pill ${cls}">${LABEL[d.status] || E(d.status)}${pct}</span>`;
  }
  function renderDocs() {
    const el = $('#kb-docs');
    if (!el) return;
    $('#kb-doc-n').textContent = `(${ST.docs.length})`;
    if (!ST.docs.length) { el.innerHTML = '<div class="kb-empty">还没有文档, 拖文件到上面的虚线框。</div>'; return; }
    const open = new Set([...el.querySelectorAll('.kb-chunks.open')].map(x => x.closest('.kb-doc').dataset.id));
    const conf = new Set([...el.querySelectorAll('.kb-confirm.open')].map(x => x.closest('.kb-doc').dataset.id));
    el.innerHTML = ST.docs.map(d => `<div class="kb-doc" data-id="${d.id}">
      <div class="kb-row"><span class="kb-fn">${E(d.filename)}</span>${docPill(d)}
        ${d.status === 'embedding' ? `<span class="kb-prog"><i style="width:${Math.round((d.progress || 0) * 100)}%"></i></span>` : ''}
        ${d.status === 'done' ? '<button class="abtn" data-act="chunks">分块</button>' : ''}
        <button class="abtn" data-act="view">原文</button>
        ${d.status === 'failed' ? '<button class="abtn" data-act="retry">重试</button>' : ''}
        <button class="abtn dng" data-act="ddel">删除</button></div>
      <div class="kb-sub">${size(d.size)} · ${d.chunk_count} 块 · ${d.text_len || 0} 字 · ${E((d.created_at || '').replace('T', ' ').slice(0, 16))}</div>
      ${d.status === 'failed' && d.error ? `<div class="kb-err">${E(d.error)}</div>` : ''}
      <div class="kb-confirm${conf.has(String(d.id)) ? ' open' : ''}"><span>删除「${E(d.filename)}」及其分块和原始文件?</span>
        <button class="abtn" data-act="ddel-cancel">取消</button><button class="abtn kb-danger" data-act="ddel-yes">确认删除</button></div>
      <div class="kb-chunks${open.has(String(d.id)) ? ' open' : ''}"></div></div>`).join('');
    el.querySelectorAll('.kb-chunks.open').forEach(c => loadChunks(c, 0));
  }

  async function loadChunks(box, offset) {
    const id = box.closest('.kb-doc').dataset.id;
    if (!offset) box.innerHTML = '<div class="kb-empty">加载中…</div>';
    try {
      const cs = (await api(`/api/kb/docs/${id}/chunks?offset=${offset}&limit=30`)).chunks || [];
      const html = cs.map(c => `<div class="kb-chunk"><b>#${c.chunk_index + 1}${c.loc ? ' · ' + E(c.loc) : ''} · ${c.content.length} 字</b>${E(c.content)}</div>`).join('');
      box.querySelector('.kb-more')?.remove();
      if (!offset) box.innerHTML = html || '<div class="kb-empty">没有分块</div>'; else box.insertAdjacentHTML('beforeend', html);
      if (cs.length === 30) box.insertAdjacentHTML('beforeend', `<button class="abtn kb-more" data-act="more" data-off="${offset + 30}">加载更多</button>`);
    } catch (e) { box.innerHTML = `<div class="kb-note err">${E(e.message)}</div>`; }
  }

  // ── 上传 (逐个, XHR 带进度) ────────────────────────────────
  async function upload(files) {
    if (!files.length || !ST.cur) return;
    const ups = $('#kb-ups');
    const rows = files.map(f => {
      const r = document.createElement('div');
      r.className = 'kb-up';
      r.innerHTML = `<span class="kb-up-name">${E(f.name)} · ${size(f.size)}</span><span class="kb-prog"><i></i></span><span class="kb-up-st">等待</span>`;
      ups.appendChild(r);
      return r;
    });
    ST.uploading++;
    for (let i = 0; i < files.length; i++) {
      const r = rows[i], bar = r.querySelector('i'), st = r.querySelector('.kb-up-st');
      st.textContent = '上传中';
      try {
        const j = await xhrUpload(files[i], p => { bar.style.width = Math.round(p * 100) + '%'; });
        bar.style.width = '100%';
        st.textContent = j.duplicate ? '内容已在库中, 未重复入库' : '已上传, 排队入库';
      } catch (e) { st.textContent = '失败: ' + e.message; st.style.color = 'var(--dg)'; }
      refreshDocs();
    }
    ST.uploading--;
    setTimeout(() => rows.forEach(r => r.remove()), 6000);
    refreshDocs();
  }
  function xhrUpload(file, onp) {
    return new Promise((res, rej) => {
      const x = new XMLHttpRequest();
      x.open('POST', `/api/kb/bases/${ST.cur.id}/upload`);
      x.withCredentials = true;
      x.upload.onprogress = e => { if (e.lengthComputable) onp(e.loaded / e.total); };
      x.onload = () => {
        let j = {};
        try { j = JSON.parse(x.responseText); } catch (e) { /* 非 JSON */ }
        if (x.status >= 200 && x.status < 300 && j.ok !== false) res(j); else rej(new Error(j.error || j.detail || ('HTTP ' + x.status)));
      };
      x.onerror = () => rej(new Error('网络错误'));
      const fd = new FormData();
      fd.append('file', file, file.name);
      x.send(fd);
    });
  }

  // ── 检索测试 ──────────────────────────────────────────────
  function mark(text, q) {
    let h = E(text);
    const words = [...new Set((q.match(/[\u4e00-\u9fff]{2,}|[A-Za-z0-9][\w.\-]+/g) || []).flatMap(w =>
      /[\u4e00-\u9fff]/.test(w) && w.length > 2 ? Array.from({ length: w.length - 1 }, (_, i) => w.slice(i, i + 2)) : [w]))];
    for (const w of words.sort((a, b) => b.length - a.length).slice(0, 30)) {
      h = h.split(E(w)).join(`<mark>${E(w)}</mark>`);
    }
    return h;
  }
  async function doSearch() {
    const q = $('#kb-q').value.trim(), out = $('#kb-res');
    if (!q) { say('先输入要检索的问题'); return; }
    out.innerHTML = '<div class="kb-empty">检索中…</div>';
    const t0 = performance.now();
    try {
      const d = await api('/api/kb/search', { method: 'POST', body: JSON.stringify({ query: q, kb_id: ST.cur.id, top_k: +$('#kb-k').value }) });
      if (d.error) throw new Error(d.error);
      const rs = d.results || [];
      const ms = Math.round(performance.now() - t0);
      out.innerHTML = `<div class="kb-sub" style="font-size:11px;color:var(--tx3)">命中 ${rs.length} 处 · ${ms} ms</div>` + (rs.length ? rs.map((r, i) => `
        <div class="kb-hit" data-doc="${r.doc_id}" data-s="${r.hit_start}" data-e="${r.hit_end}">
          <div class="kb-hh"><b>[${i + 1}]</b> ${E(r.filename)}${r.loc ? ' · ' + E(r.loc) : ''}${r.also ? ` <span class="kb-sub">(同文另见 ${E(r.also)})</span>` : ''}
            <span class="kb-score" title="向量余弦 ${r.cos} · 关键词覆盖 ${r.coverage}">相关度 ${r.score.toFixed(2)}</span>
            <button class="abtn" data-act="hit-view" style="font-size:11px;padding:2px 8px">原文</button></div>
          <div class="kb-snip">${mark(r.snippet, q)}</div>
          <details><summary>返回给模型的上下文 (${(r.context || '').length} 字)</summary><pre>${mark(r.context || '', q)}</pre></details>
        </div>`).join('') : '<div class="kb-empty">没有达到相关度阈值的片段, 换个说法试试。</div>');
    } catch (e) { out.innerHTML = `<div class="kb-note err">${E(e.message)}</div>`; }
  }

  // ── 原文预览 (面板与回答出处共用) ───────────────────────────
  async function viewDoc(docId, start, end, q) {
    let v = $('#kb-view');
    if (!v) {
      v = document.createElement('div');
      v.id = 'kb-view';
      v.innerHTML = '<div class="kb-vbox"><div class="kb-vh"><span id="kb-vt"></span><a class="abtn" id="kb-vdl" target="_blank" rel="noopener">原始文件</a>' +
        '<button class="px_" id="kb-vx" title="关闭">×</button></div><div class="kb-pre" id="kb-vp"></div></div>';
      v.addEventListener('click', e => { if (e.target === v || e.target.id === 'kb-vx') v.classList.remove('open'); });
      document.body.appendChild(v);
    }
    v.classList.add('open');
    $('#kb-vp').textContent = '加载中…';
    $('#kb-vdl').href = `/api/kb/docs/${docId}/file`;
    const from = Math.max(0, (start || 0) - 300);
    try {
      const d = await api(`/api/kb/docs/${docId}/text?start=${from}&length=${(end || 0) - from + 3000}`);
      $('#kb-vt').textContent = d.doc.filename;
      const t = d.text || '';
      if (end > start) {
        const a = start - from, b = end - from;
        $('#kb-vp').innerHTML = (from > 0 ? '…' : '') + E(t.slice(0, a)) + '<mark>' + E(t.slice(a, b)) + '</mark>' + E(t.slice(b));
        $('#kb-vp mark')?.scrollIntoView({ block: 'center' });
      } else $('#kb-vp').innerHTML = q ? mark(t, q) : E(t);
    } catch (e) { $('#kb-vp').textContent = e.message; }
  }

  // ── 事件 ──────────────────────────────────────────────────
  async function onClick(e) {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const act = t.dataset.act, card = t.closest('.kb-card'), doc = t.closest('.kb-doc');
    const kid = card ? +card.dataset.id : 0, did = doc ? +doc.dataset.id : 0;
    try {
      switch (act) {
        case 'close': closeKB(); break;
        case 'refresh': ST.cur ? refreshDocs() : loadList(); break;
        case 'new': $('#kb-new-form').classList.add('open'); $('#kb-new-name').focus(); break;
        case 'new-cancel': $('#kb-new-form').classList.remove('open'); break;
        case 'new-save': {
          const name = $('#kb-new-name').value.trim();
          if (!name) { say('❌ 名称必填'); return; }
          const r = await api('/api/kb/bases', { method: 'POST', body: JSON.stringify({ name, description: $('#kb-new-desc').value }) });
          say('✅ 已创建知识库'); ST.bases.push({ ...r.base, doc_count: 0, chunk_count: 0 }); openBase(r.base.id); break;
        }
        case 'open': openBase(kid); break;
        case 'rename': card.querySelector('.kb-form').classList.add('open'); card.querySelector('.kb-rn-name').focus(); break;
        case 'rename-cancel': card.querySelector('.kb-form').classList.remove('open'); break;
        case 'rename-save':
          await api(`/api/kb/bases/${kid}`, { method: 'PUT', body: JSON.stringify({ name: card.querySelector('.kb-rn-name').value, description: card.querySelector('.kb-rn-desc').value }) });
          say('✅ 已保存'); loadList(); break;
        case 'del': card.querySelector('.kb-confirm').classList.add('open'); break;
        case 'del-cancel': card.querySelector('.kb-confirm').classList.remove('open'); break;
        case 'del-yes': await api(`/api/kb/bases/${kid}`, { method: 'DELETE' }); say('🗑 已删除'); loadList(); break;
        case 'back': loadList(); break;
        case 'pick': $('#kb-file').click(); break;
        case 'search': doSearch(); break;
        case 'chunks': { const b = doc.querySelector('.kb-chunks'); b.classList.toggle('open'); if (b.classList.contains('open')) loadChunks(b, 0); break; }
        case 'more': loadChunks(t.closest('.kb-chunks'), +t.dataset.off); break;
        case 'view': viewDoc(did, 0, 0); break;
        case 'hit-view': { const h = t.closest('.kb-hit'); viewDoc(+h.dataset.doc, +h.dataset.s, +h.dataset.e, $('#kb-q').value); break; }
        case 'retry': await api(`/api/kb/docs/${did}/retry`, { method: 'POST' }); say('已重新排队'); refreshDocs(); break;
        case 'ddel': doc.querySelector('.kb-confirm').classList.add('open'); break;
        case 'ddel-cancel': doc.querySelector('.kb-confirm').classList.remove('open'); break;
        case 'ddel-yes': await api(`/api/kb/docs/${did}`, { method: 'DELETE' }); say('🗑 已删除文档'); refreshDocs(); break;
      }
    } catch (err) { say('❌ ' + err.message); }
  }
  function onKey(e) {
    if (e.key !== 'Enter') return;
    if (e.target.id === 'kb-q') doSearch();
    else if (e.target.id === 'kb-new-name' || e.target.id === 'kb-new-desc') $('[data-act="new-save"]')?.click();
  }
  function onChange(e) {
    if (e.target.id === 'kb-file') { upload([...e.target.files]); e.target.value = ''; }
  }

  // ── 回答里的出处 [n]: 悬停看片段, 点击看原文 ─────────────────
  // 工具时间线里 kb_search 的参数显示为 "问题 [库:名] [top_k:n]" (core/history_utils._fmt_args),
  // 按同样参数再检索一次 (结果确定, 带缓存) 就能把 [n] 对回同一条资料。
  const cache = new Map(), pending = new Set();
  function parseArgs(raw) {
    raw = (raw || '').trim().replace(/…$/, '');
    if (raw.startsWith('{')) { try { const j = JSON.parse(raw); return { query: j.query, kb: j.kb, top_k: j.top_k }; } catch (e) { /* 截断 */ } }
    const kb = (raw.match(/ \[库:([^\]]+)\]/) || [])[1], k = (raw.match(/ \[top_k:(\d+)\]/) || [])[1];
    return { query: raw.replace(/ \[库:[^\]]+\]| \[top_k:\d+\]/g, '').trim(), kb, top_k: k ? +k : undefined };
  }
  const SKIP = '.tg,.tgb,.rsb,pre,code,.kb-cite,.tr_,.ti,a';
  function wrapCites(mb, key) {
    const res = cache.get(key) || [];
    if (!res.length) return;
    const walker = document.createTreeWalker(mb, NodeFilter.SHOW_TEXT, {
      acceptNode: n => (/\[\d{1,2}\]/.test(n.nodeValue) && !n.parentElement.closest(SKIP)) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const n of nodes) {
      const frag = document.createDocumentFragment();
      let last = 0;
      const s = n.nodeValue;
      s.replace(/\[(\d{1,2})\]/g, (m, d, off) => {
        if (+d < 1 || +d > res.length) return m;
        frag.append(s.slice(last, off));
        const c = document.createElement('span');
        c.className = 'kb-cite'; c.textContent = d; c.dataset.key = key; c.dataset.n = d;
        frag.append(c);
        last = off + m.length;
        return m;
      });
      if (last) { frag.append(s.slice(last)); n.replaceWith(frag); }
    }
  }
  async function decorate(mb) {
    const tns = mb.querySelectorAll('.tn[data-tool="kb_search"]');
    if (!tns.length) return;
    const ti = tns[tns.length - 1].closest('.ti');
    const a = parseArgs((ti.querySelector('.tac') || ti.querySelector('.ta') || {}).textContent);
    if (!a.query) return;
    const key = JSON.stringify([a.query, a.kb || '', a.top_k || 0]);
    if (!cache.has(key)) {
      if (pending.has(key)) return;
      pending.add(key);
      try {
        const d = await api('/api/kb/search', { method: 'POST', body: JSON.stringify({ query: a.query, kb: a.kb, top_k: a.top_k }) });
        cache.set(key, d.results || []);
      } catch (e) { cache.set(key, []); }
      pending.delete(key);
    }
    wrapCites(mb, key);
  }
  let scanT = null;
  function scan() { clearTimeout(scanT); scanT = setTimeout(() => document.querySelectorAll('#msgs .msg.ai .mb').forEach(decorate), 400); }
  // 悬停只显示片段里和问题最相关的一段 (按问题的中文双字/英文词在 260 字窗口内的命中数挑位置)
  function focus(text, q, n = 260) {
    if (text.length <= n) return mark(text, q);
    const grams = new Set((q.match(/[\u4e00-\u9fff]{2,}|[A-Za-z0-9][\w.\-]+/g) || []).flatMap(w =>
      /[\u4e00-\u9fff]/.test(w) ? Array.from({ length: w.length - 1 }, (_, i) => w.slice(i, i + 2)) : [w]));
    let best = 0, bestScore = -1;
    for (let s = 0; s <= text.length - n; s += 10) {
      const win = text.slice(s, s + n);
      let sc = 0;
      grams.forEach(g => { if (win.includes(g)) sc++; });
      if (sc > bestScore) { bestScore = sc; best = s; }
    }
    return (best > 0 ? '…' : '') + mark(text.slice(best, best + n), q) + (best + n < text.length ? '…' : '');
  }
  function tip() {
    let t = $('#kb-tip');
    if (!t) { t = document.createElement('div'); t.id = 'kb-tip'; document.body.appendChild(t); }
    return t;
  }
  document.addEventListener('mouseover', e => {
    const c = e.target.closest?.('.kb-cite');
    if (!c) return;
    const r = (cache.get(c.dataset.key) || [])[c.dataset.n - 1];
    if (!r) return;
    const t = tip();
    t.innerHTML = `<b>[${c.dataset.n}] ${E(r.filename)}${r.loc ? ' · ' + E(r.loc) : ''} · 相关度 ${r.score.toFixed(2)}</b>${focus(r.snippet || '', JSON.parse(c.dataset.key)[0])}`;
    t.style.display = 'block';
    const b = c.getBoundingClientRect(), w = t.offsetWidth, h = t.offsetHeight;
    t.style.left = Math.max(8, Math.min(b.left, innerWidth - w - 8)) + 'px';
    t.style.top = (b.bottom + h + 12 < innerHeight ? b.bottom + 6 : Math.max(8, b.top - h - 6)) + 'px';
  });
  document.addEventListener('mouseout', e => { if (e.target.closest?.('.kb-cite')) tip().style.display = 'none'; });
  document.addEventListener('click', e => {
    const c = e.target.closest?.('.kb-cite');
    if (!c) return;
    const r = (cache.get(c.dataset.key) || [])[c.dataset.n - 1];
    if (r) { tip().style.display = 'none'; viewDoc(r.doc_id, r.hit_start ?? 0, r.hit_end ?? 0); }
  });
  function watch() {
    const m = $('#msgs');
    if (!m) return setTimeout(watch, 500);
    new MutationObserver(scan).observe(m, { childList: true, subtree: true, characterData: true });
    scan();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch); else watch();
})();
