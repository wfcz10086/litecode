/* ui_files.js — [ui-v2 2026-10-21] 一条回复里的文件只出现一次: 合进「📎 本轮产物」卡片
 *
 * 原来同一个文件会出现三处: 正文上方 diff 条 (.dv "hi.py +1 -0")、可展开预览卡 (.pv-card "▸ hi.py")、
 * 结尾「📎 本轮产物」下载栏 (.pv-download-panel)。现在每个文件一行: 图标 / 名称 / +N −M / 预览 / 差异 / ↗ / 下载。
 * 原预览卡和 diff 元素整个搬进对应行的展开区 (懒加载、图片自动展开等原逻辑不变), 下载栏删掉。
 * 做法同 ui_steps.js: 盯 #msgs 的变化, rAF 合批处理; 只处理已结束的回复 (流式中的那条结束后再收), 幂等。
 */
(function () {
  const esc_ = v => String(v == null ? '' : v).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const IMG = /\.(png|jpe?g|gif|webp|bmp|svg)$/i;
  const AUD = /\.(mp3|wav|ogg|m4a|flac)$/i;   // [tts 2026-10-22] 音频播放器很矮, 和图片一样默认展开

  function icon(nm) {
    if (IMG.test(nm)) return '🖼';
    if (/\.(mp4|webm|mov|mkv)$/i.test(nm)) return '🎬';
    if (AUD.test(nm)) return '🔊';
    if (/\.pdf$/i.test(nm)) return '📕';
    if (/\.(pptx?|odp)$/i.test(nm)) return '📊';
    if (/\.(docx?|odt)$/i.test(nm)) return '📝';
    if (/\.(xlsx?|ods|csv)$/i.test(nm)) return '📈';
    return '📄';
  }

  function liveBubble() {
    if (!window.streaming) return null;
    const all = document.querySelectorAll('#msgs .msg.ai > .mb');
    return all.length ? all[all.length - 1] : null;
  }

  function ensureBox(mb) {
    let box = mb.querySelector(':scope > .lc-files');
    if (!box) {
      box = document.createElement('div');
      box.className = 'lc-files';
      box.innerHTML = '<div class="lc-files-h">📎 本轮产物 <span class="n"></span></div><div class="lc-files-b"></div>';
      const anchor = mb.querySelector(':scope > .ub') || mb.querySelector(':scope > .mac');
      mb.insertBefore(box, anchor || null);
    }
    return box;
  }

  function rowFor(box, key, name, path) {
    let row = Array.from(box.querySelectorAll('.lc-file')).find(r => r.dataset.key === key);
    if (row) { if (path && !row.dataset.path) setPath(row, path); return row; }
    row = document.createElement('div');
    row.className = 'lc-file';
    row.dataset.key = key;
    row.innerHTML = `<div class="lc-file-r"><span class="ic">${icon(name)}</span><span class="nm" title="${esc_(path || name)}">${esc_(name)}</span>` +
      `<span class="df"></span><span class="sp"></span><span class="acts"></span></div><div class="lc-file-x"></div>`;
    box.querySelector('.lc-files-b').appendChild(row);
    if (path) setPath(row, path);
    return row;
  }

  function setPath(row, path) {
    row.dataset.path = path;
    const u = encodeURIComponent(path), nm = path.split('/').pop();
    const acts = row.querySelector('.acts');
    acts.insertAdjacentHTML('beforeend',
      `<a class="lc-fa" href="/api/media?path=${u}" target="_blank" title="新标签打开">↗</a>` +
      `<a class="lc-fa dl" href="/api/media?path=${u}" download="${esc_(nm)}" title="下载">↓ 下载</a>`);
  }

  function addToggle(row, cls, label, el, open) {
    const x = row.querySelector('.lc-file-x');
    el.classList.add('lc-in', cls);
    x.appendChild(el);
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'lc-fa lc-tg';
    b.textContent = label;
    b.onclick = () => {
      const on = !el.classList.contains('lc-show');
      el.classList.toggle('lc-show', on);
      b.classList.toggle('on', on);
      if (on && cls === 'lc-pv') {     // 触发原预览卡的懒加载
        const body = el.children[1];
        if (body && body.style.display === 'none') el.firstChild?.click();
      }
      if (on && cls === 'lc-dv') el.querySelector('.dbd')?.classList.add('open');
    };
    row.querySelector('.acts').prepend(b);
    if (open) { el.classList.add('lc-show'); b.classList.add('on'); }
  }

  function process(mb) {
    const cards = Array.from(mb.querySelectorAll('.pv-card:not(.lc-in)'));
    const diffs = Array.from(mb.querySelectorAll('.dv:not(.lc-in)'));
    const panels = mb.querySelectorAll('.pv-download-panel');
    if (!cards.length && !diffs.length && !panels.length) return;
    panels.forEach(p => {   // 下载栏里有、但没有预览卡的文件也要留一行
      p.querySelectorAll('a[download]').forEach(a => {
        const m = /path=([^&]+)/.exec(a.getAttribute('href') || '');
        if (m) { const path = decodeURIComponent(m[1]); rowFor(ensureBox(mb), path.split('/').pop(), path.split('/').pop(), path); }
      });
      p.remove();
    });
    if (!cards.length && !diffs.length) { relabel(mb); return; }
    const box = ensureBox(mb);
    cards.forEach(c => {
      const path = c.dataset.path || '';
      const nm = path.split('/').pop() || '文件';
      const row = rowFor(box, nm, nm, path);
      if (!row.querySelector('.lc-pv')) addToggle(row, 'lc-pv', '预览', c, IMG.test(nm) || AUD.test(nm));
      else c.remove();
    });
    diffs.forEach(d => {
      const nm = (d.querySelector('.dth span:nth-child(2)')?.textContent || 'file').trim();
      const add = d.querySelector('.dth span:nth-child(3)')?.textContent || '';
      const del = d.querySelector('.dth span:nth-child(4)')?.textContent || '';
      const row = rowFor(box, nm, nm, '');
      row.querySelector('.df').innerHTML = `<b class="a">${esc_(add)}</b> <b class="d">${esc_(del.replace('-', '−'))}</b>`;
      if (!row.querySelector('.lc-dv')) addToggle(row, 'lc-dv', '差异', d, false);
      else d.remove();
    });
    relabel(mb);
  }

  function relabel(mb) {
    const box = mb.querySelector(':scope > .lc-files');
    if (!box) return;
    const n = box.querySelectorAll('.lc-file').length;
    const t = `(${n})`;
    const el = box.querySelector('.lc-files-h .n');
    if (el.textContent !== t) el.textContent = t;
  }

  let queued = false;
  function pass() {
    queued = false;
    const live = liveBubble();
    document.querySelectorAll('#msgs .msg.ai > .mb').forEach(mb => { if (mb !== live) process(mb); });
  }
  function schedule() { if (!queued) { queued = true; requestAnimationFrame(pass); } }

  function start() {
    const msgs = document.getElementById('msgs');
    if (!msgs) return;
    new MutationObserver(schedule).observe(msgs, {childList: true, subtree: true});
    let was = false;
    setInterval(() => { if (was && !window.streaming) schedule(); was = !!window.streaming; }, 1000);
    schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
