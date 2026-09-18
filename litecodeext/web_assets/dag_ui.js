/* dag_ui.js — Jenkins 化 DAG 骨架 (v2).
 *
 * 全屏 overlay + 3 屏切换 (list / editor / build), 心智照抄 Jenkins:
 *   Pipeline 列表 → 点进 Pipeline 编辑 → ▶ Build → Build 详情 (全屏)
 *
 * 依赖: dag_pipelines.js / dag_editor_simple.js / dag_build_view.js
 * 老 UI (dag_drawflow.js) 通过 ?ui=old 兜底.
 */
(function () {
  'use strict';

  const CSS_ID = 'dag-ui-css';
  const CSS = `
    .dag-ui{position:fixed;inset:0;background:var(--bg,#fff);z-index:900;display:none;flex-direction:column;overflow:hidden;font-family:var(--sans,system-ui)}
    .dag-ui.on{display:flex}
    .dag-ui-top{display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--sf,#fff);border-bottom:1px solid var(--bd,#e2e8f0);flex:0 0 auto}
    .dag-ui-top .dag-crumb{font-size:13px;color:var(--tx,#1e293b);font-weight:600}
    .dag-ui-top .dag-crumb .sep{color:var(--tx3,#94a3b8);margin:0 6px;font-weight:400}
    .dag-ui-top .dag-crumb .link{color:var(--ac,#3b82f6);cursor:pointer;text-decoration:none}
    .dag-ui-top .dag-crumb .link:hover{text-decoration:underline}
    .dag-ui-top .dag-ver{font-size:10px;color:var(--tx3,#94a3b8);font-family:var(--mono,monospace);margin-left:6px}
    .dag-ui-top .dag-spacer{flex:1}
    .dag-ui-top .dag-close-btn{border:1px solid var(--bd2,#cbd5e1);background:var(--sf2,#f8fafc);color:var(--tx,#1e293b);border-radius:6px;padding:4px 12px;font-size:12px;cursor:pointer}
    .dag-ui-top .dag-close-btn:hover{background:var(--bd,#e2e8f0)}
    .dag-ui-body{flex:1;overflow:hidden;position:relative}
    .dag-screen{position:absolute;inset:0;display:none;overflow:hidden}
    .dag-screen.on{display:flex;flex-direction:column}
  `;

  function ensureCSS() {
    if (document.getElementById(CSS_ID)) return;
    const s = document.createElement('style');
    s.id = CSS_ID;
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function ensureOverlay() {
    let ov = document.getElementById('dag-ui');
    if (ov) return ov;
    ov = document.createElement('div');
    ov.className = 'dag-ui';
    ov.id = 'dag-ui';
    ov.innerHTML = `
      <div class="dag-ui-top">
        <div class="dag-crumb">
          <span class="link" data-nav="list">Pipeline</span><span class="sep">›</span>
          <span data-cur></span>
        </div>
        <span class="dag-ver">v2 · Jenkins-lite</span>
        <div class="dag-spacer"></div>
        <button class="dag-close-btn" data-close="1">关闭</button>
      </div>
      <div class="dag-ui-body">
        <div class="dag-screen" data-screen="list"></div>
        <div class="dag-screen" data-screen="editor"></div>
        <div class="dag-screen" data-screen="build"></div>
      </div>`;
    document.body.appendChild(ov);
    /* 事件委托: 关闭 / 面包屑 nav */
    ov.querySelector('.dag-ui-top').addEventListener('click', e => {
      const t = e.target;
      if (t.matches('[data-close]')) { API.close(); return; }
      const nav = t.getAttribute && t.getAttribute('data-nav');
      if (nav === 'list') API.showList();
    });
    return ov;
  }

  const API = {
    _state: { screen: null, pipeline: null, jobId: null },

    open() {
      ensureCSS();
      const ov = ensureOverlay();
      ov.classList.add('on');
      this.showList();
    },
    close() {
      const ov = document.getElementById('dag-ui');
      if (ov) ov.classList.remove('on');
      /* 关闭时销毁 Build 面板 (下次再进重建, 避免 timer 泄漏) */
      if (window._dagBuildInline) {
        try { window._dagBuildInline.close(); } catch (_e) {}
        window._dagBuildInline = null;
      }
    },
    showList() {
      ensureOverlay();
      this._switch('list');
      this._crumb('');
      if (typeof window.mountDAGPipelines === 'function') {
        window.mountDAGPipelines(document.querySelector('[data-screen="list"]'));
      }
    },
    showEditor(name) {
      ensureOverlay();
      this._state.pipeline = name || null;
      this._switch('editor');
      this._crumb(name || '(新)');
      if (typeof window.mountDAGEditor === 'function') {
        window.mountDAGEditor(document.querySelector('[data-screen="editor"]'), name || '');
      }
    },
    showBuild(jobId, name) {
      ensureOverlay();
      this._state.jobId = jobId;
      this._switch('build');
      this._crumb((name || this._state.pipeline || '?') + ' · Build #' + (jobId ? jobId.slice(0, 6) : '?'));
      if (typeof window.mountDAGBuildFullscreen === 'function') {
        window.mountDAGBuildFullscreen(
          document.querySelector('[data-screen="build"]'),
          jobId, name || this._state.pipeline || '');
      }
    },

    _switch(name) {
      this._state.screen = name;
      document.querySelectorAll('.dag-screen').forEach(s => {
        s.classList.toggle('on', s.getAttribute('data-screen') === name);
      });
    },
    _crumb(text) {
      const el = document.querySelector('.dag-crumb [data-cur]');
      if (el) el.textContent = text || '';
    },
  };

  window.DAG = API;
  /* 生产入口 — 直接 open() 到列表页 */
  window.openDAG = () => API.open();

  console.log('[dag_ui] v2 Jenkins-lite loaded — window.openDAG()');
})();
