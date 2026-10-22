/* usage_dashboard.js — token 用量看板 (全屏视图) + 离线报表模式。
 * 入口: 侧栏左上角「📊 看板」→ openUsageDash()。数据: /api/usage/* (routers/usage_router.py)。
 * 图表件在 usage_charts.js (window.UDCharts); 离线报表 (/api/usage/report.html) 内联同两份 js,
 * 数据放 window.__UD_REPORT__, 这里检测到就直接渲染、不发请求。 */
(function () {
  'use strict';
  const C = window.UDCharts;
  const {esc, full, fmt} = C;
  const RANGES = [['today', '今天'], ['7d', '7 天'], ['30d', '30 天'], ['all', '全部']];
  const RANGE_LONG = {today: '今天', '7d': '近 7 天', '30d': '近 30 天', all: '全部时间'};
  const PREV_NAME = {today: '昨天同时段', '7d': '前 7 天', '30d': '前 30 天'};
  const SRC = {web: 'Web 对话', wechat: '微信', wecom: '企微', cli: 'CLI', timer: '定时任务', dag: 'DAG',
    subagent: '子代理', vision: '识图', compress: '上下文压缩', memory: '记忆提取', deep_search: '深度搜索',
    probe: '模型测试', api: 'API', side: '旁路'};
  /* 堆叠顺序 = 校验过的相邻色序 (蓝/橙/青/黄), 自下而上 */
  const SERIES = [{key: 'cached', name: '缓存命中输入', color: '--ud-s1'}, {key: 'uncached', name: '未命中输入', color: '--ud-s2'},
    {key: 'output', name: '输出 (正文)', color: '--ud-s3'}, {key: 'reasoning', name: '思考', color: '--ud-s4'}];
  const REPORT = window.__UD_REPORT__ || null;
  let range = REPORT ? REPORT.range : 'today';
  if (!REPORT) { try { range = localStorage.getItem('lc.usage.range') || 'today'; } catch (e) {} }
  let last = null, seq = 0, resizeT = 0, showTable = false;

  const pct = (a, b) => !b ? '—' : (a > 0 && a / b < 0.001) ? '<0.1%' : (a / b * 100).toFixed(a / b < 0.1 && a > 0 ? 1 : 0) + '%';
  const srcName = s => SRC[s] || s || '未知';
  function ago(ts) {
    if (!ts) return '—';
    const d = Date.now() / 1000 - ts;
    if (d < 60) return '刚刚';
    if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
    if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
    if (d < 86400 * 30) return Math.floor(d / 86400) + ' 天前';
    return new Date(ts * 1000).toLocaleDateString('zh-CN');
  }

  /* ── 外壳 ── */
  function root() {
    let el = document.getElementById('ud-root');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'ud-root';
    el.className = REPORT ? 'on report' : '';
    el.setAttribute('role', REPORT ? 'main' : 'dialog');
    el.setAttribute('aria-label', '用量看板');
    const gen = REPORT ? new Date(REPORT.generated * 1000).toLocaleString('zh-CN', {hour12: false}) : '';
    el.innerHTML = `<div class="ud-wrap">
      <header class="ud-top"><div class="ud-title"><span class="ud-logo" aria-hidden="true">📊</span>${REPORT ? 'LiteCode 用量报表' : '用量看板'}
        ${REPORT ? `<span class="ud-gen">${esc(RANGE_LONG[range] || range)} · 生成于 ${esc(gen)}</span>` : ''}</div>
        ${REPORT ? '' : `<button class="ud-x" id="ud-close" title="关闭 (Esc)" aria-label="关闭">×</button>
        <div class="ud-filters"><div class="ud-chips" id="ud-ranges" role="tablist">${RANGES.map(([k, t]) => `<button class="ud-chip" role="tab" data-r="${k}">${t}</button>`).join('')}</div>
          <div class="ud-exports"><a class="ud-btn" id="ud-export" href="#" download title="逐次调用明细">⬇ CSV</a><a class="ud-btn" id="ud-report" href="#" download title="离线 HTML 报表, 断网可打开">⬇ <span class="lg">HTML </span>报告</a></div></div>`}
      </header><div id="ud-body"></div></div>`;
    document.body.appendChild(el);
    if (!REPORT) {
      el.querySelector('#ud-ranges').addEventListener('click', e => { const b = e.target.closest('[data-r]'); if (b) setRange(b.dataset.r); });
      el.querySelector('#ud-close').addEventListener('click', closeUsageDash);
      document.addEventListener('keydown', e => { if (e.key === 'Escape' && el.classList.contains('on')) closeUsageDash(); });
    }
    el.addEventListener('click', onBodyClick);
    el.addEventListener('keydown', e => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.closest('.ud-hbars.clk .ud-hbar, tr.clk')) { e.preventDefault(); onBodyClick(e); }
    });
    window.addEventListener('resize', () => {
      clearTimeout(resizeT);
      resizeT = setTimeout(() => { if (last && el.classList.contains('on')) drawCharts(last); }, 140);
    });
    return el;
  }

  function setRange(r) {
    range = r;
    try { localStorage.setItem('lc.usage.range', r); } catch (e) {}
    load();
  }

  async function getJ(path) {
    const r = await fetch(path, {credentials: 'same-origin'});
    if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
    return r.json();
  }

  async function load() {
    const el = root(), my = ++seq, body = el.querySelector('#ud-body');
    if (REPORT) { last = REPORT; render(body, last); return; }
    el.querySelectorAll('#ud-ranges .ud-chip').forEach(b => { const on = b.dataset.r === range; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
    el.querySelector('#ud-export').href = `/api/usage/export.csv?range=${range}`;
    el.querySelector('#ud-export').setAttribute('download', `litecode_usage_${range}.csv`);
    el.querySelector('#ud-report').href = `/api/usage/report.html?range=${range}`;
    el.querySelector('#ud-report').setAttribute('download', `litecode_usage_report_${range}.html`);
    if (!body.innerHTML) body.innerHTML = '<div class="ud-muted ud-pad">加载中…</div>';
    body.classList.add('ud-load');          /* 重新取数时保留上一帧, 半透明, 不闪骨架 */
    try {
      const q = `?range=${range}`;
      const [summary, skills, sessions, tools] = await Promise.all([
        getJ('/api/usage/summary' + q), getJ('/api/usage/skills' + q),
        getJ('/api/usage/sessions' + q + '&limit=10'), getJ('/api/usage/tools' + q)]);
      if (my !== seq) return;
      last = {range, summary, skills: skills.skills || [], sessions: sessions.sessions || [], tools: tools.tools || []};
      render(body, last);
    } catch (e) {
      if (my !== seq) return;
      body.innerHTML = `<div class="ud-empty">${emptyArt()}<div class="t">看板数据加载失败</div><div class="d">${esc(e.message || e)}</div>
        <button class="ud-btn" data-act="retry">重试</button></div>`;
    } finally {
      if (my === seq) body.classList.remove('ud-load');
    }
  }

  /* ── 数据整形 ── */
  function buckets(summary) {
    const hour = summary.bucket === 'hour';
    return (summary.series || []).map(b => ({
      label: hour ? b.key + '时' : b.key.slice(5).replace('-', '/'),
      title: hour ? `今天 ${b.key}:00–${b.key}:59` : b.key,
      calls: b.calls,
      v: {cached: b.cached, uncached: b.uncached, output: Math.max(0, b.completion - b.reasoning), reasoning: b.reasoning},
    }));
  }
  const col = (summary, f) => (summary.series || []).map(f);

  function delta(cur, prev, hero) {
    const pt = last && last.summary.prev_totals;
    if (!pt || last.range === 'all') return '';
    const name = PREV_NAME[last.range] || '上期';
    if (!pt.calls) return hero ? `<span class="ud-delta">${esc(name)}没有记录, 暂无环比</span>` : '';
    if (!prev) return cur ? `<span class="ud-delta">${esc(name)}为 0</span>` : '';
    const d = (cur - prev) / prev, up = d >= 0;
    const v = Math.abs(d) >= 10 ? `${Math.round(Math.abs(d))} 倍` : `${(Math.abs(d) * 100).toFixed(Math.abs(d) < 0.1 ? 1 : 0)}%`;
    return `<span class="ud-delta" title="较${esc(name)}: ${full(prev)}"><span class="arr">${up ? '▲' : '▼'}</span>${v} <span class="vs">较${esc(name)}</span></span>`;
  }

  function kpi(o, i) {
    return `<div class="ud-kpi" style="--d:${i}"><div class="k">${o.sw ? `<i class="ud-sw" style="background:var(${o.sw})"></i>` : ''}${o.label}</div>
      <div class="v" title="${full(o.value)}">${fmt(o.value)}</div>
      <div class="s">${o.sub || ''}</div><div class="ft">${delta(o.value, o.prev)}${C.sparkline(o.spark, {w: 96, h: 28})}</div></div>`;
  }

  function emptyArt() {
    return `<svg class="ud-art" viewBox="0 0 160 110" aria-hidden="true"><rect x="14" y="16" width="132" height="80" rx="14" class="a1"/>
      <rect x="34" y="58" width="14" height="22" rx="4" class="a2"/><rect x="58" y="44" width="14" height="36" rx="4" class="a3"/>
      <rect x="82" y="64" width="14" height="16" rx="4" class="a2"/><rect x="106" y="36" width="14" height="44" rx="4" class="a3"/>
      <path d="M30 86H130" class="a4"/><circle cx="128" cy="22" r="11" class="a5"/><path d="M124 22h8M128 18v8" class="a6"/></svg>`;
  }

  /* ── 渲染 ── */
  function render(body, d) {
    const s = d.summary, t = s.totals || {}, p = s.prev_totals || {};
    const rname = RANGE_LONG[d.range] || d.range;
    if (!t.calls) {
      body.innerHTML = `<div class="ud-empty">${emptyArt()}<div class="t">${esc(rname)}还没有用量记录</div>
        <div class="d">和 LiteCode 聊几句, 每次调用模型的 token 都会记在这里${d.range === 'today' ? '; 也可以切到「全部」看历史' : ''}。<br>
        老会话的用量可用 <code>python3 scripts/usage_backfill.py</code> 回填。</div></div>`;
      return;
    }
    const uncached = Math.max(0, t.prompt - t.cached), outText = Math.max(0, t.completion - t.reasoning);
    const est = t.estimated_calls ? ` · ${t.estimated_calls} 次估算` : '';
    const tiles = [
      {label: '缓存命中输入', sw: '--ud-s1', value: t.cached, prev: p.cached, sub: `占输入 ${pct(t.cached, t.prompt)}`, spark: col(s, b => b.cached)},
      {label: '未命中输入', sw: '--ud-s2', value: uncached, prev: p.prompt != null ? p.prompt - p.cached : null, sub: `占输入 ${pct(uncached, t.prompt)}`, spark: col(s, b => b.uncached)},
      {label: '输出', sw: '--ud-s3', value: t.completion, prev: p.completion, sub: `正文 ${fmt(outText)} · 思考 ${fmt(t.reasoning)}`, spark: col(s, b => b.completion)},
      {label: '思考', sw: '--ud-s4', value: t.reasoning, prev: p.reasoning, sub: `占输出 ${pct(t.reasoning, t.completion)}`, spark: col(s, b => b.reasoning)},
      {label: '调用次数', value: t.calls, prev: p.calls, sub: `${t.failed_calls ? t.failed_calls + ' 次失败 · ' : ''}均耗时 ${(t.avg_ms / 1000).toFixed(1)}s${est}`, spark: col(s, b => b.calls)},
      {label: '平均每轮', value: t.avg_per_turn, prev: p.avg_per_turn, sub: `${full(t.turns)} 轮对话`, spark: col(s, b => b.turns ? (b.cached + b.uncached + b.completion) / b.turns : 0)},
    ];
    const cachedW = t.prompt ? t.cached / t.prompt * 100 : 0;
    body.innerHTML = `
      <section class="ud-kpis">
        <div class="ud-kpi hero" style="--d:0"><div class="k">总 token · ${esc(rname)}</div>
          <div class="hv" title="${full(t.total)}">${fmt(t.total)}</div>
          <div class="s">输入 ${fmt(t.prompt)} + 输出 ${fmt(t.completion)}</div>
          <div class="ft">${delta(t.total, p.total, true)}</div>
          <div class="ud-hero-spark">${C.sparkline(col(s, b => b.cached + b.uncached + b.completion), {w: 420, h: 56})}</div>
          <div class="ud-meter" role="img" aria-label="输入里缓存命中 ${pct(t.cached, t.prompt)}"><span class="m1" style="width:${cachedW.toFixed(2)}%"></span><span class="m2" style="width:${(100 - cachedW).toFixed(2)}%"></span></div>
          <div class="ud-meter-lg"><span><i class="ud-sw" style="background:var(--ud-s1)"></i>缓存命中 ${pct(t.cached, t.prompt)}</span><span><i class="ud-sw" style="background:var(--ud-s2)"></i>未命中 ${pct(uncached, t.prompt)}</span></div>
          <details class="ud-why"><summary>为什么缓存命中占大头?</summary><p>每次调模型都要带上约 2.5 万 token 的固定前缀 (系统提示词 + 工具说明) 和整段对话历史,
            一轮里多次工具调用就会重复发多次。这些重复部分大多命中服务端的前缀缓存: 不用重新计算, 速度快, 多数平台按原价 1/10 左右计费。
            真正的新增开销主要看「未命中输入」和「输出」。</p></details></div>
        ${tiles.map((o, i) => kpi(o, i + 1)).join('')}
      </section>
      <section class="ud-card" style="--d:3"><div class="ud-ch"><h3>${s.bucket === 'hour' ? '按小时' : '按天'} token 用量</h3>
          <button class="ud-link" data-act="table" aria-pressed="${showTable}">${showTable ? '看图' : '表格'}</button></div>
        <div class="ud-legend">${SERIES.map(x => `<span><i class="ud-sw" style="background:var(${x.color})"></i>${x.name}</span>`).join('')}</div>
        <div class="ud-chart" id="ud-chart"></div><div class="ud-tablev" id="ud-tablev"></div></section>
      <section class="ud-card" style="--d:4"><div class="ud-ch"><h3>活跃时段</h3><span class="sub">星期 × 小时 · 调用次数</span></div><div class="ud-chart" id="ud-heat"></div></section>
      <div class="ud-grid">
        <section class="ud-card" style="--d:5" id="ud-skills"><div class="ud-ch"><h3>技能排行</h3><span class="sub">按 token · 点开看用过的会话</span></div>
          ${C.hbars(d.skills.slice(0, 8).map((r, i) => ({id: i, name: r.skill, value: r.tokens, valueText: fmt(r.tokens),
            sub: `${full(r.turns)} 轮 · ${full(r.sessions)} 个会话 · 成功 ${pct(r.success, 1)} · ${ago(r.last_ts)}`,
            title: `${r.skill}: ${full(r.tokens)} token`})), {clickable: true, empty: '这段时间没有用到技能 (自动匹配或 load_skill 加载的技能会记在这里)'})}</section>
        <section class="ud-card" style="--d:6"><div class="ud-ch"><h3>工具排行</h3><span class="sub">模型发起的调用次数</span></div>
          ${C.hbars(d.tools.slice(0, 8).map(r => ({name: r.tool, value: r.uses, valueText: `${full(r.uses)} 次`,
            sub: `${full(r.turns)} 轮 · 相关调用 ${fmt(r.tokens)} token`})), {empty: '这段时间模型没有调用工具'})}</section>
        <section class="ud-card" style="--d:7"><div class="ud-ch"><h3>模型占比</h3><span class="sub">按 token</span></div>${shareBars(s.by_model, x => x.name || '未知')}</section>
        <section class="ud-card" style="--d:8"><div class="ud-ch"><h3>来源占比</h3><span class="sub">按 token</span></div>${shareBars(s.by_source, x => srcName(x.name))}</section>
      </div>
      <section class="ud-card ud-mt" style="--d:9"><div class="ud-ch"><h3>Top 会话</h3><span class="sub">按 token${REPORT ? '' : ' · 点击跳转'}</span></div>${sessionTable(d.sessions)}</section>`;
    drawCharts(d);
  }

  function drawCharts(d) {
    const box = document.getElementById('ud-chart'), tv = document.getElementById('ud-tablev');
    if (!box) return;
    const bs = buckets(d.summary);
    box.style.display = showTable ? 'none' : '';
    tv.style.display = showTable ? '' : 'none';
    if (showTable) tv.innerHTML = bucketTable(bs, d.summary.bucket);
    else C.stackedColumns(box, bs, SERIES, {height: window.innerWidth < 760 ? 200 : 250, label: 'token 用量堆叠柱状图'});
    const heat = document.getElementById('ud-heat');
    if (heat) C.heatmap(heat, d.summary.heatmap || []);
  }

  function bucketTable(bs, bucket) {
    const rows = bs.filter(b => b.calls);
    if (!rows.length) return '<div class="ud-muted">暂无数据</div>';
    return `<div class="ud-scroll"><table class="ud-tbl"><thead><tr><th>${bucket === 'hour' ? '时段' : '日期'}</th>${SERIES.map(s => `<th class="n">${s.name}</th>`).join('')}<th class="n">调用</th></tr></thead>
      <tbody>${rows.map(b => `<tr><td>${esc(b.title)}</td>${SERIES.map(s => `<td class="n">${full(b.v[s.key])}</td>`).join('')}<td class="n">${full(b.calls)}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function shareBars(rows, nameOf) {
    rows = rows || [];
    const tot = rows.reduce((a, r) => a + (r.total || 0), 0) || 1;
    const top = rows.slice(0, 5).map(r => ({name: nameOf(r), value: r.total, valueText: `${fmt(r.total)} · ${pct(r.total, tot)}`,
      title: `${full(r.total)} token · ${full(r.calls)} 次调用${r.estimated_calls ? ` · ${r.estimated_calls} 次估算` : ''}`}));
    const rest = rows.slice(5);
    if (rest.length) {
      const v = rest.reduce((a, r) => a + (r.total || 0), 0);
      top.push({name: `其他 ${rest.length} 项`, value: v, valueText: `${fmt(v)} · ${pct(v, tot)}`,
        title: rest.map(r => nameOf(r)).join('、')});
    }
    return C.hbars(top);
  }

  function sessionTable(rows) {
    if (!rows.length) return '<div class="ud-muted">暂无数据</div>';
    return `<table class="ud-tbl"><thead><tr><th>会话</th><th class="hide-sm">来源</th><th class="n">轮数</th><th class="n">token</th></tr></thead><tbody>${rows.map(s => `
      <tr${REPORT ? '' : ` class="clk" data-sid="${esc(s.session_id)}" tabindex="0"`} title="${esc(s.session_id)}"><td class="nm">${esc(s.title || s.session_id)}</td>
      <td class="hide-sm">${esc(srcName(s.source))}</td><td class="n">${full(s.turns)}</td><td class="n">${fmt(s.total)}</td></tr>`).join('')}</tbody></table>`;
  }

  function toggleSkill(row) {
    const sk = last && last.skills[+row.dataset.id];
    if (!sk) return;
    const nx = row.nextElementSibling;
    if (nx && nx.classList.contains('ud-sub')) { nx.remove(); row.classList.remove('open'); return; }
    row.classList.add('open');
    const sub = document.createElement('div');
    sub.className = 'ud-sub';
    const rows = sk.sessions_list || [];
    sub.innerHTML = rows.length ? rows.map(s => `<a${REPORT ? '' : ` data-sid="${esc(s.session_id)}" href="#"`} title="${esc(s.session_id)}"><span>${esc(s.title || s.session_id)}</span>
      <span class="v">${fmt(s.tokens)} · ${full(s.turns)} 轮</span></a>`).join('') : '<div class="ud-muted">没有会话记录</div>';
    row.after(sub);
  }

  function jumpSession(sid) {
    if (REPORT) return;
    const list = (typeof sess !== 'undefined' && Array.isArray(sess)) ? sess : [];
    if (typeof swS === 'function' && list.some(x => x.id === sid)) { closeUsageDash(); swS(sid); return; }
    const msg = sid.startsWith('dag:') ? 'DAG 任务没有对话页, 请到 DAG 编排里看构建记录' : `会话 ${sid} 不在 Web 会话列表里 (可能来自微信 / CLI, 或已删除)`;
    if (typeof toast === 'function') toast(msg, 3200);
  }

  function onBodyClick(e) {
    const a = e.target.closest('[data-sid]');
    if (a) { e.preventDefault(); jumpSession(a.dataset.sid); return; }
    const row = e.target.closest('#ud-skills .ud-hbar[data-id]');
    if (row) { toggleSkill(row); return; }
    const act = e.target.closest('[data-act]');
    if (!act) return;
    if (act.dataset.act === 'retry') load();
    if (act.dataset.act === 'table' && last) {
      showTable = !showTable;
      act.textContent = showTable ? '看图' : '表格';
      act.setAttribute('aria-pressed', showTable);
      drawCharts(last);
    }
  }

  window.openUsageDash = function () {
    const el = root();
    el.classList.add('on');
    if (typeof closeSB === 'function') closeSB();
    load();
  };
  window.closeUsageDash = function () {
    const el = document.getElementById('ud-root');
    if (el && !REPORT) el.classList.remove('on');
  };
  if (REPORT) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
    else load();
  }
})();
