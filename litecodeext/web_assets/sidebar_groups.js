// 侧栏功能入口 → 底部「工具箱」(ui-v2 2026-10-21; 原为 日常/接入/运维 三个平铺分组, 占掉一半侧栏高度)。
// 底部一行: [🧰 工具箱 ▾] [⚙ 设置]; 工具箱展开是按 日常/接入/运维 分段的图标网格 (每格带文字 + tooltip),
// 收起时只占一行, 把高度让给会话列表。展开状态记在 localStorage。
// 调整分组只改 GROUPS; 未登记的按钮落入最后一组; 删掉本文件的 <script> 即恢复平铺。
(function () {
  const GROUPS = [
    { id: 'daily',   i18n: 'nav.group.daily',   label: '日常', keys: ['memory', 'kb', 'timer', 'artifacts', 'sfiles', 'project', 'dag'] },
    { id: 'connect', i18n: 'nav.group.connect', label: '接入', keys: ['wechat', 'wecom', 'windows_nodes'] },
    { id: 'ops',     i18n: 'nav.group.ops',     label: '运维', keys: ['stats', 'bg', 'docker', 'vnc'] },
  ];
  const PINNED = ['skills', 'settings'];   // skills: 技能面板常驻, 草稿红点要看得见 (skills_panel.js)
  const STORE = 'sb_toolbox_open';
  // 没自带图标的入口补一个 (与其它入口同风格的线条 svg)
  const FALLBACK_ICON = {
    windows_nodes: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4M12 8v5M9.5 10.5h5"/></svg>',
  };

  function keyOf(btn) {
    if (btn.dataset.panel) return btn.dataset.panel;
    const oc = btn.getAttribute('onclick') || '';
    const m = oc.match(/openP\(\s*['"]([\w-]+)['"]/);
    if (m) return m[1];
    if (/openDAG/.test(oc)) return 'dag';
    return '';
  }
  function loadOpen() { try { return localStorage.getItem(STORE) === '1'; } catch (e) { return false; } }
  function saveOpen(v) { try { localStorage.setItem(STORE, v ? '1' : '0'); } catch (e) { /* 隐私模式: 不记忆 */ } }

  function build() {
    const host = document.getElementById('sbf');
    if (!host || host.querySelector('.sbx')) return;
    const anchor = document.getElementById('sbf-status');
    const buttons = Array.from(host.querySelectorAll(':scope > .sbt'));
    if (!buttons.length) return;

    const byKey = new Map(buttons.map(b => [keyOf(b), b]));
    const claimed = new Set([...GROUPS.flatMap(g => g.keys), ...PINNED]);
    const leftovers = buttons.filter(b => !claimed.has(keyOf(b)));

    const box = document.createElement('div');
    box.className = 'sbx' + (loadOpen() ? ' open' : '');
    const grid = document.createElement('div');
    grid.className = 'sbx-grid';
    grid.id = 'sbx-grid';
    GROUPS.forEach((g, i) => {
      const members = g.keys.map(k => byKey.get(k)).filter(Boolean);
      if (i === GROUPS.length - 1) members.push(...leftovers);
      if (!members.length) return;
      const h = document.createElement('div');
      h.className = 'sbx-sec';
      h.innerHTML = `<span data-i18n="${g.i18n}">${g.label}</span>`;
      grid.appendChild(h);
      members.forEach(b => {
        const k = keyOf(b);
        if (!b.querySelector('svg') && FALLBACK_ICON[k]) b.insertAdjacentHTML('afterbegin', FALLBACK_ICON[k]);
        const lbl = (b.querySelector('span')?.textContent || b.textContent || '').trim();
        if (!b.title) b.title = lbl;
        b.classList.add('sbx-it');
        grid.appendChild(b);
      });
    });

    const row = document.createElement('div');
    row.className = 'sbx-row';
    const tog = document.createElement('button');
    tog.type = 'button';
    tog.className = 'sbx-tog';
    tog.setAttribute('aria-controls', 'sbx-grid');
    tog.innerHTML = '<span>🧰</span><span class="sbx-tl">工具箱</span><span class="sbx-caret">▾</span>';
    tog.title = '记忆 / 定时任务 / 产出物 / 工作区 / 项目 / DAG / 接入 / 运维';
    tog.setAttribute('aria-expanded', String(box.classList.contains('open')));
    tog.addEventListener('click', () => {
      const open = box.classList.toggle('open');
      tog.setAttribute('aria-expanded', String(open));
      saveOpen(open);
    });
    row.appendChild(tog);
    PINNED.forEach(k => {
      const b = byKey.get(k);
      if (b) { b.classList.add('sbx-pin'); b.title = b.title || (b.textContent || '').trim(); row.appendChild(b); }
    });

    box.append(grid, row);
    host.insertBefore(box, anchor && anchor.parentNode === host ? anchor : null);
    // 面板打开后在手机上收起侧栏 (openP 本身会 closeSB); 工具箱点完自动收起, 下次进来不占地方
    grid.addEventListener('click', e => { if (e.target.closest('.sbt') && window.innerWidth < 768) { box.classList.remove('open'); saveOpen(false); } });
    if (window.I18N && I18N.applyDOM) I18N.applyDOM(host);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
