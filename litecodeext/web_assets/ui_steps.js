/* ui_steps.js — [ui-v2 2026-10-21] 对话过程折叠 + 统计行人话化
 *
 * 1. 每条 AI 回复里的「推理过程 / 工具调用」收成一行: ▸ 已执行 N 步 · 用时 Xs (点开是时间线),
 *    结论正文放第一眼。运行中显示 ⏳ 当前步骤 · 已用 Ns, 也可展开看实时步骤。
 *    做法: 盯 #msgs 的 DOM 变化, 把气泡顶层的 .rsb / .tg / .tgb 搬进 .lc-steps 折叠壳。
 *    chat_stream.js 用 querySelector 找 .tg / .rsb.live 追加步骤 (深层查找), 搬进壳里照样能追加;
 *    流结束重建 innerHTML 后壳没了, 观察器会再折一次。所有改动幂等, 不会自触发死循环。
 * 2. 统计行: P/C/T 改成「用时 11.9s · 3 步 · 14.9 万 tokens」, 悬停看明细 (lcUsageLine, 渲染端调用)。
 */
(function () {
  const PROC = ':scope > .rsb, :scope > .tg, :scope > .tgb';

  function fmtTok(n) {
    n = +n || 0;
    if (n >= 1e8) return (n / 1e8).toFixed(1) + ' 亿';
    if (n >= 1e4) return (n / 1e4).toFixed(1) + ' 万';
    return n.toLocaleString();
  }
  function fmtSec(s) {
    s = +s || 0;
    if (s >= 60) return Math.floor(s / 60) + ' 分 ' + Math.round(s % 60) + ' 秒';
    return (s >= 10 ? s.toFixed(0) : s.toFixed(1)) + 's';
  }

  /* 统计行 HTML: 渲染端 (chat_render.js / chat_stream.js) 调用; steps = 本条工具步数 */
  window.lcUsageLine = function (u, steps) {
    const esc_ = v => String(v == null ? '' : v).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
    const parts = [];
    if (u.elapsed_seconds) parts.push('用时 ' + fmtSec(u.elapsed_seconds));
    if (steps) parts.push(steps + ' 步');
    if (u.total_tokens) parts.push(fmtTok(u.total_tokens) + ' tokens');
    const tip = `输入 ${(u.prompt_tokens || 0).toLocaleString()} · 输出 ${(u.completion_tokens || 0).toLocaleString()} · ` +
      `合计 ${(u.total_tokens || 0).toLocaleString()} tokens` + (u.iterations ? ` · 模型调用 ${u.iterations} 轮` : '');
    return {html: `<span title="${esc_(tip)}">${parts.map(esc_).join(' · ') || esc_(tip)}</span>`, el: u.elapsed_seconds || 0};
  };

  function stepItems(box) {
    return Array.from(box.querySelectorAll('.ti')).filter(t => !t.classList.contains('steer-ti') && !t.classList.contains('ti-prep'));
  }

  function liveBubble() {
    if (!window.streaming) return null;
    const all = document.querySelectorAll('#msgs .msg.ai > .mb');
    return all.length ? all[all.length - 1] : null;
  }

  function fold(mb) {
    const blocks = Array.from(mb.querySelectorAll(PROC));
    let box = mb.querySelector(':scope > .lc-steps');
    if (!blocks.length && !box) return;
    if (!box) {
      box = document.createElement('div');
      box.className = 'lc-steps';
      box.innerHTML = '<button type="button" class="lc-steps-h"><span class="arr">▸</span><span class="lbl"></span></button><div class="lc-steps-b"></div>';
      box.firstChild.addEventListener('click', () => box.classList.toggle('open'));
      mb.insertBefore(box, mb.firstChild);
    } else if (mb.firstChild !== box && blocks.length) {
      mb.insertBefore(box, mb.firstChild);
    }
    const body = box.lastChild;
    blocks.forEach(b => {
      if (b.classList.contains('tgb')) {          // 老的「≥3 个工具」折叠壳: 拆掉, 免得折两层
        const tg = b.querySelector('.tg');
        if (tg) body.appendChild(tg);
        b.remove();
      } else body.appendChild(b);
    });
    label(mb, box);
  }

  function label(mb, box) {
    const items = stepItems(box);
    const n = items.length;
    const live = mb === liveBubble();
    let txt;
    if (live) {
      if (!mb.dataset.t0) mb.dataset.t0 = String(Date.now());
      const sec = (Date.now() - (+mb.dataset.t0)) / 1000;
      const last = items[items.length - 1];
      const cur = last ? (last.querySelector('.tn')?.textContent || last.textContent || '').trim() : '';
      const think = box.querySelector('.rsb.live');
      txt = '⏳ ' + (cur ? `正在执行 ${cur}` : (think ? '正在思考' : '处理中')) + (n ? ` · 第 ${n} 步` : '') + ` · 已用 ${fmtSec(sec)}`;
    } else {
      const ub = mb.querySelector('.ub');
      const el = ub ? +ub.dataset.el || 0 : 0;
      if (n) txt = `已执行 ${n} 步` + (el ? ` · 用时 ${fmtSec(el)}` : '');
      else {
        const rz = box.querySelector('.rsc');
        const len = rz ? (rz._raw || rz.textContent || '').length : 0;
        txt = '已思考' + (len ? ` · ${len} 字` : '') + (el ? ` · 用时 ${fmtSec(el)}` : '');
      }
    }
    box.classList.toggle('live', live);
    const lbl = box.querySelector('.lbl');
    if (lbl.textContent !== txt) lbl.textContent = txt;
  }

  let queued = false;
  function pass() {
    queued = false;
    document.querySelectorAll('#msgs .msg.ai > .mb').forEach(mb => {
      const box = mb.querySelector(':scope > .lc-steps');
      if (mb.querySelector(PROC) || (box && (mb === liveBubble() || box.classList.contains('live')))) fold(mb);
    });
  }
  function schedule() { if (!queued) { queued = true; requestAnimationFrame(pass); } }

  function start() {
    const msgs = document.getElementById('msgs');
    if (!msgs) return;
    new MutationObserver(schedule).observe(msgs, {childList: true, subtree: true});
    // 运行中每秒刷新「已用 Ns」; 轮次结束后把最后一条的头部从「进行中」改成「已执行」
    let wasLive = false;
    setInterval(() => {
      const mb = liveBubble();
      if (mb) { const box = mb.querySelector(':scope > .lc-steps'); if (box) label(mb, box); }
      if (wasLive && !window.streaming) schedule();
      wasLive = !!window.streaming;
    }, 1000);
    schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
