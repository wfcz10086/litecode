/* settings_panel.js — [ui-v2 2026-10-21] 设置改版 (右侧抽屉, 覆盖 chat_panels.js 里的 loadSet)
 *
 * 常用: 状态 / 模型 (可搜索; 区分 当前 · 已配置 · 未配置 key/占位符 key; 地址/ctx 等细节折叠) / 外观与语言 / 修改密码 / 退出
 * 高级: 添加模型 / 配置编辑 (config_editor.js 的 loadCfg) / 服务器信息
 * 行为不变: 切换 / 测试 / 删除 / 添加 仍调 config_editor.js 的 mdlSwitch / mdlTest / mdlDel / mdlAdd,
 * 元素 id (mt{i} / mtr{i} / nm-* / cfg-bd / pwd-card-host) 保持原样。
 */
(function () {
  const TAB_KEY = 'lc.settings.tab';
  const PLACEHOLDER_TAILS = ['MPLE', 'e_me', 'ange', 'xxxx', 'XXXX', '****'];

  function keyState(m) {
    const k = String(m.api_key || '');
    const tail = k.replace(/^•+/, '');
    const url = String(m.backend_url || '');
    const hosted = /^https:\/\//i.test(url) && !/\/\/(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|localhost)/i.test(url);
    if (tail && PLACEHOLDER_TAILS.some(t => tail.endsWith(t))) return {cls: 'bad', txt: '占位符 key'};
    if (!tail && hosted) return {cls: 'bad', txt: '未配置 key'};
    return {cls: 'ok', txt: tail ? '已配置' : '本地 · 无需 key'};
  }

  function themeChips() {
    if (typeof THEMES === 'undefined') return '';
    let cur = null;
    try { cur = localStorage.getItem('theme'); } catch (e) {}
    if (cur === null) cur = 'kimi';
    return THEMES.map(t => `<button type="button" class="st-chip${t.id === cur ? ' on' : ''}" onclick="applyTheme('${t.id}')" title="${esc(t.desc)}">${esc(t.name)}</button>`).join('');
  }

  function modelRow(m, i, active, activeBackend) {
    const mUrl = (m.backend_url || '').replace(/\/+$/, '');
    const isActive = m.id === active && (!activeBackend || !mUrl || mUrl === activeBackend);
    const ks = keyState(m);
    const badge = isActive ? '<span class="st-badge cur">当前默认</span>' : `<span class="st-badge ${ks.cls}">${ks.txt}</span>`;
    const vis = m.supports_vision ? '<span class="st-tag">👁 视觉</span>' : '';
    const q = esc(((m.label || '') + ' ' + m.id + ' ' + mUrl).toLowerCase());
    return `<div class="st-model${isActive ? ' cur' : ''}" data-q="${q}">
      <div class="st-mh">
        <div class="st-mn" title="${esc(m.id)}">${esc(m.label || m.id)}</div>${badge}${vis}
      </div>
      <details class="st-md"><summary>详情</summary>
        <div>ID: <code>${esc(m.id)}</code></div>
        <div>地址: <code>${esc(m.backend_url || '')}</code> · ${esc(m.backend_type || 'openai')}</div>
        <div>上下文 ${(m.context_window || 0).toLocaleString()} · 单次输出上限 ${m.max_tokens || '?'} · Key ${esc(m.api_key || '无')}</div>
      </details>
      <div class="st-ma">
        ${!isActive ? `<button class="abtn" onclick="mdlSwitch('${esc(m.id)}', '${esc(m.backend_url || '')}')">设为默认</button>` : ''}
        <button class="abtn" onclick="mdlTest(${i})" id="mt${i}">测试</button>
        <button class="abtn dng" onclick="mdlDel('${esc(m.id)}')">删除</button>
        <span id="mtr${i}" class="st-mr"></span>
      </div>
    </div>`;
  }

  function addModelForm() {
    const IN = 'class="st-in"';
    return `<div class="st-form">
      <div class="st-row2"><input id="nm-id" ${IN} placeholder="Model ID (如 deepseek-chat)">
        <select id="nm-type" ${IN}><option value="vllm">vLLM</option><option value="ollama">Ollama</option><option value="openai">OpenAI</option><option value="generic">通用</option></select></div>
      <input id="nm-url" ${IN} placeholder="Backend URL (http://host:port/v1)">
      <div class="st-row2"><input id="nm-key" ${IN} placeholder="API Key (本地模型可留空)">
        <input id="nm-ctx" ${IN} type="number" placeholder="上下文" value="200000" title="上下文窗口 (tokens)">
        <input id="nm-max" ${IN} type="number" placeholder="max_tokens" value="18000" title="单次输出上限"></div>
      <div class="st-row2" style="align-items:center">
        <label class="st-ck"><input type="checkbox" id="nm-vision"> 支持图片</label>
        <label class="st-ck"><input type="checkbox" id="nm-think" checked> 思考模式</label>
        <span style="flex:1"></span><button class="abtn st-primary" onclick="mdlAdd()">添加模型</button></div>
    </div>`;
  }

  window.loadSet = async function loadSet() {
    const el = document.getElementById('set-bd');
    try {
      const [hRes, mRes] = await Promise.all([fetch('/api/health'), fetch('/api/models')]);
      const h = await hRes.json(), md = await mRes.json();
      const active = md.active || h.model || '?', models = md.models || [];
      const activeBackend = (md.active_backend || h.backend_url || '').replace(/\/+$/, '');
      let tab = 'common';
      try { tab = localStorage.getItem(TAB_KEY) || 'common'; } catch (e) {}
      const nBad = models.filter(m => keyState(m).cls === 'bad').length;
      const en = window.I18N && I18N.locale === 'en';
      el.innerHTML = `
      <div class="st-tabs" role="tablist">
        <button type="button" role="tab" data-tab="common" class="${tab === 'common' ? 'on' : ''}">常用</button>
        <button type="button" role="tab" data-tab="adv" class="${tab === 'adv' ? 'on' : ''}">高级</button>
      </div>
      <div class="st-pane" data-pane="common"${tab === 'common' ? '' : ' hidden'}>
        <div class="st-card st-status">
          <span class="st-dot ${h.ok ? 'ok' : 'bad'}"></span><b>${h.ok ? '服务在线' : '服务离线'}</b>
          <span class="st-sub">默认模型 ${esc(active)}</span>
        </div>
        <div class="st-h">模型 <span class="st-cnt">${models.length} 个${nBad ? ` · ${nBad} 个缺 key` : ''}</span></div>
        <input id="st-msearch" class="st-in" placeholder="搜索模型 (名称 / ID / 地址)" autocomplete="off">
        <div class="st-models">${models.map((m, i) => [m, i]).sort((a, b) => (b[0].id === active) - (a[0].id === active)).map(([m, i]) => modelRow(m, i, active, activeBackend)).join('')}</div>
        <div class="st-h">外观与语言</div>
        <div class="st-card"><div class="st-chips">${themeChips()}</div>
          <div class="st-chips" style="margin-top:8px">
            <button type="button" class="st-chip${en ? '' : ' on'}" onclick="I18N.setLocale('zh');loadSet()">中文</button>
            <button type="button" class="st-chip${en ? ' on' : ''}" onclick="I18N.setLocale('en');loadSet()">English</button></div></div>
        <div id="pwd-card-host"></div>
        <div style="margin-top:16px"><button class="abtn" onclick="doLogout()">退出登录</button></div>
      </div>
      <div class="st-pane" data-pane="adv"${tab === 'adv' ? '' : ' hidden'}>
        <div class="st-h">服务器</div>
        <div class="st-card st-kv"><div><span>默认模型</span><code>${esc(active)}</code></div>
          <div><span>后端地址</span><code>${esc(activeBackend)}</code></div>
          <div><span>版本</span><code>${esc((h.version || '') + (h.codename ? ' · ' + h.codename : ''))}</code></div></div>
        <div class="st-h">添加模型</div>${addModelForm()}
        <div class="st-h">配置编辑</div>
        <div id="cfg-bd" class="st-card" style="font-size:12px;color:var(--tx3)">加载配置…</div>
      </div>`;
      window._mdlList = models;
      el.querySelectorAll('.st-tabs button').forEach(b => b.onclick = () => {
        const t = b.dataset.tab;
        try { localStorage.setItem(TAB_KEY, t); } catch (e) {}
        el.querySelectorAll('.st-tabs button').forEach(x => x.classList.toggle('on', x === b));
        el.querySelectorAll('.st-pane').forEach(p => { p.hidden = p.dataset.pane !== t; });
      });
      const s = document.getElementById('st-msearch');
      s.oninput = () => {
        const q = s.value.trim().toLowerCase();
        el.querySelectorAll('.st-model').forEach(r => { r.hidden = !!q && !r.dataset.q.includes(q); });
      };
      if (typeof loadCfg === 'function') loadCfg();
      if (window.renderPasswordCard) renderPasswordCard();
    } catch (ex) {
      el.innerHTML = `<div style="color:var(--dg)">加载失败: ${esc(String(ex))} <button class="abtn" onclick="loadSet()">重试</button></div>`;
    }
  };
})();
