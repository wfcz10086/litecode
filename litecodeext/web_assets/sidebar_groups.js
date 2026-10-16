// 侧栏分组: 把 #sbf 里平铺的 .sbt 面板按钮收进可折叠分组。
// 调整分组只改 GROUPS; 未登记的按钮落入最后一组; 删掉本文件的 <script> 即恢复平铺。
(function () {
  const GROUPS = [
    { id: 'daily',   i18n: 'nav.group.daily',   label: '日常', open: true,
      keys: ['memory', 'timer', 'artifacts', 'sfiles', 'project', 'dag'] },
    { id: 'connect', i18n: 'nav.group.connect', label: '接入', open: false,
      keys: ['wechat', 'wecom', 'windows_nodes'] },
    { id: 'ops',     i18n: 'nav.group.ops',     label: '运维', open: false,
      keys: ['stats', 'bg', 'docker', 'vnc'] },
  ];
  const PINNED = ['settings'];
  const STORE = 'sb_groups_open';

  const CSS = `
    .sbg{margin-bottom:2px}
    .sbg-h{width:100%;display:flex;align-items:center;gap:6px;padding:6px 9px 4px;background:none;border:none;
      color:var(--tx3);font:600 10.5px var(--sans);letter-spacing:.06em;cursor:pointer;text-transform:uppercase}
    .sbg-h:hover{color:var(--tx2)}
    .sbg-caret{margin-left:auto;transition:transform .15s;font-size:9px}
    .sbg:not(.open) .sbg-caret{transform:rotate(-90deg)}
    .sbg:not(.open) .sbg-b{display:none}
    .sbg-n{font-weight:500;opacity:.7}
    .sbg.open .sbg-n{display:none}`;

  function keyOf(btn) {
    if (btn.dataset.panel) return btn.dataset.panel;
    const oc = btn.getAttribute('onclick') || '';
    const m = oc.match(/openP\(\s*['"]([\w-]+)['"]/);
    if (m) return m[1];
    if (/openDAG/.test(oc)) return 'dag';
    return '';
  }

  function loadOpen() {
    try { return JSON.parse(localStorage.getItem(STORE) || 'null') || {}; } catch (e) { return {}; }
  }
  function saveOpen(state) {
    try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) { /* 隐私模式等: 不记忆即可 */ }
  }

  function build() {
    const host = document.getElementById('sbf');
    if (!host || host.querySelector('.sbg')) return;
    const anchor = document.getElementById('sbf-status');
    const buttons = Array.from(host.querySelectorAll(':scope > .sbt'));
    if (!buttons.length) return;

    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    const byKey = new Map(buttons.map(b => [keyOf(b), b]));
    const claimed = new Set([...GROUPS.flatMap(g => g.keys), ...PINNED]);
    const leftovers = buttons.filter(b => !claimed.has(keyOf(b)));
    const state = loadOpen();

    GROUPS.forEach((g, i) => {
      const members = g.keys.map(k => byKey.get(k)).filter(Boolean);
      if (i === GROUPS.length - 1) members.push(...leftovers);
      if (!members.length) return;

      const wrap = document.createElement('div');
      wrap.className = 'sbg' + ((g.id in state ? state[g.id] : g.open) ? ' open' : '');
      wrap.dataset.group = g.id;

      const head = document.createElement('button');
      head.className = 'sbg-h';
      head.type = 'button';
      head.innerHTML = `<span data-i18n="${g.i18n}">${g.label}</span>` +
        `<span class="sbg-n">${members.length}</span><span class="sbg-caret">▾</span>`;
      head.addEventListener('click', () => {
        const open = wrap.classList.toggle('open');
        head.setAttribute('aria-expanded', String(open));
        const s = loadOpen(); s[g.id] = open; saveOpen(s);
      });
      head.setAttribute('aria-expanded', String(wrap.classList.contains('open')));

      const body = document.createElement('div');
      body.className = 'sbg-b';
      members.forEach(b => body.appendChild(b));

      wrap.append(head, body);
      host.insertBefore(wrap, anchor && anchor.parentNode === host ? anchor : null);
    });

    PINNED.forEach(k => {
      const b = byKey.get(k);
      if (b) host.insertBefore(b, anchor && anchor.parentNode === host ? anchor : null);
    });

    if (window.I18N && I18N.applyDOM) I18N.applyDOM(host);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
