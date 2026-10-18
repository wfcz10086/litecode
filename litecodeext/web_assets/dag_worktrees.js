/* dag_worktrees.js — DAG Build 详情里的「工作树」小节 [dag-wt 2026-10-18].
 *
 * saved DAG 并发写步跑在独立 git 工作树 (lib/agent/subagent_worktree.py), 运行记录 job.worktrees
 * 里有每步的分支与合并状态。本模块在 Build 详情 (dag_build_view.js 全屏 / 浮窗) 里渲染:
 *   每步: 步骤 id · 分支 · 合并状态 (已合并 / 合并冲突 / 未合并…) · 冲突文件
 *   未合并的: 「查看差异」(只读 diff, 后端已截断) + 「丢弃」(两步确认, 内嵌按钮, 不用弹窗)
 * 接口: routers/dag_worktree_router.py  GET /api/dags/jobs/{id}/worktrees · .../{wid}/diff · POST .../{wid}/discard
 *
 * 用法: const s = window.DAGWorktrees.create(jobId); host.appendChild(s.el); s.update(job);
 *   job.worktrees 为空时小节隐藏; 行集合/状态变化时才重新拉接口 (轮询期间不重复请求)。
 */
(function () {
  'use strict';

  const ARM_MS = 5000;   /* 丢弃按钮第一次点击后保持"确认"状态的时长 */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function _headers(json) {
    const h = json ? { 'Content-Type': 'application/json' } : {};
    let t = '';
    try { t = (window.getWebToken && window.getWebToken()) || localStorage.getItem('litecode_web_token') || ''; } catch (_e) { /* 隐私模式 */ }
    if (t) h.Authorization = 'Bearer ' + t;
    return h;
  }

  async function _api(path, opt) {
    const r = await fetch(path, Object.assign({ credentials: 'same-origin' }, opt || {}));
    let d = null;
    try { d = await r.json(); } catch (_e) { d = null; }
    if (!r.ok) {
      const msg = (d && (d.detail || d.error)) || ('HTTP ' + r.status);
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return d || {};
  }

  function _css() {
    if (document.getElementById('dag-wt-css')) return;
    const s = document.createElement('style');
    s.id = 'dag-wt-css';
    s.textContent = [
      '.dag-wt{flex:0 0 auto;padding:10px 14px;background:var(--sf2,#f8fafc);border-bottom:1px solid var(--bd,#e2e8f0);',
      ' border-top:1px solid var(--bd,#e2e8f0);max-height:46vh;overflow:auto;font-size:12px;color:var(--tx,#1e293b)}',
      '.dag-wt-title{font-weight:600;color:var(--tx2,#475569);margin-bottom:6px;display:flex;gap:8px;align-items:center}',
      '.dag-wt-title .dag-wt-badge{font-weight:500;font-size:11px;padding:1px 7px;border-radius:9px;background:rgba(239,68,68,.12);color:#b91c1c}',
      '.dag-wt-row{padding:7px 9px;margin:4px 0;border:1px solid var(--bd,#e2e8f0);border-radius:8px;background:var(--bg,#fff)}',
      '.dag-wt-line{display:flex;align-items:center;gap:8px;flex-wrap:wrap}',
      '.dag-wt-step{font-weight:600}',
      '.dag-wt-branch{font-family:var(--mono,monospace);font-size:11px;color:var(--tx3,#64748b);word-break:break-all}',
      '.dag-wt-status{font-size:11px;padding:1px 8px;border-radius:9px;background:rgba(148,163,184,.18);color:var(--tx2,#475569)}',
      '.dag-wt-status[data-status="merged"]{background:rgba(16,185,129,.14);color:#047857}',
      '.dag-wt-status[data-status="conflict"]{background:rgba(239,68,68,.14);color:#b91c1c}',
      '.dag-wt-status[data-status="discarded"],.dag-wt-status[data-status="no_changes"]{background:rgba(148,163,184,.14);color:var(--tx3,#64748b)}',
      '.dag-wt-actions{margin-left:auto;display:flex;gap:6px}',
      '.dag-wt-actions button{font-size:11px;padding:2px 10px;border-radius:6px;border:1px solid var(--bd2,#cbd5e1);background:var(--bg,#fff);color:var(--tx,#1e293b);cursor:pointer}',
      '.dag-wt-actions button:disabled{opacity:.55;cursor:default}',
      '.dag-wt-discard.armed{background:#ef4444;border-color:#ef4444;color:#fff}',
      '.dag-wt-conflicts{margin-top:4px;font-size:11px;color:#b91c1c;font-family:var(--mono,monospace);word-break:break-all}',
      '.dag-wt-note{margin-top:3px;font-size:11px;color:var(--tx3,#64748b)}',
      '.dag-wt-msg{margin-top:4px;font-size:11px;color:#b91c1c}',
      '.dag-wt-msg.ok{color:#047857}',
      '.dag-wt-diff-body{display:none;margin:6px 0 0;padding:8px 10px;background:#0f172a;color:#e2e8f0;font-family:var(--mono,monospace);',
      ' font-size:11px;border-radius:6px;max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word}',
      '.dag-wt-row.diff-open .dag-wt-diff-body{display:block}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function _sig(job) {
    return (job.worktrees || []).map(w => (w.id || '') + ':' + (w.status || '')).join('|');
  }

  window.DAGWorktrees = {
    create(jobId) {
      _css();
      const el = document.createElement('div');
      el.className = 'dag-wt';
      el.hidden = true;
      el.dataset.jobId = jobId || '';
      let lastSig = null;
      let rows = [];

      function base() { return '/api/dags/jobs/' + encodeURIComponent(jobId) + '/worktrees'; }

      function rowHTML(w) {
        const conf = (w.conflicts || []).length
          ? `<div class="dag-wt-conflicts">冲突文件: ${(w.conflicts || []).map(esc).join(', ')}</div>` : '';
        const acts = w.unmerged
          ? `<span class="dag-wt-actions">
               <button type="button" class="dag-wt-diff">查看差异</button>
               ${w.can_discard ? '<button type="button" class="dag-wt-discard">丢弃</button>' +
                 '<button type="button" class="dag-wt-cancel" hidden>取消</button>' : ''}
             </span>` : '';
        return `<div class="dag-wt-line">
            <span class="dag-wt-step">${esc(w.step_id || '?')}</span>
            <span class="dag-wt-branch">${esc(w.branch || '')}</span>
            <span class="dag-wt-status" data-status="${esc(w.status || '')}">${esc(w.status_cn || w.status || '')}</span>
            ${acts}
          </div>${conf}${w.note && w.unmerged ? `<div class="dag-wt-note">${esc(String(w.note).slice(0, 200))}</div>` : ''}
          <div class="dag-wt-msg" hidden></div>
          <pre class="dag-wt-diff-body"></pre>`;
      }

      function render() {
        if (!rows.length) { el.hidden = true; el.innerHTML = ''; return; }
        const un = rows.filter(w => w.unmerged).length;
        el.hidden = false;
        el.innerHTML = `<div class="dag-wt-title">🌿 工作树 (${rows.length})` +
          (un ? ` <span class="dag-wt-badge">未合并 ${un}</span>` : '') + '</div>';
        rows.forEach(w => {
          const r = document.createElement('div');
          r.className = 'dag-wt-row';
          r.dataset.wid = w.id || '';
          r.dataset.status = w.status || '';
          r.innerHTML = rowHTML(w);
          el.appendChild(r);
        });
      }

      async function refresh() {
        try {
          const d = await _api(base(), { headers: _headers(false) });
          rows = d.worktrees || [];
          render();
        } catch (e) {
          el.hidden = false;
          el.innerHTML = `<div class="dag-wt-title">🌿 工作树</div><div class="dag-wt-msg">加载失败: ${esc(e.message)}</div>`;
        }
      }

      function msg(row, text, ok) {
        const m = row.querySelector('.dag-wt-msg');
        if (!m) return;
        m.hidden = !text;
        m.textContent = text || '';
        m.classList.toggle('ok', !!ok);
      }

      function disarm(row) {
        const b = row.querySelector('.dag-wt-discard');
        const c = row.querySelector('.dag-wt-cancel');
        if (b) { b.classList.remove('armed'); b.textContent = '丢弃'; }
        if (c) c.hidden = true;
        clearTimeout(row._armTimer);
      }

      el.addEventListener('click', async e => {
        const btn = e.target.closest('button');
        const row = e.target.closest('.dag-wt-row');
        if (!btn || !row) return;
        const wid = row.dataset.wid;
        if (btn.classList.contains('dag-wt-diff')) {
          const pre = row.querySelector('.dag-wt-diff-body');
          if (row.classList.contains('diff-open')) {
            row.classList.remove('diff-open'); btn.textContent = '查看差异'; return;
          }
          btn.disabled = true; btn.textContent = '加载中…';
          try {
            const d = await _api(base() + '/' + encodeURIComponent(wid) + '/diff', { headers: _headers(false) });
            pre.textContent = d.diff || '(无差异)';   /* textContent: 只读展示, 不解析 HTML */
            row.classList.add('diff-open'); btn.textContent = '收起差异'; msg(row, '');
          } catch (err) {
            btn.textContent = '查看差异'; msg(row, '读取差异失败: ' + err.message);
          } finally { btn.disabled = false; }
          return;
        }
        if (btn.classList.contains('dag-wt-cancel')) { disarm(row); return; }
        if (btn.classList.contains('dag-wt-discard')) {
          if (!btn.classList.contains('armed')) {   /* 第一步: 变成"确认丢弃", 5s 内再点才执行 */
            btn.classList.add('armed'); btn.textContent = '确认丢弃?';
            const c = row.querySelector('.dag-wt-cancel'); if (c) c.hidden = false;
            msg(row, '将删除分支与工作树, 改动不可恢复; 再点一次「确认丢弃?」执行');
            clearTimeout(row._armTimer);
            row._armTimer = setTimeout(() => { disarm(row); msg(row, ''); }, ARM_MS);
            return;
          }
          disarm(row);
          btn.disabled = true; btn.textContent = '丢弃中…';
          try {
            const d = await _api(base() + '/' + encodeURIComponent(wid) + '/discard', {
              method: 'POST', headers: _headers(true), body: JSON.stringify({ confirm: wid }) });
            msg(row, d.ok ? '已丢弃' : (d.message || '未丢弃'), d.ok);
            lastSig = null;
            await refresh();
          } catch (err) {
            btn.disabled = false; btn.textContent = '丢弃'; msg(row, '丢弃失败: ' + err.message);
          }
        }
      });

      return {
        el,
        /* 每次轮询把 job 详情传进来; 只在工作树行集合/状态变化时拉接口 */
        update(job) {
          if (!job || !(job.worktrees || []).length) {
            if (!rows.length) el.hidden = true;
            return;
          }
          const sig = _sig(job);
          if (sig === lastSig) return;
          lastSig = sig;
          refresh();
        },
        refresh,
      };
    },
  };
})();
