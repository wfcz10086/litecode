/* chat_timeline.js — [trace 2026-10-22] 对话里的调用链路 + 友好报错卡片
 *
 * 1. 时间线每行: 图标(沿用 app.css 的 .tn[data-tool] ::before) + 中文名 + 参数摘要 + 状态 (转圈 / ✓ 用时 / ✗),
 *    点行展开看输入参数、结果摘要、进度说明 (结果里有图片路径时显示缩略图)。
 *    流式: chat_stream.js 每帧调用 LCTimeline.onDelta(bodyEl, delta) —— 处理 task_exec(executing 带 id) /
 *    tool_done / tool_progress / notice; 历史重绘: chat_render.js addMsg 调 decorateHistory。
 * 2. 错误卡片 (整轮级): 标题 + 建议 + [从失败处继续] [重试本轮] [换个模型] [打开设置] [查看详情 ▾ 可复制]。
 *    卡片带 .msg-error class, 流末 chat_stream.js 重建 innerHTML 时会被保留; 按钮全走事件委托, outerHTML 复制后照样能点。
 * 不用 alert/confirm/prompt。SSE 字段见 docs/SSE_CONTRACT.md §7.7 / §10。
 */
(function () {
  const esc_ = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  /* 工具名 → 中文名; 没映射的显示原名。前缀规则在 cnName 里 (browser_* / windows_* / desktop_* / ssh_* / git_*) */
  const CN = {
    web_search: '联网搜索', web_fetch: '读取网页', browser_search: '浏览器搜索', browser_read: '浏览器读取网页',
    browser_act: '浏览器操作', browser_login: '浏览器登录', browser_snapshot: '浏览器截图',
    execute_shell: '执行命令', kill_bg: '结束后台任务', list_bg: '查看后台任务', tail_log: '查看日志',
    read_file: '读取文件', write_file: '写入文件', edit_file: '修改文件', patch_file: '修改文件', edit: '修改文件',
    apply_blocks: '修改文件', edit_symbol: '修改代码', find_files: '查找文件', search_code: '搜索代码',
    find_symbol: '查找符号', locate_symbol: '定位符号', get_tree: '查看目录', project_init: '初始化项目',
    load_skill: '加载技能', list_skills: '查看技能', create_skill: '创建技能',
    vision_ocr: '识图', html_render: '渲染网页',
    ssh_exec: '远程命令', ssh_list_hosts: '查看远程主机', ssh_read_file: '读取远程文件', ssh_write_file: '写入远程文件',
    ssh_str_replace: '修改远程文件', ssh_sync_files: '同步文件到远程', ssh_save_host: '保存远程主机', ssh_remove_host: '删除远程主机',
    windows_desktop: '操控电脑', windows_system: '操控电脑 (系统)', windows_nodes: '电脑节点',
    desktop_exec: '桌面命令', desktop_click: '桌面点击', desktop_type: '桌面输入', desktop_key: '桌面按键', desktop_screenshot: '桌面截图',
    spawn_agent: '子任务', agent_control: '子任务控制', agent_worktree: '子任务工作区',
    stock_quote: '查行情', kb_search: '检索知识库', text_to_speech: '配音', tts: '配音',
    create_timer: '创建定时任务', update_timer: '修改定时任务', delete_timer: '删除定时任务', list_timers: '查看定时任务',
    run_timer_now: '立即运行定时任务', get_timer_history: '定时任务记录',
    create_dag: '创建流水线', update_dag: '修改流水线', delete_dag: '删除流水线', run_dag: '运行流水线', patch_dag_node: '修改流水线节点',
    save_memory: '记住信息', update_profile: '更新画像', update_map: '更新项目地图', self_reflect: '自我检查',
    task_create: '新建待办', task_update: '更新待办', task_list: '查看待办', task_done: '完成任务',
    git_status: 'Git 状态', git_diff: 'Git 差异', git_log: 'Git 日志', git_commit: 'Git 提交', git_checkout: 'Git 切换', git_stash: 'Git 暂存',
    github_search: '搜索 GitHub', github_repo: '查看 GitHub 仓库', github_search_issues: '搜索 GitHub Issues',
    stackoverflow_search: '搜索 StackOverflow', package_search: '搜索软件包', search_code_error: '搜索报错',
    docker_remote_ps: '查看远程容器', docker_remote_inspect: '检查远程容器', docker_remote_recreate: '重建远程容器',
    snapshot_list: '查看快照', snapshot_restore: '恢复快照', enable_tools: '启用工具',
    generate_image: '生成图片', image_gen: '生成图片', comfyui_generate: 'ComfyUI 生图', pptx_render: '生成 PPT', cad_generate: '生成 CAD',
  };
  const PREFIX = [['browser_', '浏览器'], ['windows_', '操控电脑'], ['desktop_', '桌面'], ['ssh_', '远程'], ['git_', 'Git'],
    ['comfyui', 'ComfyUI'], ['kb_', '知识库'], ['docker_', '容器']];
  function cnName(n) {
    n = String(n || '');
    if (CN[n]) return CN[n];
    for (const [p, l] of PREFIX) if (n.startsWith(p)) return l + ' · ' + n.slice(p.length);
    return n;
  }
  function fmtMs(ms) {
    ms = +ms || 0;
    if (ms < 1000) return ms + 'ms';
    const s = ms / 1000;
    if (s < 60) return (s < 10 ? s.toFixed(1) : s.toFixed(0)) + 's';
    return Math.floor(s / 60) + 'm' + Math.round(s % 60) + 's';
  }
  function prettyArgs(a) {
    if (a == null || a === '') return '';
    try { const o = typeof a === 'string' ? JSON.parse(a) : a; return JSON.stringify(o, null, 2); } catch (e) { return String(a); }
  }
  function stripSys(t) { const s = String(t == null ? '' : t); const i = s.indexOf('[SYSTEM'); return i > 0 ? s.slice(0, i).trimEnd() : s; }
  const IMG_RE = /(\/[^\s'"`<>()\[\]]+\.(?:png|jpe?g|gif|webp))/gi;
  function thumbs(text) {
    const seen = new Set(); let html = '';
    for (const m of String(text || '').matchAll(IMG_RE)) {
      const p = m[1]; if (seen.has(p) || seen.size >= 4) continue; seen.add(p);
      const u = '/api/media?path=' + encodeURIComponent(p);
      html += `<a href="${esc_(u)}" target="_blank" rel="noopener"><img class="lct-thumb" loading="lazy" src="${esc_(u)}" alt="${esc_(p.split('/').pop())}" onerror="this.parentElement.remove()"></a>`;
    }
    return html ? `<div class="lct-thumbs">${html}</div>` : '';
  }

  /* ── 单行装饰 ──────────────────────────────────────────── */
  function ensureParts(ti) {
    if (ti.querySelector(':scope > .lct-sub')) return;
    ti.classList.add('lct');
    const arr = document.createElement('span'); arr.className = 'lct-arr'; arr.textContent = '▸';
    ti.insertBefore(arr, ti.firstChild);
    const st = document.createElement('span'); st.className = 'lct-st'; ti.appendChild(st);
    const sub = document.createElement('div'); sub.className = 'lct-sub';
    sub.innerHTML = '<div class="lct-notes"></div><div class="lct-errl"></div><div class="lct-det"></div>';
    ti.appendChild(sub);
  }
  function setName(ti, name) {
    const tn = ti.querySelector(':scope > .tn');
    if (!tn || !name) return;
    tn.dataset.tool = name; tn.title = name;
    const cn = cnName(name);
    if (tn.textContent !== cn) tn.textContent = cn;
  }
  function setStatus(ti, kind, text, title) {
    const st = ti.querySelector(':scope > .lct-st'); if (!st) return;
    st.className = 'lct-st ' + kind;
    st.innerHTML = kind === 'run' ? '<i class="lct-spin"></i>' : esc_(text || '');
    st.title = title || '';
    ti.classList.toggle('lct-ok', kind === 'ok'); ti.classList.toggle('lct-fail', kind === 'fail');
    ti.classList.toggle('lct-run', kind === 'run');
  }
  function setDetail(ti, args, result) {
    const det = ti.querySelector(':scope > .lct-sub > .lct-det'); if (!det) return;
    let h = '';
    const a = prettyArgs(args);
    if (a) h += `<div class="lct-lbl">输入参数</div><div class="lct-pre">${esc_(a.length > 4000 ? a.slice(0, 4000) + '…' : a)}</div>`;
    const r = stripSys(result);
    if (r) h += `<div class="lct-lbl">结果</div><div class="lct-pre">${esc_(r.length > 6000 ? r.slice(0, 6000) + '…' : r)}</div>` + thumbs(r);
    if (!h) h = '<div class="lct-lbl">暂无详情</div>';
    det.innerHTML = h;
  }
  function addNote(ti, text) {
    const box = ti.querySelector(':scope > .lct-sub > .lct-notes'); if (!box || !text) return;
    const d = document.createElement('div'); d.className = 'lct-note'; d.textContent = '↻ ' + text; box.appendChild(d);
    while (box.children.length > 5) box.firstChild.remove();
  }
  function setErr(ti, title, hint) {
    const el = ti.querySelector(':scope > .lct-sub > .lct-errl'); if (!el) return;
    el.innerHTML = title ? `<b>⚠ ${esc_(title)}</b>${hint ? ' · ' + esc_(hint) : ''}` : '';
  }
  /* 结果行 (.tr_ 旧格式 "↳ …") 与展开详情重复, 装饰后去掉: 只去本行之后、下一行 .ti 之前的那条 */
  function dropResultLine(ti) {
    let n = ti.nextElementSibling;
    while (n && !n.classList.contains('ti')) {
      const nx = n.nextElementSibling;
      if (n.classList.contains('tr_')) { n.remove(); return; }
      n = nx;
    }
  }
  function applyDone(ti, step, resultText) {
    ensureParts(ti);
    if (step.name) setName(ti, step.name);
    const ok = step.ok !== false;
    const dur = step.duration_ms != null ? fmtMs(step.duration_ms) : '';
    setStatus(ti, ok ? 'ok' : 'fail', ok ? ('✓' + (dur ? ' ' + dur : '')) : ('✗' + (dur ? ' ' + dur : '')),
      ok ? (dur ? '用时 ' + dur : '成功') : (step.error_title || '失败'));
    if (!ok) setErr(ti, step.error_title || '执行失败', step.error_hint || '');
    if (step.retry_of && !ti.querySelector('.lct-retry')) {
      const t = document.createElement('span'); t.className = 'lct-retry'; t.textContent = '重试';
      t.title = '重新执行了之前失败的同一调用';
      ti.insertBefore(t, ti.querySelector(':scope > .lct-st'));
    }
    (step.notes || []).forEach(n => addNote(ti, n));
    setDetail(ti, ti.dataset.args || '', resultText != null ? resultText : (step.summary || ''));
    dropResultLine(ti);
  }

  function ensureTg(bodyEl) {
    let tg = bodyEl.querySelector('.tg');
    if (!tg) {
      bodyEl.querySelector('.dots')?.remove();
      tg = document.createElement('div'); tg.className = 'tg'; bodyEl.insertBefore(tg, bodyEl.firstChild);
    }
    return tg;
  }
  function findRow(bodyEl, id) {
    if (!id) return null;
    try { return bodyEl.querySelector('.ti[data-tcid="' + CSS.escape(id) + '"]'); } catch (e) { return null; }
  }
  function noticeRow(tg, text, level) {
    const d = document.createElement('div');
    d.className = 'lct-notice' + (level === 'warn' ? ' warn' : '');
    d.textContent = '⟳ ' + text;
    tg.appendChild(d);
  }

  /* ── 流式: chat_stream.js 每帧调用 ─────────────────────── */
  function onDelta(bodyEl, delta) {
    if (!bodyEl || !delta) return;
    const te = delta.task_exec;
    if (te && te.status === 'executing' && te.id && te.key !== 'preparing') {
      /* chat_stream.js 刚为这帧追加了一行 .ti: 取最后一行未装饰的 */
      const rows = bodyEl.querySelectorAll('.tg .ti:not(.lct):not(.steer-ti):not(.ti-prep)');
      const ti = rows[rows.length - 1];
      if (ti && !ti.dataset.tcid) {
        ti.dataset.tcid = te.id;
        if (te.args) ti.dataset.args = String(te.args).slice(0, 4000);
        ensureParts(ti); setName(ti, te.name); setStatus(ti, 'run', '', '执行中');
        setDetail(ti, te.args || '', '');
      }
    }
    const td = delta.tool_done;
    if (td && td.id) {
      const ti = findRow(bodyEl, td.id);
      if (ti) applyDone(ti, td, null);
    }
    const tp = delta.tool_progress;
    if (tp && tp.text) {
      const ti = findRow(bodyEl, tp.id);
      if (ti) {
        addNote(ti, tp.text + (tp.pct != null ? ` (${Math.round(tp.pct)}%)` : ''));
        if (tp.pct != null) setStatus(ti, 'run', '', `进度 ${Math.round(tp.pct)}%`);
      }
    }
    const nt = delta.notice;
    if (nt && nt.text) noticeRow(ensureTg(bodyEl), nt.text, nt.level);
  }

  /* 流结束 (含报错 / 停止): 还在转圈的行改成"未完成" */
  function finalize(bodyEl) {
    bodyEl && bodyEl.querySelectorAll('.ti.lct-run').forEach(ti => setStatus(ti, 'stop', '…', '未完成 (本轮已结束)'));
  }

  /* ── 历史重绘: addMsg 调用 ─────────────────────────────── */
  function decorateHistory(bubble, tools, toolResults, steps) {
    const rows = bubble.querySelectorAll('.tg .ti[data-ti]');
    if (!rows.length && !(steps || []).some(s => s && s.notice)) return;
    steps = Array.isArray(steps) ? steps : [];
    const byId = {}, ordered = [];
    steps.forEach(s => { if (!s || s.notice) return; if (s.id) byId[s.id] = s; ordered.push(s); });
    const haveIds = (toolResults || []).some(r => r && r.id);
    let k = 0;
    rows.forEach(ti => {
      const i = +ti.dataset.ti, t = String(tools[i] || ''), c = t.indexOf(': ');
      const name = c > 0 ? t.slice(0, c) : t, args = c > 0 ? t.slice(c + 2) : '';
      const tr = (toolResults || [])[i] || {};
      const step = (tr.id && byId[tr.id]) || (!haveIds ? ordered[k] : null) || null;
      k++;
      ti.dataset.args = args;
      if (tr.id) ti.dataset.tcid = tr.id;
      ensureParts(ti); setName(ti, name);
      const res = tr.result != null ? String(tr.result) : null;
      if (step) { applyDone(ti, step, res); return; }
      if (res != null && res !== '') {
        const fail = /^ERROR/.test(res);
        applyDone(ti, {ok: !fail, error_title: fail ? '执行失败' : '', error_hint: fail ? stripSys(res).slice(6, 140).trim() : ''}, res);
      } else {
        setStatus(ti, 'none', '', ''); setDetail(ti, args, '');
      }
    });
    const notes = steps.filter(s => s && s.notice);
    if (notes.length) { const tg = ensureTg(bubble); notes.forEach(s => noticeRow(tg, s.notice, s.level)); }
  }

  /* ── 错误卡片 ──────────────────────────────────────────── */
  const ACT_LABEL = {resume: '▶ 从失败处继续', retry: '🔄 重试本轮', switch_model: '🤖 换个模型', open_settings: '⚙ 打开设置', details: '查看详情 ▾'};
  function errorCard(fe, opts) {
    fe = fe || {}; opts = opts || {};
    const acts = (Array.isArray(fe.actions) && fe.actions.length ? fe.actions : ['retry', 'details']).filter(a => ACT_LABEL[a]);
    if (fe.details && !acts.includes('details')) acts.push('details');
    if (opts.partial) acts.unshift('resume');
    const el = document.createElement('div');
    el.className = 'msg-error lc-errcard';
    el.dataset.kind = fe.kind || 'unknown';
    el.innerHTML = `<div class="lce-h">⚠ ${esc_(fe.title || fe.reason || '出错了')}</div>` +
      (fe.hint ? `<div class="lce-hint">${esc_(fe.hint)}</div>` : '') +
      `<div class="lce-btns">${acts.map(a => `<button type="button" class="lce-b${a === 'resume' || a === 'retry' ? ' pri' : ''}" data-lce="${a}">${ACT_LABEL[a]}</button>`).join('')}</div>` +
      (fe.details ? `<div class="lce-det"><div class="lct-pre">${esc_(fe.details)}</div><button type="button" class="lce-b" data-lce="copy">📋 复制</button></div>` : '');
    return el;
  }
  /* 气泡里是否已有可继续的进度 (完成的步骤 / 部分正文) */
  function hasProgress(bodyEl, text) {
    return !!(bodyEl && bodyEl.querySelector('.ti.lct-ok')) || !!String(text || '').trim();
  }
  function showError(bodyEl, fe, text) {
    if (!bodyEl) return null;
    bodyEl.querySelector('.dots')?.remove();
    finalize(bodyEl);
    const card = errorCard(fe, {partial: hasProgress(bodyEl, text)});
    const mac = bodyEl.querySelector(':scope > .mac');
    bodyEl.insertBefore(card, mac || null);
    return card;
  }

  async function onAct(act, btn) {
    const card = btn.closest('.lc-errcard');
    if (act === 'details') { card.classList.toggle('open'); btn.textContent = card.classList.contains('open') ? '收起详情 ▴' : '查看详情 ▾'; return; }
    if (act === 'copy') {
      const t = card.querySelector('.lce-det .lct-pre')?.textContent || '';
      try { await navigator.clipboard.writeText(t); btn.textContent = '✓ 已复制'; }
      catch (e) { const r = document.createRange(); r.selectNodeContents(card.querySelector('.lce-det .lct-pre')); const s = getSelection(); s.removeAllRanges(); s.addRange(r); btn.textContent = '已选中, 按 Ctrl+C'; }
      setTimeout(() => { btn.textContent = '📋 复制'; }, 1600);
      return;
    }
    if (window.streaming) { typeof toast === 'function' && toast('正在生成中, 请稍候'); return; }
    if (act === 'retry') { if (typeof regenerateLast === 'function') regenerateLast(); return; }
    if (act === 'resume') {
      const mi = document.getElementById('mi');
      if (mi) { mi.value = '请从上次失败的地方继续, 已完成的步骤不用重做'; if (typeof arx === 'function') arx(mi); }
      if (typeof send === 'function') send();
      return;
    }
    if (act === 'switch_model') {
      const chip = document.getElementById('msw-chip');
      if (chip) { chip.scrollIntoView({block: 'nearest'}); chip.click(); }
      else if (typeof openP === 'function') openP('settings');
      return;
    }
    if (act === 'open_settings') { if (typeof openP === 'function') openP('settings'); }
  }

  document.addEventListener('click', e => {
    const b = e.target.closest && e.target.closest('.lc-errcard [data-lce]');
    if (b) { e.preventDefault(); onAct(b.dataset.lce, b); return; }
    const ti = e.target.closest && e.target.closest('.ti.lct');
    if (ti && !e.target.closest('.lct-sub, a, button, .targs')) ti.classList.toggle('open');
  });

  window.LCTimeline = {onDelta, finalize, decorateHistory, errorCard, showError, cnName, fmtMs};
})();
