// 项目设置卡片: 新建 / 编辑项目的名称、项目指令、工作目录; 删除项目。嵌入式卡片, 不用 prompt()。
// 由 project_groups.js 通过 ProjectCard.bind({api, refresh, note, moveTo}) 注入依赖。
(function () {
  let deps = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function field(label, input, hint) {
    const f = el('label', 'pc-f');
    f.appendChild(el('span', 'pc-l', label));
    f.appendChild(input);
    if (hint) f.appendChild(el('span', 'pc-h', hint));
    return f;
  }

  function close() { document.getElementById('pc-ov')?.remove(); }

  // p: 已有项目 (编辑) 或 null (新建); moveSid: 新建后要移进去的会话
  async function open(p, moveSid) {
    if (!deps) return;
    close();
    const ov = el('div', 'pc-ov');
    ov.id = 'pc-ov';
    const card = el('div', 'pc-card');
    card.setAttribute('role', 'dialog');
    card.appendChild(el('div', 'pc-t', p ? '项目设置' : '新建项目'));

    const name = el('input', 'pc-in');
    name.name = 'name';
    name.maxLength = 60;
    name.placeholder = '例如: 公司官网改版';
    name.value = p ? p.name : '';
    const ins = el('textarea', 'pc-in pc-ta');
    ins.name = 'instructions';
    ins.maxLength = 4000;
    ins.placeholder = '这个项目里你希望 AI 一直遵守的要求, 例如: 回答用英文; 代码用 TypeScript; 先给方案再动手。';
    const ws = el('input', 'pc-in');
    ws.name = 'workspace';
    ws.placeholder = '/tmp/litecode_workspace/my-project (可不填)';
    ws.value = (p && p.workspace) || '';
    const err = el('div', 'pc-err');

    card.append(
      field('名称', name),
      field('项目指令', ins, '项目里的每个会话都会带上, 改了之后下一条消息生效'),
      field('工作目录', ws, '服务器上的目录, AI 读写项目文件默认在这里'),
      err,
    );

    const row = el('div', 'pc-row');
    if (p) {
      const del = el('button', 'pc-b pc-del', '删除项目');
      del.type = 'button';
      del.addEventListener('click', () => remove(p));
      row.appendChild(del);
    }
    row.appendChild(el('span', 'pc-sp'));
    const cancel = el('button', 'pc-b', '取消');
    cancel.type = 'button';
    cancel.addEventListener('click', close);
    const save = el('button', 'pc-b pc-ok', p ? '保存' : '创建');
    save.type = 'button';
    const form = { name, ins, ws, err, save, pid: '' };   // 一张卡片一个 form, 重试时 pid 还在
    save.addEventListener('click', () => submit(p, moveSid, form));
    row.append(cancel, save);
    card.appendChild(row);

    ov.appendChild(card);
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    ov.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    document.body.appendChild(ov);
    name.focus();

    if (p) {
      try { ins.value = (await deps.api('GET', `/api/projects/${p.project_id}/instructions`)).content || ''; }
      catch (_) {}
    }
  }

  async function submit(p, moveSid, f) {
    const name = f.name.value.trim();
    if (!name) { f.err.textContent = '需要项目名'; f.name.focus(); return; }
    f.save.disabled = true;
    f.err.textContent = '';
    try {
      // 目录校验失败重试时复用已建好的项目, 不重复创建
      let pid = (p && p.project_id) || f.pid;
      if (!pid) pid = f.pid = (await deps.api('POST', '/api/projects', { name, type: 'general' })).project_id;
      await deps.api('PATCH', `/api/projects/${pid}`, { name, workspace: f.ws.value.trim() });
      await deps.api('PUT', `/api/projects/${pid}/instructions`, { content: f.ins.value });
      close();
      if (moveSid) await deps.moveTo(moveSid, pid);
      else { deps.note(p ? '项目已保存' : '项目已创建'); await deps.refresh(); }
    } catch (e) {
      f.err.textContent = e.message;
      f.save.disabled = false;
    }
  }

  async function remove(p) {
    const ok = typeof window.confirmCard === 'function'
      ? await confirmCard('删除项目', `删除「${p.name}」? 里面的会话不会删, 会回到普通会话列表。`, '删除')
      : true;
    if (!ok) return;
    try { await deps.api('DELETE', `/api/projects/${p.project_id}`); deps.note('项目已删除'); }
    catch (e) { deps.note('删除失败: ' + e.message); }
    close();
    await deps.refresh();
  }

  window.ProjectCard = { bind(d) { deps = d; }, open, close };
})();
