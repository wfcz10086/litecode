/* dag_pipelines.js — 屏 A · Pipeline 列表 (Jenkins Home 风).
 *
 * 一列 Pipeline 卡片: 名字 + 上次 Build 状态点 + 上次 run 时间.
 * 顶部 "+ 新 Pipeline" 三入口: 空模板 / 预设 / AI 生成.
 * 挂载: window.mountDAGPipelines(container).
 */
(function () {
  'use strict';

  const STATUS_COLOR = {
    done: '#10b981', success: '#10b981',
    failed: '#ef4444', error: '#ef4444',
    aborted: '#f59e0b', interrupted: '#f59e0b',
    running: '#3b82f6', queued: '#3b82f6',
    idle: '#94a3b8', '?': '#94a3b8',
  };

  const PRESETS = [
    { key: 'blank', label: '空 Pipeline', desc: '拖节点自己搭' },
    { key: 'daily_ops', label: '📆 每日运维三件套', desc: '磁盘 / 内存 / 网络 + 汇总' },
    { key: 'host_health', label: '💚 主机健康检查', desc: 'shell → 分析 → 报告' },
    { key: 'web_summary', label: '📰 网页抓取总结', desc: '搜 → 抓 → 提炼' },
    { key: 'illustrated_post', label: '🎨 图文笔记', desc: '短文 → prompt → 生图 → 组装' },
  ];

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function fmtTime(ts) {
    if (!ts) return '';
    const d = Date.now() / 1000 - ts;
    if (d < 60) return Math.round(d) + 's 前';
    if (d < 3600) return Math.round(d / 60) + '分钟前';
    if (d < 86400) return Math.round(d / 3600) + '小时前';
    return Math.round(d / 86400) + '天前';
  }

  async function api(m, u, body) {
    const r = await fetch(u, {
      method: m,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
    });
    if (!r.ok) throw new Error(u + ' ' + r.status);
    return r.json();
  }

  const CSS = `
    .dpl-wrap{display:flex;flex-direction:column;height:100%}
    .dpl-top{padding:12px 16px;background:var(--sf2,#f8fafc);border-bottom:1px solid var(--bd,#e2e8f0);display:flex;gap:8px;align-items:center;flex-wrap:wrap;flex:0 0 auto}
    .dpl-top .title{font-weight:600;font-size:14px;color:var(--tx,#1e293b);margin-right:auto}
    .dpl-top .cnt{font-size:11px;color:var(--tx3,#64748b);font-family:var(--mono,monospace)}
    .dpl-new-btn{background:var(--ac,#3b82f6);color:#fff;border:0;border-radius:6px;padding:6px 14px;font-size:13px;font-weight:600;cursor:pointer}
    .dpl-new-btn:hover{filter:brightness(1.06)}
    .dpl-list{flex:1;overflow:auto;padding:8px 12px}
    .dpl-card{display:flex;align-items:center;gap:10px;padding:10px 14px;margin:6px 0;background:var(--sf,#fff);border:1px solid var(--bd,#e2e8f0);border-radius:8px;cursor:pointer;transition:all .12s}
    .dpl-card:hover{border-color:var(--ac,#3b82f6);box-shadow:0 2px 8px rgba(0,0,0,.04);transform:translateY(-1px)}
    .dpl-card .dot{width:10px;height:10px;border-radius:50%;flex:0 0 auto}
    .dpl-card .body{flex:1;min-width:0}
    .dpl-card .name{font-weight:600;font-size:13px;color:var(--tx,#1e293b);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .dpl-card .meta{font-size:11px;color:var(--tx3,#64748b);font-family:var(--mono,monospace);margin-top:2px}
    .dpl-card .actions{display:flex;gap:4px;flex:0 0 auto}
    .dpl-act-btn{background:transparent;border:1px solid var(--bd2,#cbd5e1);color:var(--tx2,#475569);border-radius:5px;padding:3px 8px;font-size:11px;cursor:pointer}
    .dpl-act-btn:hover{background:var(--sf2,#f8fafc);color:var(--tx,#1e293b)}
    .dpl-act-btn.danger:hover{background:#fef2f2;color:#dc2626;border-color:#fca5a5}
    .dpl-empty{padding:60px 20px;text-align:center;color:var(--tx3,#64748b);font-size:13px}
    .dpl-new-menu{position:absolute;top:52px;right:16px;left:auto;background:var(--sf,#fff);border:1px solid var(--bd,#e2e8f0);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.08);z-index:10;min-width:280px;display:none}
    .dpl-new-menu.on{display:block}
    .dpl-new-item{padding:10px 14px;border-bottom:1px solid var(--bd,#f1f5f9);cursor:pointer}
    .dpl-new-item:last-child{border-bottom:0}
    .dpl-new-item:hover{background:var(--sf2,#f8fafc)}
    .dpl-new-item .lbl{font-weight:600;font-size:13px;color:var(--tx,#1e293b)}
    .dpl-new-item .dsc{font-size:11px;color:var(--tx3,#64748b);margin-top:2px}
    .dpl-ai-box{padding:12px;border-top:1px solid var(--bd,#e2e8f0);background:var(--sf2,#f8fafc)}
    .dpl-ai-box textarea{width:100%;min-height:60px;padding:8px;border:1px solid var(--bd2,#cbd5e1);border-radius:5px;font-size:12px;font-family:inherit;resize:vertical;box-sizing:border-box}
    .dpl-ai-box .row{display:flex;gap:6px;margin-top:6px}
    .dpl-ai-box .row button{padding:5px 12px;font-size:12px;border-radius:5px;border:0;cursor:pointer}
    .dpl-ai-box .row .prim{background:var(--ac,#3b82f6);color:#fff}
    .dpl-ai-box .row .sec{background:var(--sf,#fff);color:var(--tx2,#475569);border:1px solid var(--bd2,#cbd5e1)}
  `;

  function ensureCSS() {
    if (document.getElementById('dpl-css')) return;
    const s = document.createElement('style');
    s.id = 'dpl-css';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function _card(it) {
    const lb = it.last_build || null;
    const st = lb ? (lb.status || '?') : 'idle';
    const color = STATUS_COLOR[st] || STATUS_COLOR['?'];
    const timeText = lb ? (fmtTime(lb.started) + ' · ' + (lb.elapsed ? Math.round(lb.elapsed) + 's' : '?')) : '从未运行';
    const buildId = lb ? '#' + (lb.job_id || '').slice(0, 6) : '';
    return `
      <div class="dpl-card" data-name="${esc(it.name)}">
        <div class="dot" style="background:${color}" title="${esc(st)}"></div>
        <div class="body">
          <div class="name">${esc(it.name)}</div>
          <div class="meta">${esc(st)} ${esc(buildId)} · ${esc(timeText)}</div>
        </div>
        <div class="actions">
          <button class="dpl-act-btn" data-act="run" data-name="${esc(it.name)}">▶ Build</button>
          <button class="dpl-act-btn danger" data-act="del" data-name="${esc(it.name)}">🗑</button>
        </div>
      </div>`;
  }

  function _newMenu() {
    const items = PRESETS.map(p => `
      <div class="dpl-new-item" data-preset="${esc(p.key)}">
        <div class="lbl">${esc(p.label)}</div>
        <div class="dsc">${esc(p.desc)}</div>
      </div>`).join('');
    return `<div class="dpl-new-menu" id="dpl-new-menu">
        ${items}
        <div class="dpl-ai-box">
          <div style="font-size:11px;color:var(--tx3,#64748b);margin-bottom:4px">🤖 用自然语言生成:</div>
          <textarea id="dpl-ai-req" placeholder="例: 每天早上抓取比特币价格并发企微通知"></textarea>
          <div class="row">
            <button class="prim" data-preset="_ai_go">生成</button>
            <button class="sec" data-preset="_close">取消</button>
          </div>
        </div>
      </div>`;
  }

  async function render(container) {
    ensureCSS();
    container.innerHTML = `
      <div class="dpl-wrap">
        <div class="dpl-top" style="position:relative">
          <div class="title">Pipeline 列表</div>
          <span class="cnt" id="dpl-cnt">加载中…</span>
          <button class="dpl-new-btn" id="dpl-new">+ 新 Pipeline</button>
          ${_newMenu()}
        </div>
        <div class="dpl-list" id="dpl-list">
          <div class="dpl-empty">加载中…</div>
        </div>
      </div>`;
    await refresh(container);
    _bind(container);
  }

  async function refresh(container) {
    const list = container.querySelector('#dpl-list');
    const cnt = container.querySelector('#dpl-cnt');
    try {
      const d = await api('GET', '/api/dags');
      const items = d.items || (d.dags || []).map(n => ({ name: n, last_build: null }));
      items.sort((a, b) => {
        const ta = a.last_build ? a.last_build.started : 0;
        const tb = b.last_build ? b.last_build.started : 0;
        return tb - ta;
      });
      cnt.textContent = items.length + ' 个 pipeline';
      list.innerHTML = items.length
        ? items.map(_card).join('')
        : '<div class="dpl-empty">还没有 Pipeline. 点右上角 "+ 新 Pipeline" 创建.</div>';
    } catch (e) {
      list.innerHTML = '<div class="dpl-empty">加载失败: ' + esc(e.message) + '</div>';
      cnt.textContent = '错误';
    }
  }

  function _bind(container) {
    const list = container.querySelector('#dpl-list');
    const newBtn = container.querySelector('#dpl-new');
    const menu = container.querySelector('#dpl-new-menu');

    /* + 新 Pipeline 按钮 toggle 菜单 */
    newBtn.addEventListener('click', e => {
      e.stopPropagation();
      menu.classList.toggle('on');
    });
    /* 点外面关菜单 */
    document.addEventListener('click', e => {
      if (menu.classList.contains('on') && !menu.contains(e.target) && e.target !== newBtn) {
        menu.classList.remove('on');
      }
    });

    /* 卡片点击 (进编辑) + 按钮点击 (run / del) 统一委托 */
    list.addEventListener('click', async e => {
      const act = e.target.closest('[data-act]');
      if (act) {
        e.stopPropagation();
        const name = act.getAttribute('data-name');
        const kind = act.getAttribute('data-act');
        if (kind === 'run') {
          await _quickRun(container, name);
        } else if (kind === 'del') {
          if (!confirm('删除 Pipeline "' + name + '"?')) return;
          try {
            await api('DELETE', '/api/dags/' + encodeURIComponent(name));
            await refresh(container);
          } catch (err) { alert('删除失败: ' + err.message); }
        }
        return;
      }
      const card = e.target.closest('.dpl-card');
      if (card) {
        const name = card.getAttribute('data-name');
        if (window.DAG) window.DAG.showEditor(name);
      }
    });

    /* 新建菜单 preset / AI */
    menu.addEventListener('click', async e => {
      const item = e.target.closest('[data-preset]');
      if (!item) return;
      const key = item.getAttribute('data-preset');
      if (key === '_close') { menu.classList.remove('on'); return; }
      if (key === '_ai_go') {
        const req = menu.querySelector('#dpl-ai-req').value.trim();
        if (!req) { alert('先填需求描述'); return; }
        menu.classList.remove('on');
        try {
          const d = await api('POST', '/api/dags/generate', { request: req });
          if (d.steps) {
            /* 生成成功 → 进编辑器, 编辑器会读 __dfPendingSteps */
            window.__dpl_pending_dag = d;
            if (window.DAG) window.DAG.showEditor('');
          } else {
            alert('AI 生成失败: ' + (d.error || '未知'));
          }
        } catch (err) { alert('AI 生成错误: ' + err.message); }
        return;
      }
      /* preset key → 进编辑器 (空 or 预设) */
      menu.classList.remove('on');
      window.__dpl_pending_preset = key;
      if (window.DAG) window.DAG.showEditor('');
    });
  }

  async function _quickRun(container, name) {
    try {
      const d = await api('POST', '/api/dags/' + encodeURIComponent(name) + '/run');
      if (d.job_id && window.DAG) {
        window.DAG.showBuild(d.job_id, name);
      } else {
        alert('启动失败: ' + (d.error || JSON.stringify(d)));
      }
    } catch (e) { alert('启动失败: ' + e.message); }
  }

  window.mountDAGPipelines = render;
  console.log('[dag_pipelines] loaded — window.mountDAGPipelines()');
})();
