/* dag_build_view.js — Jenkins Blue Ocean 风 DAG Build 详情面板 (M1).
 *
 * 独立模块, 由 app.js 的 runDAG / _pollDAGJob 调用. 心智 = Jenkins:
 *   Pipeline (DAG) · Build (job) · Stage (agent).
 *
 * 提供全局 window.createDAGBuildView({jobId, dagName, stepCount})
 * 返回 { el, update(job), mountArtifacts(list), close() }.
 */
(function () {
  'use strict';

  const STATUS = {
    idle:        { icon: '○', color: '#94a3b8', label: '待运行' },
    queued:      { icon: '⧗', color: '#3b82f6', label: '排队' },
    running:     { icon: '▶', color: '#3b82f6', label: '运行中' },
    success:     { icon: '✓', color: '#10b981', label: '完成' },
    done:        { icon: '✓', color: '#10b981', label: '完成' },
    failed:      { icon: '✗', color: '#ef4444', label: '失败' },
    error:       { icon: '✗', color: '#ef4444', label: '失败' },
    aborted:     { icon: '⊘', color: '#f59e0b', label: '已中止' },
    interrupted: { icon: '⚠', color: '#f59e0b', label: '已中断' },
    skipped:     { icon: '⇒', color: '#94a3b8', label: '跳过' },
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function fmtSize(n) {
    if (!n) return '';
    if (n < 1024) return n + 'B';
    if (n < 1048576) return (n / 1024).toFixed(1) + 'K';
    return (n / 1048576).toFixed(1) + 'M';
  }

  function artIcon(name, kind) {
    if (kind === 'url') return '🔗';
    const ext = (String(name).split('.').pop() || '').toLowerCase();
    if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return '🖼';
    if (ext === 'pdf') return '📄';
    if (['pptx', 'ppt'].includes(ext)) return '📊';
    if (['xlsx', 'xls', 'csv'].includes(ext)) return '📈';
    if (['docx', 'doc', 'md', 'txt'].includes(ext)) return '📝';
    if (['dxf', 'dwg', 'stl', 'obj'].includes(ext)) return '🔧';
    if (['zip', 'tar', 'gz'].includes(ext)) return '🗜';
    return '📎';
  }

  function _judgeStatus(a) {
    if (a.success) return 'success';
    if (a.error) return 'failed';
    return a.status || 'running';
  }

  function _stageId(a, i) {
    return a.step_id || a.label || a.agent_type || ('s' + i);
  }

  function _mkHead(dagName, jobId, stepCount) {
    const head = document.createElement('div');
    head.className = 'build-head';
    head.style.cssText =
      'padding:10px 14px; background:var(--sf2,#f8fafc); border-bottom:1px solid var(--bd,#e2e8f0);' +
      ' display:flex; align-items:center; gap:10px;';
    head.innerHTML =
      `<span class="build-icon" style="font-size:16px; line-height:1">⏳</span>
       <div style="flex:1; min-width:0">
         <div style="font-size:13px; font-weight:600; color:var(--tx,#1e293b);
                     white-space:nowrap; overflow:hidden; text-overflow:ellipsis">
           ${esc(dagName)} · Build <span class="build-id">#${esc(jobId.slice(0, 6))}</span>
         </div>
         <div class="build-progress" style="font-size:11px; color:var(--tx3,#64748b); margin-top:2px; font-family:var(--mono,monospace)">
           0/${stepCount} · 0s
         </div>
       </div>
       <button class="build-close abtn sec" style="font-size:11px; padding:3px 10px">关闭</button>`;
    return head;
  }

  function _mkStageRow(sid) {
    const row = document.createElement('div');
    row.className = 'build-stage';
    row.dataset.sid = sid;
    row.style.cssText = 'padding:6px 10px; margin:2px 4px; border-radius:6px; cursor:pointer; user-select:none;';
    row.innerHTML =
      `<div class="build-stage-head" style="display:flex; align-items:center; gap:8px; font-size:12px">
         <span class="build-stage-icon" style="font-family:var(--mono,monospace); font-size:14px; width:14px; text-align:center"></span>
         <span class="build-stage-name" style="flex:1; color:var(--tx,#1e293b);
                     white-space:nowrap; overflow:hidden; text-overflow:ellipsis"></span>
         <span class="build-stage-meta" style="font-size:11px; color:var(--tx3,#64748b); font-family:var(--mono,monospace)"></span>
       </div>
       <pre class="build-stage-body" style="display:none; margin:6px 0 0; padding:8px 10px;
             background:#0f172a; color:#e2e8f0; font-family:var(--mono,monospace); font-size:11px;
             border-radius:5px; max-height:280px; overflow:auto;
             white-space:pre-wrap; word-break:break-word;"></pre>`;
    return row;
  }

  function _updateStageRow(row, a, status) {
    const cfg = STATUS[status] || STATUS.running;
    row.querySelector('.build-stage-icon').textContent = cfg.icon;
    row.querySelector('.build-stage-icon').style.color = cfg.color;
    const label = a.label || a.step_id || '';
    const agentTy = a.agent_type ? ' · ' + a.agent_type : '';
    row.querySelector('.build-stage-name').textContent = label + agentTy;
    const meta = (a.elapsed ? a.elapsed.toFixed(1) + 's' : '') + (a.error ? ' · ✗' : '');
    row.querySelector('.build-stage-meta').textContent = meta;
    row.style.background = status === 'running' ? 'rgba(59,130,246,.08)'
                        : status === 'failed'  ? 'rgba(239,68,68,.06)' : 'transparent';
    const body = row.querySelector('.build-stage-body');
    const tag = cfg.label;
    body.textContent =
      `[${tag} · ${(a.elapsed || 0).toFixed(1)}s]\n\n` +
      (a.output_preview || a.error || '(暂无输出)');
  }

  function _mkArtifactCard(a) {
    const href = a.kind === 'url' ? a.url : ('/download?path=' + encodeURIComponent(a.path));
    const card = document.createElement('a');
    card.href = href;
    card.target = '_blank';
    card.rel = 'noopener';
    card.style.cssText =
      'flex:0 0 auto; min-width:120px; max-width:220px; padding:6px 8px;' +
      ' background:var(--bg,#fff); border:1px solid var(--bd2,#cbd5e1); border-radius:6px;' +
      ' text-decoration:none; color:var(--tx,#1e293b); font-size:11px;' +
      ' display:flex; flex-direction:column; gap:2px;';
    card.innerHTML =
      `<div style="font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis">
         ${artIcon(a.name, a.kind)} ${esc(a.name)}
       </div>
       <div style="color:var(--tx3,#64748b); font-family:var(--mono,monospace); font-size:10px">
         ${esc(a.stage || '')}${a.size ? ' · ' + fmtSize(a.size) : ''}
       </div>`;
    return card;
  }

  window.createDAGBuildView = function ({ jobId, dagName, stepCount }) {
    const wrap = document.createElement('div');
    wrap.className = 'dag-build-view';
    wrap.style.cssText =
      'position:fixed; right:12px; bottom:12px; width:min(560px,92vw); max-height:78vh;' +
      ' background:var(--sf,#fff); border:1px solid var(--bd,#e2e8f0); border-radius:10px;' +
      ' box-shadow:0 8px 32px rgba(0,0,0,.14); z-index:9500;' +
      ' display:flex; flex-direction:column; overflow:hidden; font-family:var(--sans,system-ui);';

    const head = _mkHead(dagName, jobId, stepCount);
    wrap.appendChild(head);

    const stages = document.createElement('div');
    stages.className = 'build-stages';
    stages.style.cssText = 'flex:1; overflow:auto; padding:6px 4px;';
    wrap.appendChild(stages);

    const artifacts = document.createElement('div');
    artifacts.className = 'build-artifacts';
    artifacts.style.cssText =
      'display:none; padding:10px 14px; background:var(--sf2,#f8fafc);' +
      ' border-top:1px solid var(--bd,#e2e8f0); max-height:180px; overflow:hidden;';
    wrap.appendChild(artifacts);

    head.querySelector('.build-close').addEventListener('click', () => wrap.remove());
    stages.addEventListener('click', e => {
      const row = e.target.closest('.build-stage');
      if (!row) return;
      const open = row.classList.toggle('open');
      const body = row.querySelector('.build-stage-body');
      if (body) body.style.display = open ? 'block' : 'none';
    });

    function update(job) {
      const icon = wrap.querySelector('.build-icon');
      const prog = wrap.querySelector('.build-progress');
      const agents = job.agents || [];
      const done = agents.filter(a => a.success || a.error).length;
      const tot = job.step_count || agents.length || stepCount || 1;
      const st = job.status || 'running';
      const elapsed = job.elapsed || Math.max(0, Date.now() / 1000 - (job.started || Date.now() / 1000));
      const cfg = STATUS[st] || STATUS.running;

      icon.textContent = cfg.icon;
      icon.style.color = cfg.color;
      prog.textContent = `${done}/${tot} · ${Math.round(elapsed)}s${st === 'running' ? ' · 运行中' : ' · ' + cfg.label}`;

      const existing = Object.create(null);
      stages.querySelectorAll('.build-stage').forEach(r => { existing[r.dataset.sid] = r; });

      agents.forEach((a, i) => {
        const sid = _stageId(a, i);
        let row = existing[sid];
        if (!row) {
          row = _mkStageRow(sid);
          stages.appendChild(row);
        } else {
          delete existing[sid];
        }
        _updateStageRow(row, a, _judgeStatus(a));
      });
      Object.values(existing).forEach(r => r.remove());
    }

    function mountArtifacts(list) {
      if (!list || !list.length) {
        artifacts.style.display = 'none';
        return;
      }
      artifacts.style.display = 'block';
      artifacts.innerHTML =
        `<div style="font-size:11px; color:var(--tx2,#475569); margin-bottom:6px; font-weight:600">
          📦 产物 · Artifacts (${list.length})
        </div>`;
      const scroll = document.createElement('div');
      scroll.style.cssText = 'display:flex; gap:6px; overflow-x:auto; padding-bottom:4px; white-space:nowrap;';
      list.forEach(a => scroll.appendChild(_mkArtifactCard(a)));
      artifacts.appendChild(scroll);
    }

    function close() { wrap.remove(); }

    return { el: wrap, update, mountArtifacts, close };
  };

  /* ── 全屏版 (屏 C · Jenkins Blue Ocean 心智) ──────────────────────────
   * 供 window.DAG.showBuild → mountDAGBuildFullscreen(container, jobId, name).
   * 与 createDAGBuildView 复用 update/mountArtifacts, 但铺满 container + 自带 polling.
   */
  function _authHeaders() {
    const t = (window.getWebToken && window.getWebToken()) || localStorage.getItem('litecode_web_token') || '';
    return t ? { Authorization: 'Bearer ' + t } : {};
  }

  window.mountDAGBuildFullscreen = function (container, jobId, dagName) {
    if (!container) return null;
    container.innerHTML = '';
    /* 只设布局属性 — 不动 display, 让 .dag-screen.on 的 CSS 全权控制显隐,
     * 否则 inline display:flex 会永久覆盖 CSS, 切屏后 build 屏挥之不去截击点击 */
    container.style.flexDirection = 'column';
    container.style.height = '100%';
    container.style.background = 'var(--bg, #fff)';

    /* Top: 大 header — 全屏专属, 显示 Pipeline + Build# + 全局状态 */
    const top = document.createElement('div');
    top.style.cssText =
      'padding:16px 24px; background:var(--sf2,#f8fafc); border-bottom:1px solid var(--bd,#e2e8f0);' +
      ' display:flex; align-items:center; gap:14px; flex:0 0 auto;';
    top.innerHTML =
      `<span class="fs-build-icon" style="font-size:26px; line-height:1">⏳</span>
       <div style="flex:1; min-width:0">
         <div style="font-size:16px; font-weight:700; color:var(--tx,#1e293b);
                     white-space:nowrap; overflow:hidden; text-overflow:ellipsis">
           ${esc(dagName)} <span style="color:var(--tx3,#94a3b8); font-weight:400">·</span>
           Build <span class="fs-build-id" style="font-family:var(--mono,monospace)">#${esc(jobId.slice(0, 6))}</span>
         </div>
         <div class="fs-build-progress" style="font-size:12px; color:var(--tx3,#64748b);
                     margin-top:3px; font-family:var(--mono,monospace)">
           排队中…
         </div>
       </div>
       <button class="fs-build-abort abtn sec" style="font-size:12px; padding:5px 14px">⊘ 中止</button>`;
    container.appendChild(top);

    /* Body: 左 stages / 右 artifacts+log */
    const body = document.createElement('div');
    body.style.cssText = 'flex:1; display:flex; overflow:hidden; min-height:0;';
    container.appendChild(body);

    const stagesCol = document.createElement('div');
    stagesCol.style.cssText =
      'flex:0 0 42%; max-width:520px; min-width:280px; overflow:auto;' +
      ' border-right:1px solid var(--bd,#e2e8f0); padding:10px 6px; background:var(--sf,#fff);';
    body.appendChild(stagesCol);

    const rightCol = document.createElement('div');
    rightCol.style.cssText = 'flex:1; display:flex; flex-direction:column; overflow:hidden; min-width:0;';
    body.appendChild(rightCol);

    const artifactsBox = document.createElement('div');
    artifactsBox.style.cssText =
      'flex:0 0 auto; padding:12px 18px; background:var(--sf2,#f8fafc);' +
      ' border-bottom:1px solid var(--bd,#e2e8f0); display:none; max-height:200px; overflow:hidden;';
    rightCol.appendChild(artifactsBox);

    const logBox = document.createElement('pre');
    logBox.style.cssText =
      'flex:1; margin:0; padding:14px 18px; background:#0f172a; color:#e2e8f0;' +
      ' font-family:var(--mono,monospace); font-size:12px; line-height:1.55;' +
      ' overflow:auto; white-space:pre-wrap; word-break:break-word;';
    logBox.textContent = '[启动中] 等待 Job 状态…\n';
    rightCol.appendChild(logBox);

    /* 用户手动点某个 stage 后就把 log 交给他, 别再自动 overwrite */
    let logPinned = false;

    /* 一次性注入 .build-stage.sel 高亮样式 */
    if (!document.getElementById('fs-build-stage-css')) {
      const s = document.createElement('style');
      s.id = 'fs-build-stage-css';
      s.textContent =
        '.build-stage.sel{background:rgba(59,130,246,.14)!important;'
        + 'box-shadow:inset 3px 0 0 var(--ac,#3b82f6);}';
      document.head.appendChild(s);
    }

    /* 事件委托 — 点 stage 行, 该 stage 的输出 dump 到右侧 log (pinned = 不再自动 overwrite) */
    stagesCol.addEventListener('click', e => {
      const row = e.target.closest('.build-stage');
      if (!row) return;
      stagesCol.querySelectorAll('.build-stage').forEach(r => r.classList.remove('sel'));
      row.classList.add('sel');
      const body_ = row.querySelector('.build-stage-body');
      logBox.textContent = body_ ? body_.textContent : '(无输出)';
      logBox.scrollTop = 0;
      logPinned = true;
    });

    top.querySelector('.fs-build-abort').addEventListener('click', async () => {
      if (!confirm) return; /* stub — 正式禁 confirm, 走小卡 */
      await fetch('/api/dags/jobs/' + encodeURIComponent(jobId) + '/abort',
        { method: 'POST', headers: _authHeaders() }).catch(() => {});
    });

    let timer = null;
    let stopped = false;
    let lastJob = null;

    function _buildLogSummary(job) {
      const agents = job.agents || [];
      const st = job.status || 'running';
      const cfg = STATUS[st] || STATUS.running;
      const done = agents.filter(a => a.success || a.error).length;
      const tot = job.step_count || agents.length || 1;
      const el = Math.round(job.elapsed || 0);
      const lines = [];
      lines.push('[' + cfg.label + '] Build #' + jobId.slice(0, 6)
        + ' · ' + done + '/' + tot + ' · ' + el + 's');
      lines.push('DAG: ' + (dagName || '(unknown)'));
      lines.push('');
      lines.push('─── Stages ───');
      agents.forEach((a, i) => {
        const s = _judgeStatus(a);
        const sc = STATUS[s] || STATUS.running;
        const label = a.label || a.step_id || ('s' + i);
        const at = a.agent_type ? ' · ' + a.agent_type : '';
        const t = a.elapsed != null ? ' · ' + a.elapsed.toFixed(1) + 's' : '';
        lines.push(sc.icon + '  ' + label + at + t);
        if (a.error) lines.push('    ✗ ' + String(a.error).split('\n')[0].slice(0, 200));
      });
      if (job.error) {
        lines.push('');
        lines.push('─── Job Error ───');
        lines.push(String(job.error).slice(0, 800));
      }
      lines.push('');
      lines.push('👉 点击左侧 stage 查看该步骤输出.');
      return lines.join('\n');
    }

    function refreshUI(job) {
      lastJob = job;
      const icon = top.querySelector('.fs-build-icon');
      const prog = top.querySelector('.fs-build-progress');
      const agents = job.agents || [];
      const done = agents.filter(a => a.success || a.error).length;
      const tot = job.step_count || agents.length || 1;
      const st = job.status || 'running';
      const elapsed = job.elapsed || 0;
      const cfg = STATUS[st] || STATUS.running;
      icon.textContent = cfg.icon;
      icon.style.color = cfg.color;
      prog.textContent = `${done}/${tot} · ${Math.round(elapsed)}s · ${cfg.label}`;

      /* 重建 stages (复用 _mkStageRow / _updateStageRow) */
      const existing = Object.create(null);
      stagesCol.querySelectorAll('.build-stage').forEach(r => { existing[r.dataset.sid] = r; });
      agents.forEach((a, i) => {
        const sid = _stageId(a, i);
        let row = existing[sid];
        if (!row) {
          row = _mkStageRow(sid);
          row.style.padding = '10px 12px';
          row.style.margin = '4px 6px';
          stagesCol.appendChild(row);
        } else {
          delete existing[sid];
        }
        _updateStageRow(row, a, _judgeStatus(a));
      });
      Object.values(existing).forEach(r => r.remove());

      /* 用户没手动 pin, 就用汇总替换 [启动中] 文案 */
      if (!logPinned) {
        logBox.textContent = _buildLogSummary(job);
      }
    }

    function refreshArtifacts(list) {
      if (!list || !list.length) { artifactsBox.style.display = 'none'; return; }
      artifactsBox.style.display = 'block';
      artifactsBox.innerHTML =
        `<div style="font-size:12px; color:var(--tx2,#475569); margin-bottom:8px; font-weight:600">
          📦 产物 · Artifacts (${list.length})
        </div>`;
      const scroll = document.createElement('div');
      scroll.style.cssText = 'display:flex; gap:8px; overflow-x:auto; padding-bottom:6px;';
      list.forEach(a => scroll.appendChild(_mkArtifactCard(a)));
      artifactsBox.appendChild(scroll);
    }

    async function poll() {
      if (stopped) return;
      try {
        const r = await fetch('/api/dags/jobs/' + encodeURIComponent(jobId),
          { headers: _authHeaders() });
        if (r.ok) {
          const j = await r.json();
          refreshUI(j);
          const st = j.status || '';
          if (st === 'done' || st === 'failed' || st === 'aborted') {
            stopped = true;
            clearInterval(timer); timer = null;
            /* 拉产物 */
            fetch('/api/dags/jobs/' + encodeURIComponent(jobId) + '/artifacts',
              { headers: _authHeaders() })
              .then(r2 => r2.json())
              .then(a => refreshArtifacts(a.artifacts || []))
              .catch(() => {});
          }
        }
      } catch (_e) { /* 忽略瞬时错 */ }
    }

    poll();
    timer = setInterval(poll, 3000);

    function close() {
      stopped = true;
      if (timer) { clearInterval(timer); timer = null; }
    }

    /* 挂到全局, dag_ui.close() 用得着 */
    window._dagBuildInline = { close, jobId };
    return { close, refresh: () => lastJob && refreshUI(lastJob) };
  };
})();
