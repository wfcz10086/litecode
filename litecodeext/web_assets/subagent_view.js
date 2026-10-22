/* subagent_view.js — [subagent-view 2026-10-22] 子代理不再是黑盒: 步骤时间线 / 只读视图 / 分支操作
 *
 * 1. 事件流  LCSubView.subscribe(rid, fn): 按 ?after=<seq> 增量拉 /api/sessions/{sid}/subagents/{rid}/events,
 *            运行中每 1.5s 一次, 结束且拉完即停; 同一子代理多处订阅 (面板展开 + 进入视图) 共用一份。
 * 2. 时间线  makeTimeline(el): 工具行复用 LCTimeline (chat_timeline.js) 的行渲染 —— 先按 chat_stream 的格式追加 .ti,
 *            再喂 task_exec / tool_done / notice 帧, 所以中文名、转圈、✓用时 / ✗、点开看参数与结果都与主时间线一致;
 *            思考摘要 / 正文片段 / 补充指令 / 工作树 / 最终结果是本文件自己的行。
 * 3. 进入    enter(rid): 主聊天区 (#mw) 隐藏, 换成该子代理的只读视图 (面包屑"主对话 › #2 编码员", ← 返回 / Esc),
 *            实时追加事件; 底部输入框改为"给 #2 发补充指令" (走现有 send 接口)。主对话的 DOM 与流式不动,
 *            返回时恢复滚动位置 (原来在底部就回到底部) 与输入框草稿。
 * 4. 分支    有独立工作树的子代理: 分支名 / 改动文件数 + [看改动] (按文件折叠的 diff 卡片) [合并] [丢弃] (两步确认)。
 * 不用 alert / confirm / prompt。依赖 app.js 的全局 cur (只读) 与 LCSubagents (subagent_panel.js)。
 */
(function () {
  const POLL_MS = 1500, ARM_MS = 4000;
  const ROLE_CN = { coder: '编码员', researcher: '研究员', explorer: '探索员', analyst: '分析师', tester: '测试员',
    shell: '命令行', writer: '写作者', critic: '审查员', vision: '识图员', operator: '操作员' };
  const roleCN = t => ROLE_CN[t] || t || '子代理';
  const streams = new Map();     // rid -> {sid, rid, events, last, done, item, subs:Set, timer, busy}
  let view = null;               // 当前进入的只读视图

  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function btn(text, cls) { const b = h('button', 'sa-btn ' + (cls || ''), text); b.type = 'button'; return b; }
  function curSid() { return (typeof cur !== 'undefined' && cur) ? cur : null; }
  const api = (sid, rid, tail) => `/api/sessions/${encodeURIComponent(sid)}/subagents/${encodeURIComponent(rid)}${tail || ''}`;
  function fmtSecs(s) {
    s = Math.max(0, s | 0);
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0') + 's';
    return Math.floor(s / 3600) + 'h' + String(Math.floor(s % 3600 / 60)).padStart(2, '0') + 'm';
  }
  async function post(url) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    let d = {};
    try { d = await r.json(); } catch (e) { /* 非 JSON */ }
    if (!r.ok) return { ok: false, error: (d && d.detail) || `HTTP ${r.status}` };
    return d;
  }
  function itemOf(rid) {
    const st = window.LCSubagents && LCSubagents.state();
    return st && (st.items || []).find(i => i.id === rid) || null;
  }
  function numOf(rid) {
    const st = window.LCSubagents && LCSubagents.state();
    const i = st ? (st.items || []).findIndex(x => x.id === rid) : -1;
    return i >= 0 ? i + 1 : '?';
  }

  /* ── 1. 事件流 ─────────────────────────────────────────── */
  function subscribe(rid, fn) {
    const sid = curSid();
    if (!sid) return () => {};
    let s = streams.get(rid);
    if (!s || s.sid !== sid) {
      s = { sid, rid, events: [], last: 0, done: false, item: null, subs: new Set(), timer: 0, busy: false };
      streams.set(rid, s);
    }
    s.subs.add(fn);
    if (s.events.length || s.item) fn(s.events, s.item, true);
    if (!s.timer && !s.busy) pull(s);
    return () => {
      s.subs.delete(fn);
      if (!s.subs.size && s.timer) { clearTimeout(s.timer); s.timer = 0; }
    };
  }
  async function pull(s) {
    s.timer = 0;
    if (!s.subs.size || s.sid !== curSid()) return;
    s.busy = true;
    let again = !s.done, fast = false;
    try {
      const r = await fetch(api(s.sid, s.rid, `/events?after=${s.last}`));
      if (r.ok) {
        const d = await r.json();
        const evs = (d.items || []).filter(e => e.seq > s.last);
        if (evs.length) { s.events.push(...evs); s.last = evs[evs.length - 1].seq; }
        if (s.events.length > 600) s.events.splice(0, s.events.length - 600);
        s.item = d.item || s.item;
        fast = !!d.more;
        s.done = !!d.done && !d.more;
        again = !s.done;
        if (evs.length || d.item) s.subs.forEach(fn => { try { fn(evs, s.item, false); } catch (e) { console.warn(e); } });
      } else if (r.status === 404 || r.status === 403) { again = false; }
    } catch (e) { /* 网络抖动: 下次再拉 */ }
    finally { s.busy = false; }
    if (again && s.subs.size && !document.hidden) s.timer = setTimeout(() => pull(s), fast ? 50 : POLL_MS);
    else if (again && s.subs.size) s.timer = setTimeout(() => pull(s), POLL_MS * 4);
  }
  function reset() { for (const s of streams.values()) if (s.timer) clearTimeout(s.timer); streams.clear(); }

  /* ── 2. 时间线 ─────────────────────────────────────────── */
  function makeTimeline(body, opts) {
    opts = opts || {};
    body.classList.add('sav-body');
    const tg = h('div', 'tg sav-tg'); body.appendChild(tg);
    const keys = {}; let n = 0;
    const TL = () => window.LCTimeline;
    function add(ev) {
      const k = ev.kind;
      if (k === 'tool_start') {
        const key = 'sav' + ev.seq + '-' + (++n);
        keys[ev.id || ''] = key;
        const ti = h('div', 'ti');
        const tn = h('span', 'tn', ev.name || '工具'); tn.dataset.tool = ev.name || '';
        let a = String(ev.args || '');
        try { const o = JSON.parse(a); const v = o && (o.command || o.filepath || o.path || o.query || o.url || o.pattern); if (v) a = String(v); } catch (e) { /* 非 JSON */ }
        ti.append(tn, h('span', 'ta', a.slice(0, 90)));
        tg.appendChild(ti);
        if (TL()) TL().onDelta(body, { task_exec: { status: 'executing', id: key, name: ev.name, args: ev.args || '' } });
        return;
      }
      if (k === 'tool_done') {
        const key = keys[ev.id || ''];
        if (key && TL()) TL().onDelta(body, { tool_done: Object.assign({}, ev, { id: key }) });
        return;
      }
      if (k === 'notice') { if (TL()) TL().onDelta(body, { notice: { text: ev.text, level: ev.level } }); return; }
      let row = null;
      if (k === 'start') {
        row = h('div', 'sav-ev sav-start');
        row.append(h('span', 'sav-ic', '▶'), h('span', '', `开始 · ${roleCN(ev.agent_type)}${ev.background ? ' · 后台' : ''}`));
      } else if (k === 'thinking') {
        row = h('div', 'sav-ev sav-think', '💭 ' + (ev.text || ''));
        row.title = '点开 / 收起';
        row.addEventListener('click', () => row.classList.toggle('open'));
      } else if (k === 'text') {
        row = h('div', 'sav-ev sav-text', ev.text || '');
      } else if (k === 'instruction') {
        row = h('div', 'sav-ev sav-ins', `✉ 补充指令${ev.state === 'delivered' ? ' (已送达)' : ' (已投递, 下一轮迭代前送达)'}: ${ev.text || ''}`);
      } else if (k === 'worktree') {
        row = h('div', 'sav-ev sav-wtev', `⎇ 工作树 ${ev.branch || ev.id || ''} · ${ev.status_cn || ev.status || ''}` + (ev.files ? ` · ${ev.files} 个文件改动` : ''));
      } else if (k === 'end') {
        row = h('div', 'sav-ev sav-end st-' + (ev.status || ''));
        row.appendChild(h('div', 'sav-end-h', `■ 结束 · ${ev.status_cn || ev.status || ''} · 用时 ${fmtSecs(ev.elapsed)}`));
        if (ev.result) {
          const pre = h('pre', 'sav-res' + (opts.full ? ' full' : ''), ev.result);
          row.appendChild(pre);
          if (!opts.full) row.addEventListener('click', () => pre.classList.toggle('full'));
        }
      }
      if (row) tg.appendChild(row);
    }
    return {
      add(evs) {
        const atBottom = opts.scroller ? (opts.scroller.scrollHeight - opts.scroller.scrollTop - opts.scroller.clientHeight < 80) : false;
        (evs || []).forEach(add);
        if (opts.scroller && atBottom) opts.scroller.scrollTop = opts.scroller.scrollHeight;
      },
      clear() { tg.textContent = ''; },
    };
  }

  /* ── 4. 分支: 工作树行 + diff 卡片 ─────────────────────── */
  function wtBox(rid, onChange) {
    const box = h('div', 'sav-wt'); box.hidden = true;
    const line = h('span', 'sav-wt-line');
    const bDiff = btn('看改动', 'sav-diff'), bMerge = btn('合并', 'sav-merge'), bDisc = btn('丢弃', 'sav-discard');
    const note = h('div', 'sa-note'); note.hidden = true;
    const diff = h('div', 'sav-diffbox'); diff.hidden = true;
    const acts = h('span', 'sav-wt-acts'); acts.append(bDiff, bMerge, bDisc);
    box.append(line, acts, note, diff);
    const armed = { merge: 0, discard: 0 };
    function say(t, bad) { note.textContent = t || ''; note.hidden = !t; note.classList.toggle('bad', !!bad); }
    function arm(b, key, label, confirmLabel) {
      if (Date.now() - armed[key] > ARM_MS) {
        armed[key] = Date.now(); b.textContent = confirmLabel; b.classList.add('armed');
        setTimeout(() => { if (Date.now() - armed[key] >= ARM_MS) { b.textContent = label; b.classList.remove('armed'); } }, ARM_MS + 50);
        return false;
      }
      armed[key] = 0; b.textContent = label; b.classList.remove('armed');
      return true;
    }
    async function act(b, tail, label) {
      const sid = curSid(); if (!sid) return;
      b.disabled = true; say(label + '中…');
      try {
        const d = await post(api(sid, rid, tail));
        say(d.ok ? (d.message || '完成') : (d.error || '失败'), !d.ok);
        if (d.ok && !diff.hidden) loadDiff();
      } catch (e) { say(label + '失败 (网络)', true); }
      finally { b.disabled = false; window.LCSubagents && LCSubagents.refresh(); onChange && onChange(); }
    }
    async function loadDiff() {
      const sid = curSid(); if (!sid) return;
      diff.hidden = false; diff.textContent = '加载改动…';
      try {
        const r = await fetch(api(sid, rid, '/diff'));
        const d = r.ok ? await r.json() : { ok: false, error: `HTTP ${r.status}` };
        diff.textContent = '';
        if (!d.ok) { diff.appendChild(h('div', 'sa-note bad', d.error || '加载失败')); return; }
        diff.appendChild(diffCard(d));
      } catch (e) { diff.textContent = '加载失败 (网络)'; }
    }
    bDiff.addEventListener('click', () => { if (!diff.hidden && diff.childNodes.length) { diff.hidden = true; diff.textContent = ''; bDiff.textContent = '看改动'; return; } bDiff.textContent = '收起改动'; loadDiff(); });
    bMerge.addEventListener('click', () => { if (arm(bMerge, 'merge', '合并', '确认合并?')) act(bMerge, '/merge', '合并'); });
    bDisc.addEventListener('click', () => { if (arm(bDisc, 'discard', '丢弃', '确认丢弃?')) act(bDisc, '/discard', '丢弃'); });
    return {
      el: box,
      update(it) {
        const w = it && it.worktree;
        box.hidden = !w;
        if (!w) return;
        line.textContent = `⎇ ${w.branch || w.id} · ${w.files || 0} 个文件改动 · ${w.status_cn || w.status}`
          + (w.base_branch ? ` · 基分支 ${w.base_branch}` : '');
        line.title = w.note || '';
        const open = !['merged', 'discarded', 'no_changes', 'missing'].includes(w.status);
        const ctl = open && it.status !== 'running';
        bMerge.hidden = !ctl; bDisc.hidden = !ctl;
        bDiff.hidden = w.status === 'no_changes' || w.status === 'missing' || w.status === 'discarded';
        box.dataset.wt = w.status;
      },
    };
  }

  function diffCard(d) {
    const card = h('div', 'sav-dcard');
    const files = splitDiff(d.diff || '');
    card.appendChild(h('div', 'sav-dhead', `${files.length} 个文件` + (d.truncated ? ` · 太长已截断 (共 ${Math.round((d.bytes || 0) / 1024)}KB)` : '')));
    if (!files.length) card.appendChild(h('div', 'sa-note', '(没有差异)'));
    files.forEach((f, i) => {
      const det = h('details', 'sav-df'); if (files.length <= 3 || i === 0) det.open = true;
      const sum = h('summary', 'sav-dfs');
      sum.append(h('span', 'sav-dfp', f.path), h('span', 'sav-dadd', '+' + f.add), h('span', 'sav-ddel', '−' + f.del));
      const pre = h('pre', 'sav-dpre');
      f.lines.forEach(ln => {
        const c = ln.startsWith('@@') ? 'dh' : ln.startsWith('+') ? 'da' : ln.startsWith('-') ? 'dd' : '';
        const s = h('span', c, ln + '\n'); pre.appendChild(s);
      });
      det.append(sum, pre); card.appendChild(det);
    });
    return card;
  }
  function splitDiff(text) {
    const out = []; let f = null;
    String(text).split('\n').forEach(ln => {
      const m = ln.match(/^diff --git a\/(.+?) b\/(.+)$/);
      if (m) { f = { path: m[2], add: 0, del: 0, lines: [] }; out.push(f); return; }
      if (!f || /^(index |--- |\+\+\+ |new file mode|deleted file mode|similarity |rename )/.test(ln)) return;
      if (ln.startsWith('+')) f.add++; else if (ln.startsWith('-')) f.del++;
      f.lines.push(ln);
    });
    out.forEach(x => { while (x.lines.length && x.lines[x.lines.length - 1] === '') x.lines.pop(); });
    return out;
  }

  /* ── 面板行扩展 (subagent_panel.js 的 build / update 调用) ── */
  function rowExtras(rid) {
    const ext = h('div', 'sav-rowx');
    const acts = h('div', 'sav-rowacts');
    const bSteps = btn('步骤 ▸', 'sav-steps'), bEnter = btn('进入 →', 'sav-enter');
    acts.append(bSteps, bEnter);
    const wt = wtBox(rid);
    const mini = h('div', 'sav-mini'); mini.hidden = true;
    ext.append(wt.el, mini);
    let unsub = null, tl = null;
    bSteps.addEventListener('click', () => {
      mini.hidden = !mini.hidden;
      bSteps.textContent = mini.hidden ? '步骤 ▸' : '步骤 ▾';
      if (!mini.hidden) {
        mini.textContent = ''; tl = makeTimeline(mini, { scroller: mini });
        unsub = subscribe(rid, (evs) => tl.add(evs));
      } else if (unsub) { unsub(); unsub = null; }
    });
    bEnter.addEventListener('click', () => enter(rid));
    return { el: ext, acts, update: it => wt.update(it), close() { if (unsub) unsub(); } };
  }

  /* ── 3. 进入: 只读视图 ─────────────────────────────────── */
  function enter(rid) {
    const main = document.getElementById('main'), mw = document.getElementById('mw'), ia = document.getElementById('ia');
    const mi = document.getElementById('mi');
    if (!main || !mw || !ia || !mi) return;
    if (view) exit(true);
    const it = itemOf(rid) || { id: rid };
    const no = numOf(rid);
    const saved = { top: mw.scrollTop, bottom: mw.scrollHeight - mw.scrollTop - mw.clientHeight < 150,
      value: mi.value, ph: mi.placeholder, sid: curSid(),
      drawer: !!(window.LCSubagents && LCSubagents.state().open) };
    if (!mi.dataset.ph0 && !window.streaming) mi.dataset.ph0 = mi.placeholder;   // 免得 _steerUi 首次运行时把视图提示当原始提示
    const root = h('div', 'sav-view'); root.id = 'sav-view';
    const top = h('div', 'sav-top');
    const back = btn('← 返回', 'sav-back');
    const crumb = h('span', 'sav-crumb');
    const home = h('a', 'sav-home', '主对话'); home.href = '#'; home.addEventListener('click', ev => { ev.preventDefault(); exit(); });
    crumb.append(home, h('span', 'sav-sep', ' › '), h('b', 'sav-cur', `#${no} ${roleCN(it.agent_type)}`));
    const st = h('span', 'sa-st'), tm = h('span', 'sav-time');
    top.append(back, crumb, st, tm);
    const sc = h('div', 'sav-scroll'), inner = h('div', 'sav-inner');
    const info = h('div', 'sav-info');
    const task = h('div', 'sav-task', it.task || it.brief || '');
    const meta = h('div', 'sav-meta');
    const ro = h('div', 'sav-ro', '只读视图: 实时显示这个子代理的每一步; 底部输入框发的是给它的补充指令');
    const wt = wtBox(rid);
    info.append(task, meta, wt.el, ro);
    const body = h('div', '');
    inner.append(info, body); sc.appendChild(inner);
    root.append(top, sc);
    mw.after(root);                                  // 在任务树面板之上, 占主对话区的位置
    window.LCSubagents && LCSubagents.close && LCSubagents.close();   // 收起抽屉, 把空间留给视图
    main.classList.add('sav-on');
    const tl = makeTimeline(body, { scroller: sc, full: true });
    const sendBtn = document.getElementById('sav-send') || (() => {
      const b = h('button', '', '↑'); b.id = 'sav-send'; b.type = 'button'; b.title = '发补充指令';
      const sdb = document.getElementById('sdb'); sdb && sdb.parentNode.insertBefore(b, sdb);
      b.addEventListener('click', ev => { ev.preventDefault(); sendInstr(); });
      return b;
    })();
    mi.value = ''; typeof arx === 'function' && arx(mi);
    view = { rid, no, root, saved, tl, wt, st, tm, meta, task, sendBtn, item: it, unsub: null };
    paint(it);
    view.unsub = subscribe(rid, (evs, item) => { if (!view || view.rid !== rid) return; tl.add(evs); if (item) paint(item); });
    back.addEventListener('click', () => exit());
    sc.scrollTop = 0;
    mi.focus();
  }
  function paint(it) {
    if (!view || !it) return;
    view.item = it;
    const running = it.status === 'running';
    view.st.textContent = it.stop_requested && running ? '停止中' : (it.status_cn || it.status || '');
    view.st.className = 'sa-st st-' + (it.status || '');
    view.tm.textContent = it.elapsed != null ? fmtSecs(it.elapsed) : '';
    if (it.task) view.task.textContent = it.task;
    view.meta.textContent = `${roleCN(it.agent_type)}${it.background ? ' · 后台' : ' · 前台'} · 步数 ${it.steps || 0}`
      + ` · 当前: ${it.current_step || '-'}` + (it.parent_id ? ` · 父任务 ${it.parent_id}` : '') + ` · ${it.id}`;
    view.wt.update(it);
    const mi = document.getElementById('mi');
    const ctl = running && !it.stop_requested;
    if (mi) {
      mi.placeholder = ctl ? `给 #${view.no} 发补充指令 (它下一轮迭代前收到, Enter 发送)` : `#${view.no} 已${it.stop_requested && running ? '请求停止' : '结束'}, 只能回看 · ← 返回主对话`;
      mi.disabled = !ctl;
    }
    view.sendBtn.disabled = !ctl;
  }
  async function sendInstr() {
    if (!view) return;
    const mi = document.getElementById('mi'), sid = curSid();
    const msg = (mi.value || '').trim();
    if (!msg || !sid) return;
    view.sendBtn.disabled = true;
    try {
      const r = await fetch(api(sid, view.rid, '/send'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: msg }) });
      let d = {}; try { d = await r.json(); } catch (e) { /* 非 JSON */ }
      if (r.ok && d.ok) { mi.value = ''; typeof arx === 'function' && arx(mi); typeof toast === 'function' && toast(`已投递给 #${view.no}`); }
      else typeof toast === 'function' && toast((d && (d.error || d.detail)) || `发送失败 (HTTP ${r.status})`, 3500);
    } catch (e) { typeof toast === 'function' && toast('发送失败 (网络)'); }
    finally { if (view) { view.sendBtn.disabled = false; paint(view.item); } window.LCSubagents && LCSubagents.refresh(); }
  }
  function exit(quiet) {
    if (!view) return;
    const v = view; view = null;
    if (v.unsub) v.unsub();
    v.root.remove();
    const main = document.getElementById('main'), mw = document.getElementById('mw'), mi = document.getElementById('mi');
    main && main.classList.remove('sav-on');
    if (mi) {
      mi.disabled = false;
      /* 视图期间主轮次可能已结束: 按当前流式状态重设 (chat_stream 的 _steerUi), 别还原成进入时的旧提示 */
      mi.placeholder = v.saved.ph;
      if (typeof _steerUi === 'function') { if (window.streaming) _steerUi(true); else if (mi.dataset.ph0) mi.placeholder = mi.dataset.ph0; }
      if (v.saved.sid === curSid()) mi.value = v.saved.value; typeof arx === 'function' && arx(mi); }
    if (mw) mw.scrollTop = v.saved.bottom ? mw.scrollHeight : v.saved.top;
    if (v.saved.drawer && v.saved.sid === curSid() && window.LCSubagents && LCSubagents.open) LCSubagents.open();   // 进入前抽屉开着 → 返回时再打开
    if (!quiet && mi) mi.focus();
  }

  /* 只读视图里: Enter 发补充指令 (不触发主对话的 send); Esc 返回 */
  document.addEventListener('keydown', ev => {
    if (!view) return;
    if (ev.key === 'Escape' && !ev.target.closest('.sa-panel')) { ev.preventDefault(); exit(); return; }
    if (ev.target && ev.target.id === 'mi' && ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault(); ev.stopPropagation(); sendInstr();
    }
  }, true);
  /* 切会话 / 新建会话 → 退出视图并清掉事件流 */
  let lastSid = null;
  setInterval(() => {
    const c = curSid();
    if (c !== lastSid) { lastSid = c; if (view) exit(true); reset(); return; }
    if (view) { const it = itemOf(view.rid); paint(it && it !== view.item ? it : view.item); }   // 主轮次结束时 _steerUi 会改输入框提示, 每秒重设
  }, 1000);

  window.LCSubView = { subscribe, makeTimeline, rowExtras, enter, exit, roleCN, splitDiff, active: () => view && view.rid };
})();
