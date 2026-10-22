/* share_btn.js — 顶部「🔗 分享」: 给当前对话生成只读分享链接 (后端 /api/sessions/{sid}/share, 阅读页 /share/{token})。
 * 链接免登录可看, 只含对话正文 (不含工具输出、文件、录屏); 默认 7 天有效, 可随时撤销。 */
(function () {
  const TTL = {'1 天': 86400, '7 天': 7 * 86400, '30 天': 30 * 86400};

  function closeCard() { document.getElementById('lcShareCard')?.remove(); }

  async function list(sid) {
    try { return (await (await fetch(`/api/sessions/${sid}/shares`)).json()).shares || []; } catch (e) { return []; }
  }

  async function open() {
    if (!cur) { toast('先打开一个对话'); return; }
    closeCard();
    const sid = cur;
    const card = document.createElement('div');
    card.id = 'lcShareCard';
    card.style.cssText = 'position:fixed;top:56px;right:16px;z-index:9999;width:380px;max-width:calc(100vw - 32px);' +
      'background:var(--bg1);border:1px solid var(--bd2);border-radius:10px;padding:14px;box-shadow:0 8px 30px rgba(0,0,0,.35);font-size:13px';
    card.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <b>🔗 分享这个对话</b><button class="abtn" id="lcShareX" style="padding:0 8px">✕</button></div>
      <div style="color:var(--tx3);font-size:11px;margin-bottom:10px">拿到链接的人免登录只读查看对话正文（不含工具输出、文件、录屏），可随时撤销。</div>
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:10px">有效期
        <select id="lcShareTtl" style="background:var(--bg2);color:var(--tx);border:1px solid var(--bd2);border-radius:6px;padding:2px 6px">
          ${Object.keys(TTL).map(k => `<option ${k === '7 天' ? 'selected' : ''}>${k}</option>`).join('')}</select>
        <button class="abtn" id="lcShareNew" style="background:var(--ac);color:#000;border:none;margin-left:auto">生成链接</button></div>
      <div id="lcShareList"></div>`;
    document.body.appendChild(card);
    card.querySelector('#lcShareX').onclick = closeCard;
    card.querySelector('#lcShareNew').onclick = async () => {
      const ttl = TTL[card.querySelector('#lcShareTtl').value];
      const r = await fetch(`/api/sessions/${sid}/share`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ttl_sec: ttl, read_only: true})});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast('生成失败: ' + (d.detail || r.status)); return; }
      const url = `${location.origin}/share/${d.token}`;
      try { await navigator.clipboard.writeText(url); toast('✅ 分享链接已复制'); } catch (e) { toast('链接已生成'); }
      render(sid);
    };
    render(sid);
  }

  async function render(sid) {
    const box = document.getElementById('lcShareList');
    if (!box) return;
    const items = (await list(sid)).filter(s => !s.revoked && (!s.expires || s.expires * 1000 > Date.now()));
    if (!items.length) { box.innerHTML = '<div style="color:var(--tx3);font-size:11px">还没有有效的分享链接</div>'; return; }
    box.innerHTML = items.map(s => {
      const url = `${location.origin}/share/${s.token}`;
      const exp = s.expires ? new Date(s.expires * 1000).toLocaleString() : '不过期';
      return `<div style="border-top:1px solid var(--bd);padding:8px 0;display:flex;gap:6px;align-items:center">
        <input readonly value="${esc(url)}" style="flex:1;min-width:0;background:var(--bg2);color:var(--tx2);border:1px solid var(--bd);border-radius:6px;padding:3px 6px;font-size:11px">
        <button class="abtn" data-copy="${esc(url)}" style="padding:2px 8px">复制</button>
        <button class="abtn" data-revoke="${esc(s.token)}" style="padding:2px 8px;color:var(--dg)">撤销</button></div>
        <div style="color:var(--tx3);font-size:10px;margin:-4px 0 4px">到期：${esc(exp)}</div>`;
    }).join('');
    box.querySelectorAll('[data-copy]').forEach(b => b.onclick = async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); toast('已复制'); } catch (e) {}
    });
    box.querySelectorAll('[data-revoke]').forEach(b => b.onclick = async () => {
      await fetch(`/api/sessions/${sid}/share/${b.dataset.revoke}`, {method: 'DELETE'});
      toast('已撤销，这个链接打不开了'); render(sid);
    });
  }

  function mount() {
    const exp = document.getElementById('exp-btn');
    if (!exp || document.getElementById('share-btn')) return;
    const b = document.createElement('button');
    b.id = 'share-btn'; b.className = 'abtn'; b.textContent = '🔗 分享'; b.title = '生成只读分享链接';
    b.style.cssText = 'font-size:10px;padding:2px 8px;margin-left:4px';
    b.onclick = open;
    exp.after(b);
  }
  window.LCShare = {open};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
