// 画布分屏 (对标 ChatGPT Canvas): AI 回复里的长代码 / HTML 收成聊天流里的一张卡片,
// 内容在右侧常驻面板里看、改、复制、下载、全屏; html/svg/markdown 可直接预览。
// 判定规则在 canvas_core.js; 删掉这两个 <script> 和 canvas.css 即回到原样。
(function () {
  const C = window.CanvasCore;
  if (!C) return;

  const blocks = new Map();   // id → {lang, kind, text, title, pre}
  let seq = 0;
  let openId = null;
  let mode = 'code';
  let sawStreaming = false;
  let panel = null;

  const WIDTH_KEY = 'lc.canvas.width';
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // ── 聊天流: 长代码块 → 卡片 ─────────────────────────────
  function codeText(pre) {
    const code = pre.querySelector('code');
    return code ? code.textContent : '';
  }

  function makeCard(id, b) {
    const card = el('div', 'cv-card');
    card.dataset.cvId = id;
    card.tabIndex = 0;
    const icon = b.kind === 'html' || b.kind === 'svg' ? '🖼️' : b.kind === 'markdown' ? '📝' : '📄';
    const meta = el('div', 'cv-meta');
    meta.append(el('div', 'cv-title', b.title),
      el('div', 'cv-sub', `${b.lang || b.kind} · ${C.lineCount(b.text)} 行`));
    const inline = el('button', 'cv-inline', '展开');
    inline.type = 'button';
    inline.title = '在聊天里展开原代码';
    const go = el('button', 'cv-go', '在画布打开');
    go.type = 'button';
    card.append(el('span', 'cv-ic', icon), meta, inline, go);
    card.addEventListener('click', e => {
      if (e.target === inline) {
        const folded = b.pre.classList.toggle('cv-folded');
        inline.textContent = folded ? '展开' : '收起';
        return;
      }
      open(id);
    });
    card.addEventListener('keydown', e => { if (e.key === 'Enter') open(id); });
    return card;
  }

  function scan() {
    document.querySelectorAll('#msgs .msg.ai .mb pre').forEach(pre => {
      if (pre.dataset.cv || pre.closest('.sraw, .dv, .pv-card')) return;
      const code = pre.querySelector('code');
      const text = codeText(pre);
      const lang = C.langOf(code && code.className);
      if (!C.shouldCanvas(lang, text)) { pre.dataset.cv = '0'; return; }
      const id = 'cv' + (++seq);
      const b = { lang, kind: C.kindOf(lang, text), text, title: C.titleOf(lang, text), pre };
      blocks.set(id, b);
      pre.dataset.cv = id;
      pre.classList.add('cv-folded');
      pre.parentNode.insertBefore(makeCard(id, b), pre);
    });
    // 会话切换后 #msgs 整体重建, 旧块的 DOM 已不在
    for (const [id, b] of blocks) {
      if (!b.pre.isConnected) { blocks.delete(id); if (id === openId) close(); }
    }
  }

  function newestInLastReply() {
    const replies = document.querySelectorAll('#msgs .msg.ai');
    const last = replies[replies.length - 1];
    if (!last) return null;
    const cards = last.querySelectorAll('.cv-card');
    return cards.length ? cards[cards.length - 1].dataset.cvId : null;
  }

  // ── 面板 ────────────────────────────────────────────────
  function btn(label, act, title) {
    const b = el('button', 'cv-btn', label);
    b.type = 'button';
    b.dataset.act = act;
    if (title) b.title = title;
    return b;
  }

  function build() {
    panel = el('aside');
    panel.id = 'cvs';
    panel.hidden = true;
    const grip = el('div', 'cv-grip');
    grip.title = '拖动调整宽度';
    const hd = el('div', 'cv-hd');
    const tabs = el('div', 'cv-tabs');
    tabs.append(btn('预览', 'mode:preview'), btn('代码', 'mode:code'));
    hd.append(el('span', 'cv-hd-title'), el('span', 'cv-hd-lang'), tabs, el('span', 'cv-sp'),
      btn('编辑', 'edit', '直接修改, 复制和下载会用改过的内容'), btn('复制', 'copy'),
      btn('下载', 'download'), btn('⛶', 'full', '全屏'), btn('×', 'close', '关闭画布'));
    panel.append(grip, hd, el('div', 'cv-bd'));
    document.getElementById('app').appendChild(panel);

    const w = parseInt(store.get(WIDTH_KEY) || '', 10);
    if (w > 0) panel.style.width = w + 'px';
    hd.addEventListener('click', onAction);
    grip.addEventListener('pointerdown', startResize);
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape' || panel.hidden) return;
      if (panel.classList.contains('cv-full')) panel.classList.remove('cv-full');
      else close();
    });
  }

  function previewable(b) { return b.kind === 'html' || b.kind === 'svg' || b.kind === 'markdown'; }

  function render() {
    const b = blocks.get(openId);
    if (!b) return;
    panel.querySelector('.cv-hd-title').textContent = b.title;
    panel.querySelector('.cv-hd-lang').textContent = b.lang || b.kind;
    panel.querySelector('.cv-tabs').hidden = !previewable(b);
    panel.querySelectorAll('.cv-tabs .cv-btn').forEach(t =>
      t.classList.toggle('on', t.dataset.act === 'mode:' + mode));
    panel.querySelector('[data-act=edit]').classList.toggle('on', mode === 'edit');
    const bd = panel.querySelector('.cv-bd');
    bd.textContent = '';
    if (mode === 'edit') {
      const ta = el('textarea', 'cv-edit');
      ta.value = b.text;
      ta.spellcheck = false;
      ta.addEventListener('input', () => { b.text = ta.value; });
      bd.appendChild(ta);
      ta.focus();
    } else if (mode === 'preview' && b.kind === 'markdown') {
      const div = el('div', 'cv-md mb');
      try { div.innerHTML = marked.parse(b.text); } catch (_) { div.textContent = b.text; }
      bd.appendChild(div);
    } else if (mode === 'preview') {
      const fr = el('iframe', 'cv-frame');
      fr.setAttribute('sandbox', 'allow-scripts allow-forms allow-modals');
      fr.srcdoc = b.kind === 'svg'
        ? `<!doctype html><body style="margin:0;display:grid;place-items:center;min-height:100vh">${b.text}</body>`
        : b.text;
      bd.appendChild(fr);
    } else {
      const pre = el('pre', 'cv-code');
      const code = el('code', b.lang ? 'language-' + b.lang : '', b.text);
      pre.appendChild(code);
      try { if (window.hljs) hljs.highlightElement(code); } catch (_) {}
      bd.appendChild(pre);
    }
  }

  function open(id) {
    const b = blocks.get(id);
    if (!b) return;
    if (!panel) build();
    if (openId !== id) mode = previewable(b) ? 'preview' : 'code';
    openId = id;
    panel.hidden = false;
    document.body.classList.add('cv-on');
    document.querySelectorAll('.cv-card').forEach(c => c.classList.toggle('active', c.dataset.cvId === id));
    render();
  }

  function close() {
    openId = null;
    if (!panel) return;
    panel.hidden = true;
    panel.classList.remove('cv-full');
    document.body.classList.remove('cv-on');
    document.querySelectorAll('.cv-card.active').forEach(c => c.classList.remove('active'));
  }

  function flash(button, text) {
    const old = button.textContent;
    button.textContent = text;
    setTimeout(() => { button.textContent = old; }, 1200);
  }

  // http 非本机地址下 navigator.clipboard 不可用, 退回 execCommand
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    const ta = el('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } finally { ta.remove(); }
    return Promise.resolve();
  }

  function download(b) {
    const a = el('a');
    a.href = URL.createObjectURL(new Blob([b.text], { type: 'text/plain;charset=utf-8' }));
    a.download = C.fileName(b.lang, b.text);
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  function onAction(e) {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const b = blocks.get(openId);
    const act = t.dataset.act;
    if (act === 'close') return close();
    if (act === 'full') return panel.classList.toggle('cv-full');
    if (!b) return;
    if (act.startsWith('mode:')) { mode = act.slice(5); return render(); }
    if (act === 'edit') { mode = mode === 'edit' ? (previewable(b) ? 'preview' : 'code') : 'edit'; return render(); }
    if (act === 'copy') return copyText(b.text).then(() => flash(t, '✓ 已复制'), () => flash(t, '复制失败'));
    if (act === 'download') return download(b);
  }

  function startResize(e) {
    e.preventDefault();
    const appRight = document.getElementById('app').getBoundingClientRect().right;
    panel.classList.add('cv-resizing');
    const move = ev => {
      const w = Math.max(320, Math.min(appRight - ev.clientX, window.innerWidth - 360));
      panel.style.width = w + 'px';
    };
    const up = () => {
      panel.classList.remove('cv-resizing');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      store.set(WIDTH_KEY, String(parseInt(panel.style.width, 10) || ''));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  // ── 盯住 #msgs: 渲染完成后收卡片; 一次生成刚结束时自动打开最新那块 ──
  let timer = null;
  function onMutate() {
    if (window.streaming) sawStreaming = true;
    clearTimeout(timer);
    timer = setTimeout(() => {
      scan();
      if (sawStreaming && !window.streaming) {
        sawStreaming = false;
        const id = newestInLastReply();
        if (id) open(id);
      }
    }, 250);
  }

  function start() {
    const msgs = document.getElementById('msgs');
    if (!msgs) return;
    new MutationObserver(onMutate).observe(msgs, { childList: true, subtree: true });
    scan();
  }

  window.LCCanvas = { open, close, scan, blocks };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
