/* ui_polish.js — [ui-audit 2026-10-21] 顶栏 / 输入框布局 + 浮层交互 (配套样式在 ui_v2.css)
 *
 * 顶栏: 只留 会话标题 (单击改名) / 模型 / 🔗 分享 / ⋯ 更多。
 *   导出、录屏三件套 (⏺ 自动 / 🎥 手动 / 🎞 回放)、桌面、主题、语言、版本号 收进「⋯」卡片菜单。
 *   这些原按钮有的由别的模块动态插入 (share_btn.js / screen_record.js), 不搬动它们:
 *   CSS 把原按钮藏起来, 菜单每次打开时现查现建, 点菜单项 = 代点原按钮 (状态文字如「录屏中」跟原按钮走)。
 * 输入框: 📎 / 模型 / 🧠 / 🎭 和 停止 / ⚡立即 / 发送 都收进同一个圆角框底部工具条 (把原节点搬进去, 事件不变)。
 * 浮层统一「点外面 / Esc 关闭」; 导出改 <a download> 直接下载; 代码块没标语言不再让 highlight.js 乱猜。
 */
(function () {
  const $ = id => document.getElementById(id);
  const POPOVERS = ['hdr-more-card', 'lcRecList', 'lcShareCard', 'theme-menu'];
  const OPENERS = {'lcRecList': 'recList', 'lcShareCard': 'share-btn', 'theme-menu': 'theme-btn', 'hdr-more-card': 'hdr-more'};

  // ── 「⋯ 更多」菜单 ──────────────────────────────────────────
  function menuItems() {
    const rec = LCRecState();
    return [
      {id: 'exp-btn', label: '⬇ 导出 Markdown'},
      {sep: '录屏'},
      {id: 'recAuto', label: rec.auto ? '⏺ 自动录屏: 开 (点击关闭)' : '⏺ 自动录屏: 关', on: rec.auto},
      {id: 'recMan', label: rec.man ? `⏹ 停止手动录屏 ${rec.manText}` : '🎥 手动录屏', on: rec.man},
      {id: 'recList', label: '🎞 回放'},
      {sep: ''},
      {id: 'hdr-vnc', label: '🖥️ 桌面'},
      {id: 'theme-btn', label: '🎨 主题'},
      {id: 'hdr-i18n', label: (window.I18N && I18N.locale === 'en') ? '🌐 切换到中文' : '🌐 English'},
    ];
  }
  function LCRecState() {
    const a = $('recAuto'), m = $('recMan');
    const manText = (m && m.textContent || '').replace('⏹', '').trim();
    return {auto: !!(window.LCRec && LCRec.on), man: !!(m && /⏹/.test(m.textContent || '')), manText};
  }

  function closeMoreCard() { $('hdr-more-card')?.remove(); }

  function openMoreCard(ev) {
    ev && ev.stopPropagation();
    if ($('hdr-more-card')) { closeMoreCard(); return; }
    const card = document.createElement('div');
    card.id = 'hdr-more-card';
    card.setAttribute('role', 'menu');
    menuItems().forEach(it => {
      if (it.sep !== undefined) {
        const s = document.createElement('div');
        s.className = it.sep ? 'hdr-more-sec' : 'hdr-more-sep';
        s.textContent = it.sep;
        card.appendChild(s);
        return;
      }
      const src = $(it.id);
      if (!src) return;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'hdr-more-it' + (it.on ? ' on' : '');
      b.setAttribute('role', 'menuitem');
      b.dataset.for = it.id;
      b.textContent = it.label;
      if (src.title) b.title = src.title;
      b.onclick = e => { e.stopPropagation(); closeMoreCard(); src.click(); };
      card.appendChild(b);
    });
    const ver = $('verpill');
    if (ver && ver.textContent.trim()) {
      const v = document.createElement('div');
      v.className = 'hdr-more-ver';
      v.textContent = 'LiteCode ' + ver.textContent.trim();
      card.appendChild(v);
    }
    document.body.appendChild(card);
  }

  // ── 顶栏: 标题单击改名 / 模型按钮 / 更多 ─────────────────────────
  function mountHeader() {
    const hdr = $('hdr');
    if (!hdr || $('hdr-more')) return;
    const ct = $('ct');
    if (ct) {
      ct.removeAttribute('data-i18n');   // 否则 I18N 初始化/切语言时把会话标题覆盖成「新建会话」
      ct.title = '点击重命名';
      ct.addEventListener('click', () => { if (typeof renameCurSession === 'function') renameCurSession(); });
    }
    // 模型只有一个入口: 输入框里的模型 chip (本会话模型, 卡片里可「设为默认」); 顶栏不再放第二个选择器
    const share = $('share-btn');
    if (share) {
      share.innerHTML = '<span class="i">🔗</span><span class="t">分享</span>';
      share.style.marginLeft = 'auto';
    }
    const more = document.createElement('button');
    more.id = 'hdr-more';
    more.type = 'button';
    more.title = '更多: 导出 / 录屏 / 回放 / 桌面 / 主题 / 语言';
    more.setAttribute('aria-label', '更多操作');
    more.setAttribute('aria-haspopup', 'menu');
    more.textContent = '⋯';
    more.onclick = openMoreCard;
    hdr.appendChild(more);
  }

  // ── 输入框: 工具条 + 发送收进同一个圆角框 ─────────────────────────
  function mountComposer() {
    const iw = $('iw'), irow = $('irow');
    if (!iw || $('iw-bar')) return;
    const bar = document.createElement('div');
    bar.id = 'iw-bar';
    const tools = document.createElement('div');
    tools.className = 'iw-tools';
    const acts = document.createElement('div');
    acts.className = 'iw-acts';
    ['upb', 'msw-chip', 'thk', 'strat'].forEach(id => { const n = $(id); if (n) tools.appendChild(n); });
    const ib = $('ib');
    if (ib) { ib.removeAttribute('data-i18n'); ib.textContent = ''; ib.title = '停止生成'; ib.setAttribute('aria-label', '停止生成'); acts.appendChild(ib); }
    ['stb', 'sdb'].forEach(id => { const n = $(id); if (n) acts.appendChild(n); });
    const sdb = $('sdb');
    if (sdb) { sdb.title = sdb.title || '发送 (Enter)'; sdb.setAttribute('aria-label', '发送'); }
    bar.append(tools, acts);
    iw.appendChild(bar);
    // 「↓ 回到底部」按钮跟着输入区高度走, 不再压在发送按钮上
    const ia = $('ia');
    if (ia && window.ResizeObserver) new ResizeObserver(() => {
      document.documentElement.style.setProperty('--ia-h', ia.getBoundingClientRect().height + 'px');
    }).observe(ia);
    // 输入框里点空白处也聚焦到文本框 (圆角框整体像一个输入控件)
    iw.addEventListener('mousedown', e => {
      if (e.target === iw || e.target === bar || e.target === tools) { e.preventDefault(); $('mi')?.focus(); }
    });
  }

  // ── 浮层: 点外面 / Esc 关闭 ───────────────────────────────────
  document.addEventListener('mousedown', e => {
    POPOVERS.forEach(id => {
      const el = $(id);
      if (!el || el.contains(e.target)) return;
      const opener = $(OPENERS[id]);
      if (opener && opener.contains(e.target)) return;
      if (e.target.closest && e.target.closest('#hdr-more-card')) return;   // 菜单项会再打开对应浮层
      el.remove();
    });
  }, true);
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    POPOVERS.forEach(id => $(id)?.remove());
  });

  // ── 导出: 直接下载, 不开空白标签页 ──────────────────────────────
  window.exportSession = function () {
    if (!window.cur) { toast('请先选择会话'); return; }
    const a = document.createElement('a');
    a.href = `/api/sessions/${encodeURIComponent(window.cur)}/export?fmt=md`;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast('📥 已开始下载 Markdown');
  };

  // ── 代码块没写语言: 不让 highlight.js 自动猜 (纯文本常被猜成 MAKEFILE), 按纯文本显示 ──
  if (window.hljs && !window.hljs.__lcNoGuess) {
    const orig = window.hljs.highlightElement.bind(window.hljs);
    window.hljs.highlightElement = function (el) {
      if (el && el.classList && !/\blanguage-\w/.test(el.className) && !/\bno-?highlight\b/.test(el.className)) el.classList.add('nohighlight');
      return orig(el);
    };
    window.hljs.__lcNoGuess = true;
  }

  // ── 侧栏: 顶部醒目的「＋ 新对话」, 排序下拉收起 (列表按时间分组, 见 project_groups.js) ──
  function mountSidebar() {
    const ncb = $('ncb'), sbh = $('sbh');
    if (!ncb || !sbh || ncb.dataset.v2) return;
    ncb.dataset.v2 = '1';
    ncb.innerHTML = '<span class="i">＋</span><span>新对话</span>';
    ncb.title = '新对话';
    sbh.after(ncb);
  }

  // ── 窄屏输入框: 🧠 / 🎭 收进「＋」小菜单, 工具条不再被发送按钮压住 ──────────────
  function closeToolCard() { $('iw-more-card')?.remove(); }
  function renderToolCard(card) {
    card.textContent = '';
    ['thk', 'strat'].forEach(id => {
      const src = $(id);
      if (!src) return;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'hdr-more-it' + (src.classList.contains('on') ? ' on' : '');
      b.textContent = (src.textContent || '').trim();
      b.title = src.title || '';
      b.onclick = async e => { e.stopPropagation(); await src.onclick?.(); setTimeout(() => $('iw-more-card') && renderToolCard(card), 400); };
      card.appendChild(b);
    });
  }
  function openToolCard(ev) {
    ev && ev.stopPropagation();
    if ($('iw-more-card')) { closeToolCard(); return; }
    const card = document.createElement('div');
    card.id = 'iw-more-card';
    renderToolCard(card);
    document.body.appendChild(card);
    const r = $('iw-more').getBoundingClientRect();
    card.style.left = Math.max(8, Math.min(r.left, innerWidth - 228)) + 'px';
    card.style.bottom = (innerHeight - r.top + 8) + 'px';
  }
  function mountToolMore() {
    const tools = document.querySelector('#iw-bar .iw-tools');
    if (!tools || $('iw-more')) return;
    const b = document.createElement('button');
    b.id = 'iw-more';
    b.type = 'button';
    b.title = '更多: 思考强度 / 推演';
    b.setAttribute('aria-label', '更多输入选项');
    b.textContent = '＋';
    b.onclick = openToolCard;
    tools.appendChild(b);
  }
  POPOVERS.push('iw-more-card');
  OPENERS['iw-more-card'] = 'iw-more';

  // 切会话后刷新 🧠 (本会话思考强度)
  let _lastSid;
  setInterval(() => {
    const sid = (typeof cur !== 'undefined') ? cur : null;
    if (sid !== _lastSid) { _lastSid = sid; if (typeof _syncThinkChip === 'function') _syncThinkChip(); }
  }, 800);

  function mount() { mountHeader(); mountComposer(); mountToolMore(); mountSidebar(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
