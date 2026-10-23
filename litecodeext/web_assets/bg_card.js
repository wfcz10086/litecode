// bg_card.js — 当前对话底部 (输入框上方) 的「⏳ 后台进行中」卡片 [2026-10-23]
// 长任务 (MV / 视频生成) 放后台跑后本轮对话就结束了, 用户看对话"不更新了"以为卡住。
// 这里只列属于当前会话 (/api/bgprocs 的 sid === cur) 的后台任务: 说明 + 已运行时长 + 日志最后一条进度,
// 点开看最近 20 行日志, 「停止」走 confirmCard 二次确认。每 10 秒刷新; 结束后显示结果, 30 秒后收起。
(function () {
  'use strict';
  const POLL_MS = 10000, HIDE_MS = 30000, TAIL_N = 20;
  const SCRIPT_RE = /\.(py|sh|js|mjs|ts|rb|pl)$/;

  const clip = (s, n) => (s.length > n ? s.slice(0, n) + '…' : s);
  const unq = t => String(t || '').replace(/^['"]+|['"]+$/g, '');
  const base = t => unq(t).replace(/\/+$/, '').split('/').pop();

  /** 命令 → 任务说明: "脚本名 子命令 …/输出目录名"; 不是脚本就给去掉 cd/export 后的命令 */
  function bgDesc(cmd) {
    const c = String(cmd || '').trim();
    const segs = c.split(/\s*(?:&&|\|\||;|\n)\s*/).map(s => s.trim())
      .filter(s => s && !/^(cd|export|source|set|trap)\s/.test(s) && !/^\.\s/.test(s));
    const seg = segs.find(s => s.split(/\s+/).some(t => SCRIPT_RE.test(unq(t))));
    if (!seg) return clip(segs.join('; ') || c, 60);
    const toks = seg.split(/\s+/);
    const si = toks.findIndex(t => SCRIPT_RE.test(unq(t)));
    const out = [base(toks[si])];
    const nx = toks[si + 1];
    if (nx && !nx.startsWith('-') && !nx.includes('/') && !SCRIPT_RE.test(nx)) out.push(unq(nx));
    const pth = toks.slice(si + 1).find(t => !t.startsWith('-') && unq(t).includes('/'));
    if (pth) out.push('…/' + base(pth));
    return clip(out.join(' '), 80);
  }

  /** 日志最后一条有意义的行: 跳过空行和 [BG-EXIT …]; 进度条 \r 覆盖的取最后一段 */
  function bgLastLine(log) {
    const ls = String(log || '').split('\n');
    for (let i = ls.length - 1; i >= 0; i--) {
      const parts = ls[i].split('\r').map(x => x.trim()).filter(Boolean);
      const l = parts.length ? parts[parts.length - 1] : '';
      if (!l || /^\[BG-EXIT code=/.test(l)) continue;
      return clip(l, 160);
    }
    return '';
  }

  function bgExitCode(log) {
    const m = String(log || '').match(/\[BG-EXIT code=(\d+)\]/g);
    return m ? parseInt(m[m.length - 1].replace(/\D/g, ''), 10) : null;
  }

  function bgDur(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (sec < 60) return s + '秒';
    if (sec < 3600) return m + '分' + s + '秒';
    return h + '时' + m + '分';
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { bgDesc, bgLastLine, bgExitCode, bgDur };
    return;
  }

  // ── DOM ────────────────────────────────────────────────────
  const tasks = new Map();   // pid → {p, tail, line, code, doneAt, open}
  let box = null, shownSid, seq = 0, lastPoll = 0, lastStreaming = false;
  const h = s => (typeof esc === 'function' ? esc(s) : String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])));

  function host() {
    if (box && box.isConnected) return box;
    const ia = document.getElementById('ia');
    if (!ia) return null;
    box = document.createElement('div');
    box.id = 'bgc';
    box.className = 'bgc';
    box.hidden = true;
    box.addEventListener('click', onClick);
    ia.insertBefore(box, ia.firstChild);
    return box;
  }

  const isDone = t => !t.p.alive;

  function statusHtml(t) {
    if (!isDone(t)) return '';
    const code = t.p.exit_code ?? t.code;
    if (t.p.stopped) return '<span class="bgc-st stop">⏹ 已停止</span>';
    if (code === 0) return '<span class="bgc-st ok">✅ 已完成</span>';
    if (code == null) return '<span class="bgc-st ok">✅ 已结束</span>';
    return `<span class="bgc-st bad">❌ 失败 (code=${h(code)})</span>`;
  }

  function elapsed(t) {
    const ts = Number(t.p.ts) || 0;
    if (!ts) return '';
    const end = isDone(t) ? (Number(t.p.ended) || t.doneAt / 1000) : Date.now() / 1000;
    return bgDur(end - ts);
  }

  function visible() {
    const now = Date.now();
    return [...tasks.values()].filter(t => !isDone(t) || now - t.doneAt < HIDE_MS)
      .sort((a, b) => (Number(b.p.ts) || 0) - (Number(a.p.ts) || 0));
  }

  function render() {
    const el = host();
    if (!el) return;
    const list = visible();
    if (!list.length) { el.hidden = true; el.innerHTML = ''; return; }
    const running = list.filter(t => !isDone(t)).length;
    let s = `<div class="bgc-hd">${running ? `⏳ 后台进行中 <span class="bgc-n">${running}</span>` : '后台任务已结束'}</div>`;
    for (const t of list) {
      const pid = h(t.p.pid);
      s += `<div class="bgc-it${isDone(t) ? ' done' : ''}" data-pid="${pid}">
  <div class="bgc-row">
    <button class="bgc-tg" data-act="tog" aria-expanded="${t.open ? 'true' : 'false'}" title="展开 / 收起最近 ${TAIL_N} 行日志">${t.open ? '▾' : '▸'}</button>
    <span class="bgc-ds" data-act="tog" title="${h(t.p.cmd || '')}">${h(bgDesc(t.p.cmd))}</span>
    <span class="bgc-el" data-pid="${pid}">${h(elapsed(t))}</span>
    ${isDone(t) ? statusHtml(t) : `<button class="bgc-stop" data-act="stop">停止</button>`}
  </div>
  <div class="bgc-ln">${h(t.line || (isDone(t) ? '' : '(还没有输出)'))}</div>
  ${isDone(t) ? '<div class="bgc-hint">在对话里问一句"好了吗"即可拿到结果</div>' : ''}
  <pre class="bgc-log"${t.open ? '' : ' hidden'}>${h(t.tail || '(日志为空)')}</pre>
</div>`;
    }
    el.innerHTML = s;
    el.hidden = false;
  }

  function tickElapsed() {
    if (!box || box.hidden) return;
    box.querySelectorAll('.bgc-el').forEach(n => {
      const t = tasks.get(n.dataset.pid);
      if (t) n.textContent = elapsed(t);
    });
    if (visible().length !== box.querySelectorAll('.bgc-it').length) render();
  }

  async function fetchTail(t) {
    try {
      const r = await fetch(`/api/bgprocs/${encodeURIComponent(t.p.pid)}/tail?lines=${TAIL_N}`);
      if (!r.ok) return;
      const d = await r.json();
      t.tail = d.log || '';
      t.line = bgLastLine(t.tail);
      const c = bgExitCode(t.tail);
      if (c != null) t.code = c;
    } catch (e) { /* 网络抖动: 保留上次内容 */ }
  }

  async function poll() {
    const my = ++seq, sid = window.cur;
    lastPoll = Date.now();
    if (!sid) { tasks.clear(); render(); return; }
    let procs;
    try {
      const r = await fetch('/api/bgprocs');
      if (!r.ok) return;
      procs = ((await r.json()) || {}).procs || [];
    } catch (e) { return; }
    if (my !== seq || sid !== window.cur) return;
    const mine = procs.filter(p => p && p.sid === sid && !p.keep);
    const seen = new Set();
    const jobs = [];
    for (const p of mine) {
      const pid = String(p.pid);
      seen.add(pid);
      let t = tasks.get(pid);
      if (!t) { t = { p, tail: '', line: '', code: null, doneAt: 0, open: false }; tasks.set(pid, t); }
      const wasDone = !!t.doneAt;
      t.p = p;
      if (!p.alive && !t.doneAt) t.doneAt = Date.now();
      if (p.alive || !wasDone) jobs.push(fetchTail(t));   // 结束后只再取一次日志
    }
    for (const pid of [...tasks.keys()]) if (!seen.has(pid)) tasks.delete(pid);
    await Promise.all(jobs);
    if (my !== seq || sid !== window.cur) return;
    render();
  }

  async function onClick(e) {
    const a = e.target.closest('[data-act]');
    const it = e.target.closest('.bgc-it');
    if (!a || !it) return;
    const t = tasks.get(it.dataset.pid);
    if (!t) return;
    if (a.dataset.act === 'tog') {
      t.open = !t.open;
      render();
      if (t.open) {
        const pre = box.querySelector(`.bgc-it[data-pid="${CSS.escape(String(t.p.pid))}"] .bgc-log`);
        if (pre) pre.scrollTop = pre.scrollHeight;
      }
      return;
    }
    if (a.dataset.act === 'stop') {
      const ok = await confirmCard('停止后台任务', `确定停止「${bgDesc(t.p.cmd)}」?\n已运行 ${elapsed(t)}, 停止后未完成的部分不会继续生成。`, '停止');
      if (!ok) return;
      a.disabled = true;
      try {
        await fetch('/api/bgprocs/' + encodeURIComponent(t.p.pid), { method: 'DELETE' });
        if (typeof toast === 'function') toast('已停止后台任务');
      } catch (ex) { if (typeof toast === 'function') toast('停止失败: ' + ex); }
      poll();
    }
  }

  function loop() {
    if (window.cur !== shownSid) {         // 切换会话: 立即清空并拉新会话的任务
      shownSid = window.cur;
      seq++;
      tasks.clear();
      render();
      poll();
      return;
    }
    const st = !!window.streaming;           // 一轮回答刚结束 (多半刚起了后台任务): 立即刷新, 不等满 10 秒
    const turnEnded = lastStreaming && !st;
    lastStreaming = st;
    if (!document.hidden && (turnEnded || Date.now() - lastPoll >= POLL_MS)) poll();
    else tickElapsed();
  }

  function start() { host(); loop(); setInterval(loop, 1000); }
  window.bgCardRefresh = poll;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
