/* dag_editor_simple.js — 屏 B · 极简画布 (Jenkins Pipeline 编辑).
 *
 * 与老 dag_drawflow.js 相比砍掉:
 *   - 右侧属性面板 (改抽屉 overlay)
 *   - Dry-run / 断点 / 高级 retry 策略 (defer)
 *   - 底部 result 区 (Build 详情走独立屏 C)
 *   - inline onclick / prompt / confirm (全走事件委托 + card)
 *
 * 顶部只 3 按钮: ▶ Build / 💾 Save / 🤖 AI
 * 依赖 CDN drawflow@0.0.59 (web_ui.html 已引入).
 * 挂载: window.mountDAGEditor(container, name).
 */
(function () {
  'use strict';

  const AGENTS = ['coder', 'explorer', 'researcher', 'analyst', 'tester', 'shell', 'writer', 'critic', 'vision'];
  const AGENT_COLOR = {
    coder: '#4f46e5', explorer: '#0891b2', researcher: '#7c3aed', analyst: '#d97706',
    tester: '#16a34a', shell: '#dc2626', writer: '#db2777', critic: '#9333ea', vision: '#0ea5e9', tool: '#0f766e',
  };
  const TOOLS = [
    'pptx_render', 'cad_generate', 'image_gen', 'html_render',
    'ssh_exec', 'web_search', 'web_fetch', 'execute_shell', 'docker_ps',
    'vision_ocr',
  ];
  const PRESET_DAGS = {
    daily_ops: {
      steps: [
        { id: 'disk', agent_type: 'shell', task: 'df -h && du -sh /var/log/* 2>/dev/null | sort -h | tail -10' },
        { id: 'mem', agent_type: 'shell', task: 'free -h && ps -eo pid,%mem,cmd --sort=-%mem | head -10' },
        { id: 'net', agent_type: 'shell', task: 'ss -tln && netstat -i 2>/dev/null || ip -s link' },
        { id: 'report', agent_type: 'writer', task: '汇总磁盘 / 内存 / 网络三项检查, 输出巡检日报.', depends_on: ['disk', 'mem', 'net'] },
      ],
    },
    host_health: {
      steps: [
        { id: 'sys', agent_type: 'shell', task: 'uptime && free -h && df -h' },
        { id: 'diag', agent_type: 'analyst', task: '分析系统数据: 负载/内存/磁盘. 标出异常项.', depends_on: ['sys'] },
        { id: 'report', agent_type: 'writer', task: '生成 Markdown 巡检报告.', depends_on: ['diag'] },
      ],
    },
    web_summary: {
      steps: [
        { id: 'search', agent_type: 'researcher', task: '搜索关键词 $QUERY 最近相关网页, 收集 top 5 URL.' },
        { id: 'fetch', agent_type: 'tool', tool_name: 'web_search', task: '抓取 URL 正文.', depends_on: ['search'] },
        { id: 'digest', agent_type: 'analyst', task: '提炼共识 vs 分歧, 输出 500 字摘要.', depends_on: ['fetch'] },
      ],
    },
    illustrated_post: {
      steps: [
        { id: 'outline', agent_type: 'writer', task: '围绕 $TOPIC 写 300 字短文.' },
        { id: 'prompt', agent_type: 'analyst', task: '抽成英文 image prompt.', depends_on: ['outline'] },
        { id: 'cover', agent_type: 'tool', tool_name: 'image_gen', task: '按 prompt 生成封面.', depends_on: ['prompt'] },
        { id: 'assemble', agent_type: 'writer', task: '合成 Markdown 图文笔记.', depends_on: ['outline', 'cover'] },
      ],
    },
  };

  /* 运行条件 when — 可视化条件行. 生成 v2 字符串, 顶层数组 = AND (见 core/_when_eval.py). */
  const WHEN_OPS = [
    { v: 'success',  label: '成功 (success)',   needsVal: false },
    { v: 'failure',  label: '失败 (failure)',   needsVal: false },
    { v: 'contains', label: '输出含 (contains)', needsVal: true },
    { v: 'eq',       label: '输出等于 (eq)',     needsVal: true },
    { v: 'gt',       label: '输出 > (gt)',       needsVal: true },
    { v: 'lt',       label: '输出 < (lt)',       needsVal: true },
  ];
  function _opNeedsVal(op) { return !(op === 'success' || op === 'failure'); }

  const state = { df: null, name: '', dirty: false, container: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  async function api(m, u, body) {
    const r = await fetch(u, {
      method: m,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((j.error || r.status) + '');
    return j;
  }

  const CSS = `
    .des-wrap{display:flex;flex-direction:column;height:100%}
    .des-top{display:flex;gap:8px;align-items:center;padding:8px 12px;background:var(--sf2,#f8fafc);border-bottom:1px solid var(--bd,#e2e8f0);flex:0 0 auto}
    .des-top input.name{flex:0 0 220px;padding:5px 10px;border:1px solid var(--bd2,#cbd5e1);border-radius:5px;font-size:12px}
    .des-top .spacer{flex:1}
    .des-btn{border:1px solid var(--bd2,#cbd5e1);background:var(--sf,#fff);color:var(--tx,#1e293b);border-radius:5px;padding:5px 12px;font-size:12px;cursor:pointer;font-weight:600}
    .des-btn.prim{background:var(--ac,#3b82f6);color:#fff;border-color:var(--ac,#3b82f6)}
    .des-btn.warn{background:#f59e0b;color:#fff;border-color:#f59e0b}
    .des-btn:hover{filter:brightness(1.05)}
    .des-body{flex:1;display:flex;overflow:hidden;min-height:0}
    .des-palette{flex:0 0 130px;padding:8px;background:var(--sf,#fff);border-right:1px solid var(--bd,#e2e8f0);overflow:auto}
    .des-pal-h{font-size:10px;color:var(--tx3,#64748b);text-transform:uppercase;font-weight:600;margin:6px 4px 4px;letter-spacing:.5px}
    .des-pal-item{display:flex;align-items:center;gap:6px;padding:4px 8px;margin:2px 0;border:1px solid var(--bd,#e2e8f0);border-radius:4px;cursor:grab;background:var(--sf2,#f8fafc);font-size:11px;user-select:none}
    .des-pal-item:hover{border-color:var(--ac,#3b82f6)}
    .des-pal-dot{width:8px;height:8px;border-radius:50%;flex:0 0 8px}
    .des-canvas-wrap{flex:1;position:relative;overflow:hidden;background:var(--bg2,#fafafa)}
    .des-canvas{width:100%;height:100%}
    .des-canvas.empty::before{content:'👉 拖左侧节点到画布 · 或点上方 🤖 让 AI 生成';position:absolute;top:40%;left:50%;transform:translate(-50%,0);color:var(--tx3,#94a3b8);font-size:14px;pointer-events:none}
    /* 屏蔽 drawflow 默认节点边框/背景 — 让 .df-simple-node 完整接管 */
    .drawflow .drawflow-node{background:transparent!important;border:0!important;padding:0!important;box-shadow:none!important;-webkit-box-shadow:none!important}
    .drawflow .drawflow-node.selected .df-simple-node{outline:2px solid var(--ac,#3b82f6);outline-offset:2px}
    .des-drawer{position:absolute;top:0;right:0;bottom:0;width:360px;background:var(--sf,#fff);border-left:1px solid var(--bd,#e2e8f0);box-shadow:-4px 0 16px rgba(0,0,0,.06);transform:translateX(100%);transition:transform .18s;z-index:20;display:flex;flex-direction:column}
    .des-drawer.on{transform:translateX(0)}
    .des-drawer-head{padding:10px 14px;border-bottom:1px solid var(--bd,#e2e8f0);display:flex;align-items:center;gap:8px;font-weight:600;flex:0 0 auto}
    .des-drawer-head .close{margin-left:auto;background:transparent;border:0;font-size:16px;cursor:pointer;color:var(--tx3,#64748b)}
    .des-drawer-body{flex:1;overflow:auto;padding:12px 14px}
    .des-fld{margin-bottom:10px}
    .des-fld label{display:block;font-size:11px;color:var(--tx3,#64748b);margin-bottom:3px;font-weight:600}
    .des-fld input,.des-fld select,.des-fld textarea{width:100%;padding:5px 8px;border:1px solid var(--bd2,#cbd5e1);border-radius:4px;font-size:12px;font-family:inherit;box-sizing:border-box}
    .des-fld textarea{min-height:70px;resize:vertical}
    .des-drawer-bot{padding:10px 14px;border-top:1px solid var(--bd,#e2e8f0);display:flex;gap:6px;justify-content:flex-end;flex:0 0 auto}
    .des-ai-card{position:absolute;top:0;left:0;right:0;background:var(--sf,#fff);border-bottom:1px solid var(--ac,#3b82f6);padding:10px 14px;box-shadow:0 2px 8px rgba(59,130,246,.1);z-index:15;display:none}
    .des-ai-card.on{display:block}
    .des-ai-card textarea{width:100%;min-height:50px;padding:6px;border:1px solid var(--bd2,#cbd5e1);border-radius:4px;font-size:12px;box-sizing:border-box}
    .des-ai-card .row{display:flex;gap:6px;margin-top:6px;justify-content:flex-end}
    .df-simple-node{padding:8px 10px;background:var(--sf,#fff);border:2px solid var(--bd2,#ccc);border-radius:6px;min-width:140px;box-shadow:0 1px 3px rgba(0,0,0,.06);cursor:pointer}
    .df-simple-node .h{font-size:11px;font-weight:600;color:var(--tx,#1e293b);display:flex;align-items:center;gap:4px}
    .df-simple-node .b{font-size:10px;color:var(--tx3,#64748b);margin-top:3px;line-height:1.3;max-height:24px;overflow:hidden}
    .df-simple-node .badge{font-size:9px;color:#fff;padding:1px 5px;border-radius:2px;text-transform:uppercase;font-weight:600}
  `;

  function ensureCSS() {
    if (document.getElementById('des-css')) return;
    const s = document.createElement('style');
    s.id = 'des-css';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function _nodeHTML(step) {
    const at = step.agent_type || 'coder';
    const color = AGENT_COLOR[at] || '#475569';
    const label = at === 'tool' ? (step.tool_name || 'tool') : at;
    const task = (step.task || '').slice(0, 60);
    const nWhen = Array.isArray(step.when) ? step.when.length : (step.when ? 1 : 0);
    const whenMark = nWhen ? ` <span title="有 ${nWhen} 个运行条件" style="font-size:9px;margin-left:auto">⚡when</span>` : '';
    return `<div class="df-simple-node" style="border-color:${color}" data-nid-marker="1">
      <div class="h"><span class="badge" style="background:${color}">${esc(label)}</span> <span>${esc(step.id || '')}</span>${whenMark}</div>
      <div class="b">${esc(task)}</div>
    </div>`;
  }

  function _pal() {
    const agents = AGENTS.map(a =>
      `<div class="des-pal-item" draggable="true" data-drop="agent:${a}">
        <span class="des-pal-dot" style="background:${AGENT_COLOR[a]}"></span>${a}</div>`).join('');
    const tools = TOOLS.map(t =>
      `<div class="des-pal-item" draggable="true" data-drop="tool:${t}">
        <span class="des-pal-dot" style="background:${AGENT_COLOR.tool}"></span>${t}</div>`).join('');
    return `
      <div class="des-pal-h">Agents</div>${agents}
      <div class="des-pal-h">Tools</div>${tools}
      <div class="des-pal-h">流程</div><div class="des-pal-item" draggable="true" data-drop="decision:branch"><span class="des-pal-dot" style="background:#eab308"></span>🔀 决策分支</div><div class="des-pal-item" draggable="true" data-drop="dag:sub"><span class="des-pal-dot" style="background:#64748b"></span>📦 子DAG</div>`;
  }

  function render(container, name) {
    ensureCSS();
    state.container = container;
    state.name = name || '';
    state.dirty = false;

    container.innerHTML = `
      <div class="des-wrap">
        <div class="des-top">
          <input class="name" id="des-name" placeholder="Pipeline 名 (a-z0-9_-)" value="${esc(state.name)}">
          <div class="spacer"></div>
          <button class="des-btn" data-act="ai">🤖 AI</button>
          <button class="des-btn" data-act="save">💾 保存</button>
          <button class="des-btn prim" data-act="build">▶ Build</button>
        </div>
        <div class="des-body">
          <div class="des-palette">${_pal()}</div>
          <div class="des-canvas-wrap">
            <div class="des-canvas" id="des-canvas"></div>
            <div class="des-drawer" id="des-drawer">
              <div class="des-drawer-head">节点属性<button class="close" data-drawer="close">×</button></div>
              <div class="des-drawer-body" id="des-drawer-body"></div>
              <div class="des-drawer-bot">
                <button class="des-btn warn" data-drawer="del">删除</button>
                <button class="des-btn prim" data-drawer="save">保存</button>
              </div>
            </div>
            <div class="des-ai-card" id="des-ai-card">
              <textarea id="des-ai-req" placeholder="用自然语言描述 pipeline / 或对当前 pipeline 说 '在最后加一步生 PPT'..."></textarea>
              <div class="row">
                <button class="des-btn" data-ai="cancel">取消</button>
                <button class="des-btn prim" data-ai="go">生成</button>
              </div>
            </div>
          </div>
        </div>
      </div>`;

    _initDrawflow();
    _bind();
    _loadContent();
  }

  function _initDrawflow() {
    const canvas = document.getElementById('des-canvas');
    if (!window.Drawflow) { canvas.innerHTML = '<div style="padding:40px;text-align:center;color:#dc2626">Drawflow 库未加载</div>'; return; }
    state.df = new window.Drawflow(canvas);
    state.df.reroute = true;
    state.df.start();
    _refreshEmpty();
    state.df.on('nodeCreated', () => { state.dirty = true; _refreshEmpty(); });
    state.df.on('nodeRemoved', () => { state.dirty = true; _refreshEmpty(); });
    state.df.on('connectionCreated', () => { state.dirty = true; });
  }

  function _refreshEmpty() {
    const canvas = document.getElementById('des-canvas');
    if (!canvas || !state.df) return;
    const has = canvas.querySelectorAll('.drawflow-node').length > 0;
    canvas.classList.toggle('empty', !has);
  }

  function _bind() {
    const top = state.container.querySelector('.des-top');
    top.addEventListener('click', e => {
      const act = e.target.getAttribute('data-act');
      if (act === 'save') _save();
      else if (act === 'build') _build();
      else if (act === 'ai') _toggleAI(true);
    });
    const nameIn = state.container.querySelector('#des-name');
    nameIn.addEventListener('input', () => { state.name = nameIn.value.trim(); state.dirty = true; });

    /* Palette drag */
    const pal = state.container.querySelector('.des-palette');
    pal.addEventListener('dragstart', e => {
      const t = e.target.closest('[data-drop]');
      if (t) { e.dataTransfer.setData('text/plain', t.getAttribute('data-drop')); }
    });
    const canvasWrap = state.container.querySelector('.des-canvas-wrap');
    canvasWrap.addEventListener('dragover', e => e.preventDefault());
    canvasWrap.addEventListener('drop', e => {
      e.preventDefault();
      const kv = e.dataTransfer.getData('text/plain');
      if (!kv) return;
      const [kind, val] = kv.split(':');
      const step = { id: 's' + Date.now().toString(36).slice(-5), task: '' };
      if (kind === 'agent') step.agent_type = val;
      else if (kind === 'tool') { step.agent_type = 'tool'; step.tool_name = val; }
        else if (kind === 'decision') { step.agent_type = 'decision'; }  // [#2-②] 决策分支
        else if (kind === 'dag') { step.agent_type = 'dag'; }  // [#2] 子 DAG 步
      const rect = canvasWrap.getBoundingClientRect();
      _addStep(step, e.clientX - rect.left, e.clientY - rect.top);
    });

    /* Node click → drawer */
    canvasWrap.addEventListener('click', e => {
      const nodeEl = e.target.closest('.drawflow-node');
      if (nodeEl) {
        const nid = parseInt(nodeEl.id.replace('node-', ''), 10);
        if (!isNaN(nid)) _openDrawer(nid);
      }
    });

    /* Drawer buttons */
    const drawer = state.container.querySelector('#des-drawer');
    drawer.addEventListener('click', e => {
      const act = e.target.getAttribute('data-drawer');
      if (act === 'close') _closeDrawer();
      else if (act === 'save') _saveDrawer();
      else if (act === 'del') _delDrawer();
      else if (act === 'when-add') _addWhenRow();
      else if (act === 'when-del') {
        const r = e.target.closest('.dr-when-row');
        if (r) { r.remove(); state.dirty = true; }
      }
    });
    /* when 算子切换 → 显隐"值"输入 (success/failure 不需要值) */
    drawer.addEventListener('change', e => {
      const opSel = e.target.closest('[data-when-op]');
      if (!opSel) return;
      const valEl = opSel.closest('.dr-when-row').querySelector('[data-when-val]');
      if (valEl) valEl.style.display = _opNeedsVal(opSel.value) ? '' : 'none';
    });

    /* AI card */
    const aiCard = state.container.querySelector('#des-ai-card');
    aiCard.addEventListener('click', async e => {
      const act = e.target.getAttribute('data-ai');
      if (act === 'cancel') _toggleAI(false);
      else if (act === 'go') await _aiGenerate();
    });
  }

  function _addStep(step, x, y) {
    if (!state.df) return null;
    const outs = step.agent_type === 'decision' ? 2 : 1;  // [#2-②] 决策 2 出口 ✓真/✗假
    const nid = state.df.addNode(step.id, 1, outs, x, y, 'df-step',
      { step: JSON.parse(JSON.stringify(step)) }, _nodeHTML(step));
    return nid;
  }

  function _loadContent() {
    /* 优先: pending AI 生成的 dag > pending preset > 已有 name → 后端 load > 空 */
    if (window.__dpl_pending_dag) {
      const d = window.__dpl_pending_dag;
      window.__dpl_pending_dag = null;
      _importDAG(d);
      state.name = d.name || '';
      state.container.querySelector('#des-name').value = state.name;
      return;
    }
    if (window.__dpl_pending_preset) {
      const key = window.__dpl_pending_preset;
      window.__dpl_pending_preset = null;
      if (key !== 'blank' && PRESET_DAGS[key]) {
        _importDAG(PRESET_DAGS[key]);
        state.name = key;
        state.container.querySelector('#des-name').value = key;
      }
      return;
    }
    if (state.name) {
      api('GET', '/api/dags/' + encodeURIComponent(state.name))
        .then(d => _importDAG(d))
        .catch(() => {});
    }
  }

  function _importDAG(dag) {
    if (!state.df) return;
    state.df.clearModuleSelected();
    state.df.import({ drawflow: { Home: { data: {} } } });
    const steps = dag.steps || [];
    const idToStep = {};
    steps.forEach(s => { idToStep[s.id] = s; });
    /* 拓扑分层 */
    const level = {};
    function lv(id) {
      if (level[id] !== undefined) return level[id];
      const deps = (idToStep[id] || {}).depends_on || [];
      level[id] = deps.length ? Math.max(...deps.map(lv)) + 1 : 0;
      return level[id];
    }
    steps.forEach(s => lv(s.id));
    const byLv = {};
    steps.forEach(s => { const l = level[s.id]; (byLv[l] = byLv[l] || []).push(s); });
    const idToNid = {};
    Object.keys(byLv).sort((a, b) => +a - +b).forEach(l => {
      byLv[l].forEach((s, i) => {
        idToNid[s.id] = _addStep(s, 80 + l * 260, 60 + i * 140);
      });
    });
    steps.forEach(s => (s.depends_on || []).forEach(d => {
      if (idToNid[d] && idToNid[s.id]) {
        try { state.df.addConnection(idToNid[d], idToNid[s.id], 'output_1', 'input_1'); }
        catch (_e) {}
      }
    }));
    _refreshEmpty();
    state.dirty = false;
  }

    function _exportDAG() {
      if (!state.df) return { name: state.name, steps: [] };
      const data = state.df.export();
      const nodes = data.drawflow.Home.data || {};
      const nidToStep = {};
      Object.entries(nodes).forEach(([nid, n]) => { nidToStep[nid] = (n.data && n.data.step) || {}; });
      const isDec = nid => (nidToStep[nid] || {}).agent_type === 'decision';
      const rawIn = {};
      Object.entries(nodes).forEach(([nid, n]) => {
        rawIn[nid] = [];
        Object.values(n.inputs || {}).forEach(inp => {
          (inp.connections || []).forEach(c => rawIn[nid].push({ src: String(c.node), port: c.input || 'output_1' }));
        });
      });
      // [#2-②] 决策节点自身不导出; 下游按连的出口拿分支条件: 上口output_1(✓)继承条件, 下口output_2(✗)取反{"not":..}; 依赖继承决策上游
      const steps = [];
      Object.entries(nodes).forEach(([nid]) => {
        if (isDec(nid)) return;
        const s = { ...(nidToStep[nid] || {}) };
        const deps = [];
        const branchWhen = [];
        rawIn[nid].forEach(c => {
          if (isDec(c.src)) {
            const dc = (nidToStep[c.src] || {}).when || [];
            const arr = Array.isArray(dc) ? dc : [dc];
            const isFalse = String(c.port || '').endsWith('2');
            arr.forEach(x => branchWhen.push(isFalse ? { not: x } : x));
            (rawIn[c.src] || []).forEach(uc => {
              const uid = (nidToStep[uc.src] || {}).id;
              if (uid && !deps.includes(uid)) deps.push(uid);
            });
          } else {
            const sid = (nidToStep[c.src] || {}).id;
            if (sid && !deps.includes(sid)) deps.push(sid);
          }
        });
        if (branchWhen.length) {
          const ex = Array.isArray(s.when) ? s.when : (s.when ? [s.when] : []);
          s.when = ex.concat(branchWhen);
        }
        if (deps.length) s.depends_on = deps; else delete s.depends_on;
        steps.push(s);
      });
      return { name: state.name || 'untitled', steps };
    }

  async function _save() {
    const name = state.name.trim();
    if (!name) { alert('先填 Pipeline 名'); return; }
    if (!/^[A-Za-z0-9_\-.]+$/.test(name)) { alert('名字只能 a-z0-9_-.'); return; }
    const dag = _exportDAG();
    try {
      await api('PUT', '/api/dags/' + encodeURIComponent(name), dag);
      state.dirty = false;
      _toast('✓ 已保存');
    } catch (e) { alert('保存失败: ' + e.message); }
  }

  async function _build() {
    if (state.dirty || !state.name) {
      await _save();
      if (!state.name) return;
    }
    try {
      const d = await api('POST', '/api/dags/' + encodeURIComponent(state.name) + '/run');
      if (d.job_id && window.DAG) window.DAG.showBuild(d.job_id, state.name);
    } catch (e) { alert('启动失败: ' + e.message); }
  }

  function _toast(msg) {
    let el = document.getElementById('des-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'des-toast';
      el.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1e293b;color:#fff;padding:8px 16px;border-radius:6px;font-size:12px;z-index:99999;opacity:0;transition:opacity .2s';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.style.opacity = '1';
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.style.opacity = '0'; }, 1800);
  }

  /* ---- 运行条件 when ---- */
  let _drawerStepIds = [];   // 当前节点可引用的上游 step id
  let _drawerWhenExtra = []; // 无法用条件行表达的原始 when 项 (dict/复杂串), 保存时原样回写

  /* 一个条件对象 {sid, op, val} → v2 字符串. 返回 null 表示无效行 (跳过). */
  function _whenToStr(c) {
    if (!c.sid || !c.op) return null;
    if (c.op === 'success') return `$step.${c.sid}.status eq success`;
    if (c.op === 'failure') return `$step.${c.sid}.status eq failure`;
    return `$step.${c.sid}.output ${c.op} ${c.val == null ? '' : String(c.val)}`;
  }

  /* 单个 when 项 (字符串) → 条件对象. 认不出的 (dict / 复杂串) 返回 null → 归 extra. */
  function _parseWhenItem(item) {
    if (typeof item !== 'string') return null;
    const s = item.trim();
    let m = /^(success|failure):([\w\-]+)$/.exec(s);
    if (m) return { sid: m[2], op: m[1], val: '' };
    m = /^contains:([\w\-]+):(.*)$/.exec(s);
    if (m) return { sid: m[1], op: 'contains', val: m[2] };
    m = /^\$step\.([\w\-]+)\.status\s+eq\s+(success|failure)$/.exec(s);
    if (m) return { sid: m[1], op: m[2], val: '' };
    m = /^\$step\.([\w\-]+)\.output\s+(contains|eq|ne|gt|ge|lt|le)\s+(.*)$/.exec(s);
    if (m) return { sid: m[1], op: m[2], val: m[3] };
    return null;
  }

  /* step.when (str|dict|list|null) → {rows:[条件对象], extra:[原始不可编辑项]} */
  function _parseWhen(when) {
    const rows = [], extra = [];
    let arr;
    if (when == null || when === '') arr = [];
    else if (Array.isArray(when)) arr = when;
    else arr = [when];
    arr.forEach(item => {
      const c = _parseWhenItem(item);
      if (c) rows.push(c); else extra.push(item);
    });
    return { rows, extra };
  }

  /* 当前画布里除 excludeNid 外的全部 step id (供条件引用上游). */
  function _stepIdsExcept(nid) {
    const ids = [];
    if (!state.df) return ids;
    const nodes = (state.df.export().drawflow.Home.data) || {};
    Object.entries(nodes).forEach(([k, n]) => {
      if (parseInt(k, 10) === nid) return;
      const s = (n.data && n.data.step) || {};
      if (s.id) ids.push(s.id);
    });
    return ids;
  }

  function _whenRowHTML(c, stepIds) {
    const ids = stepIds.slice();
    if (c.sid && !ids.includes(c.sid)) ids.unshift(c.sid); // 保留引用了已删/未知步骤的旧条件
    const sidOpts = ids.map(id =>
      `<option value="${esc(id)}"${id === c.sid ? ' selected' : ''}>${esc(id)}</option>`).join('')
      || '<option value="">(无上游步骤)</option>';
    const opOpts = WHEN_OPS.map(o =>
      `<option value="${o.v}"${o.v === c.op ? ' selected' : ''}>${o.label}</option>`).join('');
    const needsVal = _opNeedsVal(c.op);
    return `<div class="dr-when-row" style="display:flex;gap:4px;margin-bottom:4px;align-items:center">
      <select data-when-sid style="flex:0 0 84px;min-width:0">${sidOpts}</select>
      <select data-when-op style="flex:0 0 120px;min-width:0">${opOpts}</select>
      <input data-when-val placeholder="值" value="${esc(c.val || '')}" style="flex:1;min-width:0;${needsVal ? '' : 'display:none'}">
      <button class="des-btn" data-drawer="when-del" style="flex:0 0 auto;padding:2px 7px;line-height:1">×</button>
    </div>`;
  }

  function _renderWhenRows(rows, stepIds) {
    const wrap = state.container.querySelector('#dr-when-rows');
    if (!wrap) return;
    wrap.innerHTML = rows.map(c => _whenRowHTML(c, stepIds)).join('');
  }

  function _addWhenRow() {
    const wrap = state.container.querySelector('#dr-when-rows');
    if (!wrap) return;
    const holder = document.createElement('div');
    holder.innerHTML = _whenRowHTML({ sid: _drawerStepIds[0] || '', op: 'success', val: '' }, _drawerStepIds);
    wrap.appendChild(holder.firstElementChild);
    state.dirty = true;
  }

  /* 从 DOM 条件行 + extra 收集出 when 数组 (供保存回存节点). */
  function _collectWhen() {
    const out = [];
    state.container.querySelectorAll('#dr-when-rows .dr-when-row').forEach(r => {
      const sid = r.querySelector('[data-when-sid]').value;
      const op = r.querySelector('[data-when-op]').value;
      const valEl = r.querySelector('[data-when-val]');
      const str = _whenToStr({ sid, op, val: valEl ? valEl.value.trim() : '' });
      if (str) out.push(str);
    });
    return out.concat(_drawerWhenExtra || []);
  }

  /* ---- 抽屉 ---- */
  let _drawerNid = null;
  function _openDrawer(nid) {
    _drawerNid = nid;
    const node = state.df && state.df.getNodeFromId(nid);
    if (!node) return;
    const step = (node.data && node.data.step) || {};
    const at = step.agent_type || 'coder';
    const body = state.container.querySelector('#des-drawer-body');
    const isTool = at === 'tool';
    const isDag = at === 'dag';
    const agentOpts = [...AGENTS, 'tool', 'dag'].map(a =>
      `<option value="${a}"${a === at ? ' selected' : ''}>${a}</option>`).join('');
    body.innerHTML = `
      <div class="des-fld"><label>id</label><input id="dr-id" value="${esc(step.id || '')}"></div>
      <div class="des-fld"><label>agent_type</label><select id="dr-at">${agentOpts}</select></div>
      <div class="des-fld" ${isTool ? '' : 'style="display:none"'} id="dr-tn-row">
        <label>tool_name</label><input id="dr-tn" value="${esc(step.tool_name || '')}"></div>
      <div class="des-fld" ${isDag ? '' : 'style="display:none"'} id="dr-dag-row">
        <label>子 DAG 名 (dag_name)</label><input id="dr-dag" value="${esc(step.dag_name || '')}" placeholder="要运行的已保存 DAG 名"></div>
      <div class="des-fld"><label>task / 描述</label><textarea id="dr-task">${esc(step.task || '')}</textarea></div>
      <div class="des-fld"><label>label (可选显示名)</label><input id="dr-label" value="${esc(step.label || '')}"></div>
      <div class="des-fld">
        <label>运行条件 when (多行 = 且 / AND, 空 = 无条件)</label>
        <div id="dr-when-rows"></div>
        <button class="des-btn" data-drawer="when-add" style="margin-top:2px;font-size:11px;padding:3px 9px">+ 加条件</button>
      </div>
      <div class="des-fld" style="color:var(--tx3,#94a3b8);font-size:11px;line-height:1.5;background:#f8fafc;padding:8px;border-radius:4px">
        💡 引上游步骤输出用 <code>$step.&lt;id&gt;.output</code>. 依赖从画布连线得到, 不用手填.<br>
        ⚡ 条件里"前步"只能选画布里已有的其它步骤; 多行之间是"且"的关系.
      </div>`;
    body.querySelector('#dr-at').addEventListener('change', e => {
      const isT = e.target.value === 'tool';
      body.querySelector('#dr-tn-row').style.display = isT ? '' : 'none';
      const isD = e.target.value === 'dag';
      body.querySelector('#dr-dag-row').style.display = isD ? '' : 'none';
    });
    /* when 条件行 */
    _drawerStepIds = _stepIdsExcept(nid);
    const parsed = _parseWhen(step.when);
    _drawerWhenExtra = parsed.extra;
    _renderWhenRows(parsed.rows, _drawerStepIds);
    state.container.querySelector('#des-drawer').classList.add('on');
  }
  function _closeDrawer() {
    state.container.querySelector('#des-drawer').classList.remove('on');
    _drawerNid = null;
  }
  function _saveDrawer() {
    if (_drawerNid == null || !state.df) return;
    const body = state.container.querySelector('#des-drawer-body');
    const node = state.df.getNodeFromId(_drawerNid);
    if (!node) return;
    const step = { ...(node.data && node.data.step || {}) };
    step.id = body.querySelector('#dr-id').value.trim() || step.id;
    step.agent_type = body.querySelector('#dr-at').value;
    step.task = body.querySelector('#dr-task').value;
    step.label = body.querySelector('#dr-label').value.trim() || undefined;
    if (step.agent_type === 'tool') {
      step.tool_name = body.querySelector('#dr-tn').value.trim();
    } else { delete step.tool_name; }
    if (step.agent_type === 'dag') {
      step.dag_name = body.querySelector('#dr-dag').value.trim();
    } else { delete step.dag_name; }
    /* 运行条件 when → 数组 (顶层 AND); 空则删字段 */
    const when = _collectWhen();
    if (when.length) step.when = when; else delete step.when;
    /* 更新 drawflow node data + HTML */
    state.df.updateNodeDataFromId(_drawerNid, { ...(node.data || {}), step });  // [FIX 2026-09-04] getNodeFromId 返回深拷贝, 直接赋值不写回 store → 抽屉编辑全丢; 用官方 API 写回
    const el = document.getElementById('node-' + _drawerNid);
    if (el) {
      const inner = el.querySelector('.drawflow_content_node');
      if (inner) inner.innerHTML = _nodeHTML(step);
    }
    state.dirty = true;
    _closeDrawer();
    _toast('✓ 节点已更新');
  }
  function _delDrawer() {
    if (_drawerNid == null || !state.df) return;
    state.df.removeNodeId('node-' + _drawerNid);
    state.dirty = true;
    _closeDrawer();
  }

  /* ---- AI 生成 / 改 ---- */
  function _toggleAI(on) {
    const card = state.container.querySelector('#des-ai-card');
    card.classList.toggle('on', !!on);
    if (on) setTimeout(() => card.querySelector('#des-ai-req').focus(), 40);
  }
  async function _aiGenerate() {
    const ta = state.container.querySelector('#des-ai-req');
    const req = ta.value.trim();
    if (!req) return;
    _toast('AI 生成中…');
    try {
      const d = await api('POST', '/api/dags/generate', { request: req });
      if (d.steps) {
        _importDAG(d);
        if (d.name && !state.name) {
          state.name = d.name;
          state.container.querySelector('#des-name').value = d.name;
        }
        state.dirty = true;
        _toggleAI(false);
        ta.value = '';
        _toast('✓ AI 已生成 ' + d.steps.length + ' 步');
      } else {
        alert('AI 生成失败: ' + (d.error || '未知'));
      }
    } catch (e) { alert('AI 错误: ' + e.message); }
  }

  window.mountDAGEditor = render;
  console.log('[dag_editor_simple] loaded — window.mountDAGEditor()');
})();
