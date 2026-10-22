/* usage_charts.js — 用量看板的手写 SVG 图表件 (不引图表库), 挂在 window.UDCharts。
 * 规范照 dataviz: 细柱 (<=24px, 顶端 4px 圆角、基线方角)、段间 2px 表面色缝、1px 实线网格、
 * 文字只用文字色 (--tx/--tx2/--tx3), 系列色只上图形; 悬停/聚焦出提示, 提示里数值在前。
 * 颜色变量见 usage_dashboard.css (--ud-s1..s4 / --ud-seq), 明暗主题各一套。 */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const full = n => (Number(n) || 0).toLocaleString('zh-CN');
  function fmt(n) {
    n = Number(n) || 0;
    const a = Math.abs(n);
    if (a >= 1e8) return +(n / 1e8).toFixed(a >= 1e9 ? 1 : 2) + ' 亿';
    if (a >= 1e4) return +(n / 1e4).toFixed(a >= 1e6 ? 0 : 1) + ' 万';
    return full(Math.round(n));
  }
  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v))), m = v / p;
    return ([1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(k => m <= k)) * p;
  }
  /* 顶端两角圆、底端方的柱段 */
  function topRounded(x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h));
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  /* ── 提示框: 每个图表容器一个 .ud-tip ── */
  function tip(box) {
    let t = box.querySelector(':scope > .ud-tip');
    if (!t) {
      t = document.createElement('div');
      t.className = 'ud-tip';
      t.setAttribute('role', 'status');
      box.appendChild(t);
    }
    return t;
  }
  function showTip(box, html, clientX, clientY) {
    const t = tip(box);
    t.innerHTML = html;
    t.style.display = 'block';
    const r = box.getBoundingClientRect();
    let x = clientX - r.left + 14;
    if (x + t.offsetWidth > r.width) x = clientX - r.left - t.offsetWidth - 14;
    let y = clientY - r.top - t.offsetHeight - 10;
    if (y < 0) y = clientY - r.top + 16;
    t.style.left = Math.max(0, x) + 'px';
    t.style.top = Math.max(0, y) + 'px';
  }
  function hideTip(box) { const t = box.querySelector(':scope > .ud-tip'); if (t) t.style.display = 'none'; }
  function tipRows(rows) {   /* rows: [{name, value, color}] — 数值在前加粗, 名称在后; 线形色键 */
    return rows.map(r => `<div class="r"><b>${esc(r.value)}</b><span>${r.color ? `<i class="ud-key" style="background:var(${r.color})"></i>` : ''}${esc(r.name)}</span></div>`).join('');
  }
  function bindHover(box, svg, describe) {
    const at = (el, x, y) => { const h = describe(el); if (h) showTip(box, h, x, y); };
    svg.addEventListener('pointermove', e => {
      const el = e.target.closest('[data-tip]');
      svg.querySelectorAll('.is-hot').forEach(n => n !== el && n.classList.remove('is-hot'));
      if (!el) { hideTip(box); svg.classList.remove('has-hot'); return; }
      el.classList.add('is-hot');
      svg.classList.add('has-hot');
      at(el, e.clientX, e.clientY);
    });
    svg.addEventListener('pointerleave', () => {
      hideTip(box);
      svg.classList.remove('has-hot');
      svg.querySelectorAll('.is-hot').forEach(n => n.classList.remove('is-hot'));
    });
    svg.addEventListener('focusin', e => {
      const el = e.target.closest('[data-tip]');
      if (!el) return;
      const r = el.getBoundingClientRect();
      at(el, r.left + r.width / 2, r.top);
    });
    svg.addEventListener('focusout', () => hideTip(box));
  }

  /* ── 堆叠柱: buckets [{label, title, v:{key:val}}], series [{key, name, color}] ── */
  function stackedColumns(box, buckets, series, opt) {
    opt = opt || {};
    const W = Math.max(280, box.clientWidth || 600), H = opt.height || 240;
    const L = 52, R = 8, T = 10, B = 26, iw = W - L - R, ih = H - T - B;
    const tot = b => series.reduce((a, s) => a + (b.v[s.key] || 0), 0);
    const max = niceMax(Math.max(0, ...buckets.map(tot)));
    const lead = max / Math.pow(10, Math.floor(Math.log10(max)));   /* 1.5/3/6 分 3 格, 2.5 分 5 格, 其余 4 格: 刻度都是整数 */
    const nt = [1.5, 3, 6].includes(lead) ? 3 : lead === 2.5 ? 5 : 4;
    const slot = iw / Math.max(1, buckets.length), bw = Math.max(2, Math.min(24, slot * 0.64));
    const y = v => T + ih - v / max * ih;
    let g = '';
    for (let i = 0; i <= nt; i++) {
      const v = max * i / nt, yy = Math.round(y(v)) + 0.5;
      g += `<line class="ud-grid${i ? '' : ' base'}" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/>` +
        `<text class="ud-ax" x="${L - 8}" y="${yy + 3.5}" text-anchor="end">${fmt(v)}</text>`;
    }
    const every = Math.ceil(buckets.length / Math.max(1, Math.floor(iw / 46)));
    buckets.forEach((b, i) => {
      const cx = L + slot * i + slot / 2, x = cx - bw / 2;
      let base = T + ih, segs = '';
      const live = series.filter(s => (b.v[s.key] || 0) > 0);
      live.forEach((s, j) => {
        const h = (b.v[s.key] || 0) / max * ih;
        const gap = j > 0 ? 2 : 0, top = j === live.length - 1;
        const hh = Math.max(0.8, h - gap);
        segs += top ? `<path d="${topRounded(x, base - h, bw, hh, 4)}" style="fill:var(${s.color})"/>`
          : `<rect x="${x}" y="${base - h}" width="${bw}" height="${hh}" style="fill:var(${s.color})"/>`;
        base -= h;
      });
      g += `<g class="ud-col" data-tip="${i}" tabindex="0" aria-label="${esc(b.title)}: ${full(tot(b))} token">` +
        `<rect class="ud-hit" x="${L + slot * i}" y="${T}" width="${slot}" height="${ih}"/>${segs}</g>`;
      if (i % every === 0 || buckets.length <= 8) {
        g += `<text class="ud-ax" x="${cx}" y="${H - 8}" text-anchor="middle">${esc(b.label)}</text>`;
      }
    });
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="${esc(opt.label || '')}">${g}</svg>`;
    const svg = box.querySelector('svg');
    bindHover(box, svg, el => {
      const b = buckets[+el.dataset.tip];
      return `<div class="h">${esc(b.title)}</div>` +
        tipRows(series.slice().reverse().map(s => ({name: s.name, value: full(b.v[s.key] || 0), color: s.color}))) +
        `<div class="r sum"><b>${full(tot(b))}</b><span>合计${b.calls != null ? ` · ${full(b.calls)} 次调用` : ''}</span></div>`;
    });
  }

  /* ── 迷你折线 (KPI 卡): 淡色线 + 末点强调色, 10% 面积 ── */
  function sparkline(values, opt) {
    opt = opt || {};
    const w = opt.w || 120, h = opt.h || 32, p = 4;
    const v = (values || []).map(x => Number(x) || 0);
    if (v.length < 2 || !v.some(x => x > 0)) return `<svg class="ud-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><line x1="${p}" x2="${w - p}" y1="${h - p}" y2="${h - p}" class="ud-spark-flat"/></svg>`;
    const max = Math.max(...v), step = (w - 2 * p) / (v.length - 1);
    const pts = v.map((x, i) => [p + i * step, h - p - (max ? x / max : 0) * (h - 2 * p)]);
    const d = pts.map((q, i) => (i ? 'L' : 'M') + q[0].toFixed(1) + ',' + q[1].toFixed(1)).join('');
    const last = pts[pts.length - 1];
    return `<svg class="ud-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">` +
      `<path class="ud-spark-area" d="${d}L${last[0]},${h - p}L${p},${h - p}Z"/><path class="ud-spark-line" d="${d}"/>` +
      `<circle class="ud-spark-dot" cx="${last[0]}" cy="${last[1]}" r="3.5"/></svg>`;
  }

  /* ── 横向条形 (单系列, 无图例): rows [{id, name, sub, value, valueText, title}] ── */
  function hbars(rows, opt) {
    opt = opt || {};
    if (!rows.length) return `<div class="ud-muted">${esc(opt.empty || '暂无数据')}</div>`;
    const max = Math.max(...rows.map(r => r.value || 0)) || 1;
    return `<div class="ud-hbars${opt.clickable ? ' clk' : ''}">${rows.map(r => `
      <div class="ud-hbar"${r.id != null ? ` data-id="${esc(r.id)}"` : ''}${opt.clickable ? ' tabindex="0" role="button"' : ''} title="${esc(r.title || '')}">
        <div class="ud-hbar-top"><span class="nm">${opt.clickable ? '<span class="ud-caret" aria-hidden="true">›</span>' : ''}${esc(r.name)}</span><span class="val">${esc(r.valueText)}</span></div>
        <div class="ud-hbar-trk"><span style="width:${Math.max(0.8, (r.value || 0) / max * 100).toFixed(2)}%"></span></div>
        ${r.sub ? `<div class="ud-hbar-sub">${esc(r.sub)}</div>` : ''}
      </div>`).join('')}</div>`;
  }

  /* ── 星期 × 小时热力: cells [{dow(0=周日), hour, calls, total}] ── */
  const DOW = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  function heatmap(box, cells) {
    const grid = Array.from({length: 7}, () => Array(24).fill(null));
    cells.forEach(c => { grid[(c.dow + 6) % 7][c.hour] = c; });
    const max = Math.max(1, ...cells.map(c => c.calls || 0));
    const W = Math.max(300, box.clientWidth || 600), L = 34, T = 4, B = 20;
    const cw = (W - L) / 24, ch = Math.min(26, Math.max(14, cw * 0.9)), H = T + ch * 7 + B;
    let g = '';
    grid.forEach((row, r) => {
      g += `<text class="ud-ax" x="${L - 8}" y="${T + r * ch + ch / 2 + 3.5}" text-anchor="end">${DOW[r]}</text>`;
      row.forEach((c, h) => {
        const x = L + h * cw + 1, yy = T + r * ch + 1, n = c ? c.calls : 0;
        const op = n ? (0.18 + 0.82 * Math.pow(n / max, 0.6)).toFixed(3) : 0;
        g += `<rect class="ud-cell${n ? '' : ' zero'}" data-tip="${r}:${h}" x="${x.toFixed(1)}" y="${yy.toFixed(1)}" width="${(cw - 2).toFixed(1)}" height="${(ch - 2).toFixed(1)}" rx="3"` +
          (n ? ` style="fill-opacity:${op}"` : '') + '/>';
      });
    });
    [0, 6, 12, 18, 23].forEach(h => {
      const end = h === 23, x = end ? W - 2 : L + h * cw + cw / 2;
      g += `<text class="ud-ax" x="${x.toFixed(1)}" y="${H - 5}" text-anchor="${end ? 'end' : 'middle'}">${h}时</text>`;
    });
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="按星期和小时的调用次数热力图">${g}</svg>` +
      `<div class="ud-scale"><span>少</span>${[0.18, 0.4, 0.6, 0.8, 1].map(o => `<i style="opacity:${o}"></i>`).join('')}<span>多</span><span class="mx">最多 ${full(max)} 次/格</span></div>`;
    const svg = box.querySelector('svg');
    bindHover(box, svg, el => {
      const [r, h] = el.dataset.tip.split(':').map(Number), c = grid[r][h];
      return `<div class="h">${DOW[r]} ${String(h).padStart(2, '0')}:00–${String(h).padStart(2, '0')}:59</div>` +
        tipRows([{name: '次调用', value: full(c ? c.calls : 0)}, {name: 'token', value: full(c ? c.total : 0)}]);
    });
  }

  window.UDCharts = {esc, full, fmt, niceMax, stackedColumns, sparkline, hbars, heatmap, showTip, hideTip};
})();
