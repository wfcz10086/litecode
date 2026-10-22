/* screen_record.js — 录屏模式 (Web 与 Windows 桌面版通用)
 *
 * ⏺ 录屏 (右上角顶栏开关, 只影响当前对话): 开着时每一轮由服务端自动录 LiteCode 这个对话窗口,
 *   结束后回复里出现「🎬 本轮录屏」视频卡片, 可播放 / 下载 mp4 (见 lib/screen_record.py)。
 * 🎥 (手动录屏): 浏览器只录当前这个 LiteCode 标签页, 点停止后上传并转成 mp4, 作为附件放进输入框,
 *   发给 LiteCode 让它看, 或直接下载拿去宣传。
 */
(function () {
  const LCRec = {on: false, mr: null, chunks: [], t0: 0, timer: null, stream: null};
  window.LCRec = LCRec;

  function btnStyle(active, danger) {
    return `background:${active ? (danger ? 'rgba(239,68,68,.15)' : 'var(--ac)') : 'transparent'};` +
      `border:1px solid ${active ? (danger ? 'var(--dg)' : 'var(--ac)') : 'var(--bd2)'};` +
      `color:${active ? (danger ? 'var(--dg)' : '#000') : 'var(--tx2)'};border-radius:12px;padding:2px 10px;` +
      `font-size:10px;cursor:pointer;margin-left:4px;white-space:nowrap`;
  }

  function render() {
    const a = document.getElementById('recAuto'), m = document.getElementById('recMan');
    if (a) {
      a.style.cssText = btnStyle(LCRec.on, true);
      a.textContent = LCRec.on ? '⏺ 录屏中' : '⏺ 录屏';
      a.title = LCRec.on ? '已开启：这个对话每一轮自动录成 mp4（点击关闭）'
                         : '自动录屏：开启后这个对话每一轮自动录成 mp4，结束后可播放、下载';
    }
    if (m) {
      const rec = LCRec.mr && LCRec.mr.state === 'recording';
      m.style.cssText = btnStyle(rec, true);
      m.textContent = rec ? `⏹ ${fmt(Date.now() - LCRec.t0)}` : '🎥';
      m.title = rec ? '停止录屏并转成 mp4' : '手动录屏：录当前这个 LiteCode 窗口，停止后转成 mp4';
    }
  }

  function fmt(ms) {
    const s = Math.floor(ms / 1000);
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }

  async function ensureSession() {
    if (cur) return cur;
    const d = await (await fetch('/api/sessions', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: '录屏'})})).json();
    cur = d.id; sess.unshift({id: d.id, name: d.name, count: 0}); renderSL();
    return cur;
  }

  LCRec.refresh = async function () {
    LCRec.on = false;
    if (cur) {
      try { LCRec.on = !!(await (await fetch(`/api/chat/${cur}/record`)).json()).on; } catch (e) {}
    }
    render();
  };

  LCRec.toggleAuto = async function () {
    const sid = await ensureSession();
    const want = !LCRec.on;
    const r = await fetch(`/api/chat/${sid}/record`, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({on: want})});
    if (!r.ok) { toast('录屏开关失败: ' + r.status); return; }
    LCRec.on = (await r.json()).on;
    render();
    toast(LCRec.on ? '⏺ 已开启自动录屏：从下一轮开始，每轮结束出一个 mp4' : '已关闭自动录屏');
  };

  // 🎥: 先选录什么 —— 当前标签页 (原行为) / 整个窗口或屏幕 (系统选择框里任选, 可带系统声音)
  LCRec.toggleManual = function () {
    if (LCRec.mr && LCRec.mr.state === 'recording') { LCRec.mr.stop(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      toast('这个浏览器不支持录屏（需要 Chrome / Edge，且通过 https 或本机地址访问）'); return;
    }
    document.getElementById('lcRecPick')?.remove();
    const b = document.getElementById('recMan').getBoundingClientRect();
    const card = document.createElement('div'); card.id = 'lcRecPick';
    card.style.cssText = `position:fixed;top:${b.bottom + 6}px;right:${Math.max(8, innerWidth - b.right)}px;z-index:9999;` +
      'background:var(--bg2,var(--sf));border:1px solid var(--bd2);border-radius:14px;padding:8px;min-width:220px;' +
      'box-shadow:0 8px 30px rgba(0,0,0,.25);display:flex;flex-direction:column;gap:6px;font-size:13px';
    const opt = (label, sub, fn) => {
      const o = document.createElement('button');
      o.style.cssText = 'text-align:left;background:var(--sf2,transparent);border:1px solid var(--bd);border-radius:10px;padding:8px 10px;cursor:pointer;color:var(--tx)';
      o.innerHTML = `<div style="font-weight:600">${label}</div><div style="font-size:11px;color:var(--tx3)">${sub}</div>`;
      o.onclick = () => { card.remove(); fn(); };
      card.appendChild(o);
    };
    opt('📑 当前标签页', '只录 LiteCode 这个页面', () => startManual('tab'));
    opt('🖥️ 整个窗口 / 整个屏幕', '在系统弹框里选任意窗口或整屏，可带系统声音', () => startManual('screen'));
    document.body.appendChild(card);
    setTimeout(() => document.addEventListener('click', function off(e) {
      if (!card.contains(e.target)) { card.remove(); document.removeEventListener('click', off); }
    }), 0);
  };

  async function startManual(kind) {
    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia(kind === 'tab'
        ? {video: {displaySurface: 'browser', frameRate: 15}, audio: false,
           preferCurrentTab: true, selfBrowserSurface: 'include', surfaceSwitching: 'exclude',
           monitorTypeSurfaces: 'exclude'}
        : {video: {displaySurface: 'monitor', frameRate: 20}, audio: true, systemAudio: 'include',
           selfBrowserSurface: 'include', surfaceSwitching: 'include', monitorTypeSurfaces: 'include'});
    } catch (e) { toast('已取消录屏'); return; }
    const withAudio = stream.getAudioTracks().length > 0;
    const mime = (withAudio ? ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus'] : [])
      .concat(['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm']).find(t => MediaRecorder.isTypeSupported(t));
    LCRec.chunks = []; LCRec.stream = stream;
    LCRec.mr = new MediaRecorder(stream, {mimeType: mime, videoBitsPerSecond: 3_000_000});
    LCRec.mr.ondataavailable = e => { if (e.data && e.data.size) LCRec.chunks.push(e.data); };
    LCRec.mr.onstop = upload;
    stream.getVideoTracks()[0].addEventListener('ended', () => { if (LCRec.mr.state === 'recording') LCRec.mr.stop(); });
    LCRec.mr.start(1000);
    LCRec.t0 = Date.now();
    LCRec.timer = setInterval(render, 1000);
    render();
    toast(kind === 'tab' ? '🎥 开始录屏（当前标签页），再点一次按钮停止' : '🎥 开始录屏（你选的窗口/屏幕），再点一次按钮停止');
  };

  async function upload() {
    clearInterval(LCRec.timer);
    try { LCRec.stream && LCRec.stream.getTracks().forEach(t => t.stop()); } catch (e) {}
    render();
    const blob = new Blob(LCRec.chunks, {type: 'video/webm'});
    LCRec.chunks = [];
    if (!blob.size) { toast('录屏是空的'); return; }
    const sid = await ensureSession();
    const slot = {name: '录屏.mp4', size: blob.size, path: '', loading: true, progress: 0, error: null};
    _pendFiles.push(slot); _renderPendFiles();
    toast('正在转成 mp4…');
    try {
      const fd = new FormData(); fd.append('file', blob, 'screen.webm');
      const r = await fetch(`/api/screenrec/${sid}`, {method: 'POST', body: fd});
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.status);
      Object.assign(slot, {name: d.name, size: d.size, path: d.path, loading: false});
      _renderPendFiles();
      toast('✅ 录屏已转成 mp4，已放进附件：可直接发给 LiteCode，发出后消息里能播放和下载');
    } catch (e) {
      slot.loading = false; slot.error = String(e.message || e); _renderPendFiles();
      toast('录屏上传失败: ' + slot.error);
    }
  }

  // 🎞 回放: 卡片列出这个对话的所有录屏 (自动录的每轮 + 手动录的), 点开就播
  LCRec.showList = async function () {
    document.getElementById('lcRecList')?.remove();
    if (!cur) { toast('先打开一个对话'); return; }
    let items = [];
    try { items = (await (await fetch(`/api/chat/${cur}/recordings`)).json()).items || []; } catch (e) {}
    const card = document.createElement('div'); card.id = 'lcRecList';
    card.style.cssText = 'position:fixed;right:24px;top:56px;width:min(480px,calc(100vw - 32px));max-height:70vh;overflow:auto;' +
      'background:var(--bg2,var(--sf));border:1px solid var(--bd2);border-radius:14px;padding:12px 14px;z-index:9999;box-shadow:0 8px 30px rgba(0,0,0,.25)';
    const url = p => '/api/media?path=' + encodeURIComponent(p);
    const esc_ = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
    card.innerHTML = `<div style="display:flex;align-items:center;margin-bottom:8px"><b style="flex:1">🎞 本对话录屏（${items.length}）</b>` +
      `<button id="lcRecListX" style="background:none;border:none;color:var(--tx2);cursor:pointer;font-size:16px">✕</button></div>` +
      (items.length ? items.map((it, i) => `<div style="margin-bottom:10px">
        <div style="font-size:12px;color:var(--tx3);margin-bottom:4px">${esc_(it.name)} · ${(it.size / 1048576).toFixed(1)} MB ·
          <a href="${url(it.path)}" download="${esc_(it.name)}" style="color:var(--ac)">下载</a></div>
        <video controls preload="${i ? 'none' : 'metadata'}" src="${url(it.path)}" style="width:100%;border-radius:8px;background:#000"></video></div>`).join('')
        : '<div style="color:var(--tx3);font-size:13px">还没有录屏。点「⏺ 录屏」开启后，之后每一轮都会自动录；或用「🎥」手动录。</div>');
    document.body.appendChild(card);
    card.querySelector('#lcRecListX').onclick = () => card.remove();
  };

  function mount() {
    // 放右上角顶栏, 跟「⬇ 导出 / 🔗 分享」一排 (2026-10-21 用户: 录屏应该在右上角; 原来在输入框下面不好找)
    const anchor = document.getElementById('share-btn') || document.getElementById('exp-btn')
      || document.getElementById('strat') || document.getElementById('thk');
    if (!anchor || document.getElementById('recAuto')) return;
    const a = document.createElement('button'); a.id = 'recAuto'; a.onclick = () => LCRec.toggleAuto();
    const m = document.createElement('button'); m.id = 'recMan'; m.onclick = () => LCRec.toggleManual();
    const v = document.createElement('button'); v.id = 'recList'; v.onclick = () => LCRec.showList();
    v.textContent = '🎞 回放'; v.title = '这个对话的全部录屏：播放 / 下载 mp4'; v.style.cssText = btnStyle(false);
    anchor.after(a); a.after(m); m.after(v);
    render();
    // 切换会话时刷新开关状态
    const _sw = window.swS;
    if (typeof _sw === 'function' && !_sw._lcRec) {
      window.swS = async function (sid) { const r = await _sw.apply(this, arguments); LCRec.refresh(); return r; };
      window.swS._lcRec = true;
    }
    LCRec.refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
