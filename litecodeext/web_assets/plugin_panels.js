// 仓外插件前端面板挂载点 (契约见 plugins/_contract.md "可选: 前端资源")。
// 插件 WEB_ENTRY 里的 JS (defer 注入) 调用:
//   window.LiteCodePlugins.registerPanel({id, title, icon?, render(container), onOpen?(container)})
// → 侧栏 "接入" 分组加一个按钮, 点开复用 .pnl/.pb_/.ph_/.px_ 面板 + openP/closeP 体系。
// render 只在首次打开时调用一次; onOpen 每次打开都调用 (可选, 用于刷新数据)。
// 本文件须在 sidebar_groups.js 之后引用: 分组建好后再把按钮放进 "接入"。
(function () {
  if (window.LiteCodePlugins) return;
  const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$/;
  const GROUP = 'connect';
  const panels = new Map();   // id -> {spec, rendered, btn, pnl}
  let ready = false;
  const pending = [];

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }

  function key(id) { return 'plugin-' + id; }

  function close(id) {
    if (typeof window.closeP === 'function') window.closeP(key(id));
    else { const p = document.getElementById('pnl-' + key(id)); if (p) p.classList.remove('open'); }
  }

  function open(id) {
    const rec = panels.get(id);
    if (!rec) return false;
    const body = rec.pnl.querySelector('.pbd_');
    if (!rec.rendered) {
      rec.rendered = true;
      try { rec.spec.render(body); }
      catch (e) {
        console.error('[plugin panel] render 失败', id, e);
        body.textContent = '插件面板渲染失败: ' + (e && e.message || e);
      }
    }
    if (typeof rec.spec.onOpen === 'function') {
      try { rec.spec.onOpen(body); } catch (e) { console.error('[plugin panel] onOpen 失败', id, e); }
    }
    if (typeof window.openP === 'function') window.openP(key(id));
    else rec.pnl.classList.add('open');
    return true;
  }

  function buildPanel(spec) {
    const pnl = el('div', 'pnl');
    pnl.id = 'pnl-' + key(spec.id);
    pnl.dataset.plugin = spec.id;
    pnl.addEventListener('click', ev => { if (ev.target === pnl) close(spec.id); });
    const box = el('div', 'pb_');
    const head = el('div', 'ph_');
    head.appendChild(el('span', 'pt_', (spec.icon ? spec.icon + ' ' : '') + spec.title));
    const x = el('button', 'px_', '×');
    x.type = 'button';
    x.title = '关闭';
    x.addEventListener('click', () => close(spec.id));
    head.appendChild(x);
    box.append(head, el('div', 'pbd_'));
    pnl.appendChild(box);
    document.body.appendChild(pnl);
    return pnl;
  }

  function buildButton(spec) {
    const b = el('button', 'sbt');
    b.type = 'button';
    b.dataset.panel = key(spec.id);
    b.title = spec.title;
    if (spec.icon) b.appendChild(el('span', 'sbt-ic', spec.icon));
    b.appendChild(el('span', null, spec.title));
    b.addEventListener('click', () => open(spec.id));
    return b;
  }

  function mountButton(btn) {
    const host = document.getElementById('sbf');
    if (!host) return;
    const grp = host.querySelector('.sbg[data-group="' + GROUP + '"]');
    if (grp) {
      grp.querySelector('.sbg-b').appendChild(btn);
      const n = grp.querySelector('.sbg-n');
      if (n) n.textContent = String(grp.querySelectorAll('.sbg-b > .sbt').length);
      return;
    }
    // 没有分组 (sidebar_groups.js 被移除): 平铺到 #sbf, 状态卡之前
    const anchor = document.getElementById('sbf-status');
    host.insertBefore(btn, anchor && anchor.parentNode === host ? anchor : null);
  }

  function mount(spec) {
    const rec = panels.get(spec.id);
    rec.pnl = buildPanel(spec);
    rec.btn = buildButton(spec);
    mountButton(rec.btn);
  }

  function registerPanel(spec) {
    if (!spec || typeof spec !== 'object') { console.warn('[plugin panel] spec 必须是对象'); return false; }
    const id = String(spec.id || '');
    if (!ID_RE.test(id)) { console.warn('[plugin panel] 非法 id', spec.id); return false; }
    if (panels.has(id)) { console.warn('[plugin panel] id 重复', id); return false; }
    if (typeof spec.render !== 'function') { console.warn('[plugin panel] 缺 render(container)', id); return false; }
    const clean = {
      id,
      title: String(spec.title || id).slice(0, 40),
      icon: spec.icon ? String(spec.icon).slice(0, 4) : '',
      render: spec.render,
      onOpen: spec.onOpen,
    };
    panels.set(id, { spec: clean, rendered: false, btn: null, pnl: null });
    if (ready) mount(clean); else pending.push(clean);
    return true;
  }

  window.LiteCodePlugins = {
    registerPanel,
    open,
    close,
    list: () => Array.from(panels.keys()),
  };

  function flush() {
    ready = true;
    pending.splice(0).forEach(mount);
  }
  // 插件脚本是 defer: 在 DOMContentLoaded 之前执行, 此时 sidebar_groups 还没建组 → 先排队;
  // 本监听注册在 sidebar_groups 之后, 所以 flush 时分组已就位。
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', flush);
  else flush();
})();
