/* skill_create.js — 「新建技能」卡片 (对话区) 与草稿卡片 (技能面板草稿区共用)。
 * `/新建技能 <描述>` 或选择器底部「+ 用一句话新建技能」→ POST /api/skills/create 让模型写 SKILL.md 草稿,
 * 卡片显示 SKILL.md 预览 + 安全扫描结果 + 「试运行」「启用」「编辑」(卡片内 textarea)「丢弃」。
 * 试运行 = 把草稿作为 chip 选中 (本轮强制注入草稿全文, 草稿本身不进索引), 用户输入一个示例任务发送。
 * 启用遇到 warn 级扫描项: 卡片内勾选"我已了解风险"再启用。全程不用 prompt/alert/confirm。
 */
(function () {
  const {h, esc, api, scanBox, armButton} = window.LCSk;

  /* 草稿卡片: draft = {name, title, description, body, scan}; opts.onChange() 在启用/丢弃后回调 */
  function draftCard(draft, opts) {
    opts = opts || {};
    const card = h('div', 'sk-newcard');
    card.dataset.draft = draft.name;
    const head = h('div', 'sk-row');
    head.innerHTML = `<span class="sk-title">${esc(draft.title || draft.name)}</span><span class="sk-name">${esc(draft.name)}</span>` +
      `<span class="sk-badge draft">草稿</span>`;
    card.appendChild(head);
    if (draft.description) card.appendChild(h('div', 'sk-desc', draft.description));
    const pre = h('pre', 'sk-pre', draft.body || '');
    card.appendChild(pre);
    let scan = scanBox(draft.scan);
    card.appendChild(scan);
    const msg = h('div', 'sk-res');
    const editor = h('textarea');
    editor.style.display = 'none';
    editor.value = draft.body || '';
    card.appendChild(editor);
    const row = h('div', 'sk-row');
    const bTry = h('button', 'sk-btn', '试运行');
    const bOn = h('button', 'sk-btn pri', '启用');
    const bEdit = h('button', 'sk-btn', '编辑');
    const bDrop = h('button', 'sk-btn dng', '丢弃');
    [bTry, bOn, bEdit, bDrop].forEach(b => { b.type = 'button'; row.appendChild(b); });
    card.appendChild(row);
    card.appendChild(msg);
    const setMsg = (cls, text) => { msg.className = 'sk-res ' + cls; msg.textContent = text; };
    const lock = on => [bTry, bOn, bEdit, bDrop].forEach(b => b.disabled = on);

    bTry.onclick = () => {
      if (!window.LCSkills) return;
      LCSkills.add({name: draft.name, title: draft.title, draft: true});
      if (typeof closeP === 'function') closeP('skills');
      const t = document.getElementById('mi');
      if (t) { t.focus(); t.placeholder = '输入一个示例任务, 用草稿技能试运行一次…'; }
      setMsg('sk-ok', '已在输入框上方选中草稿, 输入一个示例任务发送即可试运行');
    };
    bEdit.onclick = async () => {
      if (editor.style.display === 'none') {
        editor.style.display = 'block'; pre.style.display = 'none'; bEdit.textContent = '保存';
        editor.focus();
        return;
      }
      lock(true);
      try {
        const d = await api(`/api/skills/drafts/${encodeURIComponent(draft.name)}`, {method: 'PUT', json: {skill_md: editor.value}});
        draft = Object.assign(draft, d.draft);
        pre.textContent = draft.body; editor.style.display = 'none'; pre.style.display = 'block'; bEdit.textContent = '编辑';
        const ns = scanBox(draft.scan); scan.replaceWith(ns); scan = ns;
        setMsg('sk-ok', '已保存');
      } catch (e) { setMsg('sk-err', '保存失败: ' + e.message); }
      lock(false);
    };
    async function activate(confirm) {
      lock(true);
      try {
        const d = await api(`/api/skills/drafts/${encodeURIComponent(draft.name)}/activate`, {method: 'POST', json: {confirm: !!confirm}});
        if (d.installed && d.installed.length) {
          setMsg('sk-ok', `✅ 已启用「${draft.title || draft.name}」, 之后对话会自动匹配, 也可用 / 选择`);
          row.remove();
          if (opts.onChange) opts.onChange();
          return;
        }
        if (d.need_confirm) {
          setMsg('', '');
          const c = h('label', 'sk-confirm');
          const cb = h('input'); cb.type = 'checkbox';
          c.appendChild(cb); c.appendChild(document.createTextNode('我已了解上面的风险项, 仍要启用'));
          const go = h('button', 'sk-btn pri', '确认启用'); go.type = 'button';
          go.onclick = () => { if (!cb.checked) { setMsg('sk-err', '请先勾选确认'); return; } c.remove(); go.remove(); activate(true); };
          msg.appendChild(c); msg.appendChild(go);
          lock(false);
          return;
        }
        const why = (d.skipped || []).map(s => s.reason).join('; ') || '未能启用';
        setMsg('sk-err', why);
      } catch (e) { setMsg('sk-err', '启用失败: ' + e.message); }
      lock(false);
    }
    bOn.onclick = () => activate(false);
    armButton(bDrop, '丢弃', async () => {
      lock(true);
      try {
        await api(`/api/skills/drafts/${encodeURIComponent(draft.name)}/discard`, {method: 'POST'});
        setMsg('', '已丢弃'); row.remove(); pre.remove(); scan.remove();
        if (opts.onChange) opts.onChange();
      } catch (e) { setMsg('sk-err', '丢弃失败: ' + e.message); lock(false); }
    });
    return card;
  }

  /* 对话区卡片: 生成中 → 草稿卡片 */
  async function start(desc) {
    const msgs = document.getElementById('msgs');
    if (!msgs) return;
    document.getElementById('empty')?.remove();
    const wrap = h('div', 'msg ai sk-create-msg');
    const ar = h('div', 'mr', 'AI');
    const mb = h('div', 'mb');
    wrap.appendChild(ar); wrap.appendChild(mb);
    const card = h('div', 'sk-newcard');
    card.innerHTML = `<div class="sk-row"><span class="sk-title">🧩 新建技能</span></div><div class="sk-desc">${esc(desc)}</div>`;
    const st = h('div', 'sk-res', '⏳ 正在让模型写 SKILL.md 草稿… (约 10-60 秒)');
    card.appendChild(st);
    mb.appendChild(card);
    msgs.appendChild(wrap);
    if (typeof sbot === 'function') sbot(true);
    try {
      const d = await api('/api/skills/create', {method: 'POST', json: {description: desc}});
      card.replaceWith(draftCard(d.draft, {onChange: () => window.LCSkillsPanel && LCSkillsPanel.refreshDot()}));
      if (window.LCSkillsPanel) LCSkillsPanel.refreshDot();
    } catch (e) {
      st.className = 'sk-res sk-err';
      st.textContent = '生成失败: ' + e.message;
    }
    if (typeof sbot === 'function') sbot(true);
  }

  window.LCSkillCreate = {start, draftCard};
})();
