/* Windows 节点管理：一次性安装命令、上线状态与首张截图。 */
(() => {
  let dialog, timer;
  const el = (tag, text) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  async function api(path = '', options = {}) {
    const response = await fetch('/api/windows/nodes' + path, {
      ...options, headers: {'Content-Type': 'application/json'},
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '请求失败');
    return data;
  }
  window.openWindowsNodes = function () {
    if (dialog?.open) return;
    dialog?.remove();
    dialog = el('dialog');
    dialog.style.cssText = 'width:min(760px,90vw);max-height:85vh;overflow:auto;padding:24px;border:1px solid #777;border-radius:12px;background:var(--bg,#181a20);color:var(--text,#eee)';
    const close = el('button', '关闭');
    close.style.float = 'right';
    close.onclick = () => dialog.close();
    dialog.append(close, el('h3', '添加 Windows'));
    dialog.append(el('p', '① 复制接入命令 → ② 在新电脑运行 → ③ 自动发现电脑并显示桌面。'));
    dialog.append(el('p', '新电脑：右键开始按钮 → 终端（管理员）→ 粘贴命令并回车。电脑名字和登录用户会自动识别。'));
    dialog.append(el('small', '代理使用安装用户可用的最高权限运行；设备凭据由 Windows 为该用户加密保存。'));
    const form = el('form');
    const alias = el('input');
    alias.placeholder = '节点别名，例如 win-office';
    alias.required = true;
    alias.pattern = '[A-Za-z0-9_-]{1,50}';
    alias.value = 'win-' + Date.now().toString(36).slice(-6);
    const url = el('input');
    url.type = 'url';
    url.required = true;
    url.value = location.origin;
    url.setAttribute('aria-label', '新电脑能访问的 LiteCode Web 地址');
    for (const input of [alias, url]) input.style.cssText = 'display:block;box-sizing:border-box;width:100%;margin:10px 0;padding:9px';
    const create = el('button', '生成接入命令');
    create.type = 'submit';
    const message = el('p');
    message.setAttribute('role', 'status');
    const command = el('textarea');
    command.readOnly = true;
    command.rows = 4;
    command.style.cssText = 'width:100%;box-sizing:border-box;display:none;margin:12px 0';
    const copy = el('button', '复制命令');
    copy.type = 'button';
    copy.hidden = true;
    let commandKind = 'join';
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(command.value);
        message.textContent = commandKind === 'join' ? '已复制。接入码十分钟内有效，仅供这台新电脑使用。' : '已复制' + (commandKind === 'update' ? '更新' : '卸载') + '命令。请在安装时的 Windows 用户下，用管理员 PowerShell 执行。';
      } catch (_) {
        command.focus(); command.select();
        message.textContent = '命令已选中，请按 Ctrl+C 复制。';
      }
    };
    const advanced = el('details');
    advanced.append(el('summary', '接入地址（自动填写，连接失败时检查）'), url);
    const transport = el('p');
    const showTransport = () => {
      transport.textContent = url.value.startsWith('https://') ? '连接使用 HTTPS 加密。' : '当前地址使用 HTTP，凭据和截图在网络上传输时未加密。长期使用请配置 HTTPS。';
    };
    url.addEventListener('input', showTransport); showTransport();
    form.append(el('label', '电脑代号（已自动生成，可改）'), alias, advanced, transport, create);
    form.onsubmit = async event => {
      event.preventDefault();
      create.disabled = true;
      try {
        if (['localhost', '127.0.0.1', '[::1]'].includes(new URL(url.value).hostname)) {
          advanced.open = true;
          throw new Error('当前地址只指向本机，请填写新电脑能够访问的 LiteCode Web 地址。');
        }
        const data = await api('', {method: 'POST', body: JSON.stringify({alias: alias.value.trim(), server_url: url.value.trim()})});
        commandKind = 'join';
        command.value = data.command;
        command.style.display = 'block';
        copy.hidden = false;
        message.textContent = '命令十分钟内有效。安装完成后，下面会显示在线状态与首张截图；安装中断可在同一电脑重试原命令。';
        await refresh();
      } catch (error) { message.textContent = error.message; }
      finally { create.disabled = false; }
    };
    const list = el('div');
    dialog.append(form, command, copy, message, el('h4', '已登记节点'), list);
    dialog.append(el('small', '完全卸载会删除本节点代理、启动任务、独立运行环境和本机凭据；已有软件、历史对话和任务截图保留。仅撤销授权不会删除本机文件。'));
    const states = {awaiting_join: '等待执行命令', expired: '接入码已过期', installing: '安装中', online: '在线', offline: '离线', error: '安装或连接失败', revoked: '已撤销', uninstall_pending: '等待电脑接收卸载', uninstalling: '正在卸载，等待清理回执', uninstall_failed: '卸载未完成', uninstalled: '已完全卸载'};
    const stages = {enrolled: '已发现电脑', python: '准备独立 Python 环境', packages: '安装桌面组件', agent: '启动桌面代理'};
    const taskStates = {queued: '等待电脑领取', running: '电脑正在执行', returned: '动作已返回，业务结果请看截图验收', failed: '动作失败', cancelled: '已取消派发，已执行的动作无法撤回', unconfirmed: '未确认是否完成，请先检查桌面，勿重复发送'};
    const actions = {screenshot: '截图检查', initialize: '初始化', windows: '查看窗口', click: '点击', right_click: '右键', hover: '悬停', click_text: '点击文字', scroll: '滚动', drag: '拖动', hotkey: '快捷键', type_text: '输入文字', type_secret: '输入已保存凭据', open_url: '打开网页', wechat_login: '微信登录', wechat_status: '检查微信', ocr: '识别文字', clipboard: '读取剪贴板', focus_window: '切换窗口', close_window: '关闭窗口'};
    const busy = new Set();
    const removeArmed = new Set();
    let refreshing = false;
    async function refresh() {
      if (refreshing) return;
      refreshing = true;
      try {
        const data = await api();
        list.replaceChildren();
        if (!data.nodes.length) list.append(el('p', '尚未登记 Windows 节点。'));
        for (const node of data.nodes) {
          const row = el('div');
          row.style.cssText = 'border-top:1px solid #777;padding:12px 0';
          row.append(el('strong', node.alias + ' · ' + (states[node.state] || node.state)));
          if (node.computer) row.append(el('p', '电脑：' + node.computer + ' · 登录用户：' + (node.user || '待识别')));
          if (node.state === 'installing') row.append(el('p', stages[node.stage] || '正在准备，请稍候'));
          if (node.state === 'online') {
            const state = node.desktop_ready ? '桌面已检查，可在对话中操作。' : node.desktop_state === 'desktop_unavailable' ? '电脑已连接，但桌面锁定或未登录，请在电脑上解锁后点“检查桌面”。' : '连接正常，桌面状态需要检查。';
            row.append(el('p', state));
          }
          if (node.update_state) {
            const version = node.agent_version ? '代理 ' + node.agent_version.slice(0, 8) + ' · 组件 ' + (node.assets_version || '未知').slice(0, 8) : '旧版代理（不上报版本）';
            const updates = {current: '已是最新', updating: '发现新版本，空闲时自动更新', legacy: '需要在电脑上执行一次“本机更新命令”，之后可自动更新'};
            row.append(el('p', '运行版本：' + version + ' · ' + updates[node.update_state]));
          }
          if (node.last_seen) row.append(el('small', '最近连接：' + new Date(node.last_seen * 1000).toLocaleString()));
          if (node.last_task) row.append(el('p', '最近动作：' + (actions[node.last_task.action] || node.last_task.action) + ' · ' + (taskStates[node.last_task.state] || node.last_task.state)));
          if (node.error) row.append(el('p', node.error));
          if (node.screenshot) {
            const link = el('a', '查看最近截图');
            link.href = '/api/media?path=' + encodeURIComponent(node.screenshot);
            link.target = '_blank'; link.rel = 'noopener';
            const preview = el('img');
            preview.src = link.href; preview.alt = '最近回传的桌面截图'; preview.loading = 'lazy';
            preview.style.cssText = 'display:block;max-width:100%;max-height:220px;margin:8px 0;border-radius:6px';
            link.append(preview);
            row.append(link);
          }
          if (node.state === 'online') {
            const check = el('button', '检查桌面');
            check.disabled = busy.has(node.id);
            check.onclick = async () => {
              busy.add(node.id); check.disabled = true;
              message.textContent = '正在检查桌面，等待电脑回传截图…';
              try {
                const result = await api('/' + node.id + '/check', {method: 'POST'});
                message.textContent = result.ok ? '截图已返回，请查看电脑卡片。' : (result.error || '桌面暂不可操作');
              } catch (error) { message.textContent = error.message; }
              finally { busy.delete(node.id); await refresh(); }
            };
            row.append(check);
            const chat = el('button', '在对话中操作');
            chat.onclick = () => {
              const input = document.getElementById('mi');
              if (!input) return;
              dialog.close(); input.value = '请查看 ' + node.alias + ' 当前桌面'; input.dispatchEvent(new Event('input')); input.focus();
            };
            row.append(chat);
          }
          if (node.state !== 'revoked' && node.state !== 'uninstalled') {
            if (!['awaiting_join', 'expired', 'uninstall_pending', 'uninstalling'].includes(node.state)) {
              const uninstall = el('button', '完全卸载');
              uninstall.onclick = async () => {
                uninstall.disabled = true;
                try {
                  await api('/' + node.id + '/uninstall', {method: 'POST'});
                  message.textContent = '已请求完全卸载。离线电脑会等待上线；收到本机清理回执前不会显示成功。';
                  await refresh();
                } catch (error) { message.textContent = error.message; uninstall.disabled = false; }
              };
              row.append(uninstall);
            }
            const revoke = el('button', '撤销接入');
            revoke.style.marginLeft = '16px';
            revoke.onclick = async () => {
              revoke.disabled = true;
              try { await api('/' + encodeURIComponent(node.id), {method: 'DELETE'}); await refresh(); }
              catch (error) { message.textContent = error.message; revoke.disabled = false; }
            };
            row.append(revoke);
          }
          if (!['awaiting_join', 'expired', 'uninstalled'].includes(node.state)) {
            const local = el('button', '本机卸载命令');
            local.onclick = async () => {
              try {
                const data = await api('/' + node.id + '/uninstall-command');
                commandKind = 'uninstall'; command.value = data.command;
                command.style.display = 'block'; copy.hidden = false;
                message.textContent = '请复制上方卸载命令，在安装时的 Windows 用户下，用管理员 PowerShell 执行。';
              } catch (error) { message.textContent = error.message; }
            };
            row.append(local);
          }
          if (node.update_state === 'legacy') {
            const update = el('button', '本机更新命令');
            update.onclick = async () => {
              try {
                const data = await api('/' + node.id + '/update-command');
                commandKind = 'update'; command.value = data.command;
                command.style.display = 'block'; copy.hidden = false;
                message.textContent = '请复制上方更新命令，在安装时的 Windows 用户下，用管理员 PowerShell 执行。';
              } catch (error) { message.textContent = error.message; }
            };
            row.append(update);
          }
          if (['revoked', 'uninstalled', 'expired'].includes(node.state)) {
            // 列表每 3 秒重建，二次确认状态放在 removeArmed 里，避免刷新后丢失
            const remove = el('button', removeArmed.has(node.id) ? '确认删除' : '删除记录');
            remove.style.marginLeft = '16px';
            remove.onclick = async () => {
              if (!removeArmed.has(node.id)) {
                removeArmed.add(node.id);
                setTimeout(() => { removeArmed.delete(node.id); refresh(); }, 5000);
                message.textContent = node.state === 'revoked'
                  ? '再点一次“确认删除”。删除后不能再从这里获取本机卸载命令，电脑上的文件需另行清理。'
                  : '再点一次“确认删除”，该记录将从列表移除，别名可重新使用。';
                await refresh();
                return;
              }
              remove.disabled = true;
              try {
                await api('/' + node.id + '/remove', {method: 'POST'});
                removeArmed.delete(node.id);
                message.textContent = '已删除 ' + node.alias + ' 的记录。';
                await refresh();
              } catch (error) { message.textContent = error.message; remove.disabled = false; }
            };
            row.append(remove);
          }
          list.append(row);
        }
      } catch (error) { message.textContent = error.message; }
      finally { refreshing = false; }
    }
    dialog.addEventListener('close', () => { clearInterval(timer); command.value = ''; });
    document.body.append(dialog);
    dialog.showModal();
    refresh();
    timer = setInterval(refresh, 3000);
  };
})();
