/* skill_market.js — 技能面板「技能市场」页签 + 其它安装方式。
 * 搜索 GET /api/skills/market?q=&source= (Anthropic 官方 / ClawHub / 腾讯 SkillHub), 结果卡片 →「详情」
 * (SKILL.md 预览 + 预扫描) →「安装」POST /api/skills/install {market_id}。
 * 其它方式: 「从 GitHub 仓库安装」(owner/repo[/path] 浏览仓库里的技能, 或整仓 git 安装) /「上传 zip」/「粘贴 SKILL.md」。
 * 安装结果统一由 installResult() 展示: 成功 / 被拒 (block) / 需确认 (warn: 勾选后带 stage_id 再提交)。
 */
(function () {
  const {h, esc, api, scanBox} = window.LCSk;
  const SOURCES = [['all', '全部来源'], ['anthropic', 'Anthropic 官方'], ['clawhub', 'ClawHub'], ['skillhub', '腾讯 SkillHub']];
  let mounted = false, root = null, seq = 0;

  /* 安装结果: resp = POST /api/skills/install 的返回; after() 安装成功后回调 */
  function installResult(box, resp, after) {
    box.innerHTML = '';
    if (resp.error) {
      box.appendChild(h('div', 'sk-err', '❌ ' + resp.error));
      if (resp.scan) box.appendChild(scanBox(resp.scan));
      return;
    }
    (resp.skills || []).forEach(s => {
      const line = h('div', '', null);
      line.innerHTML = `<b>${esc(s.title || s.name)}</b> <span class="sk-name">${esc(s.name)}</span>` +
        (s.conflict === 'upgrade' ? ' <span class="sk-badge">升级</span>' : '');
      box.appendChild(line);
      box.appendChild(scanBox(s.scan));
      if (s.error) box.appendChild(h('div', 'sk-err', s.error));
    });
    (resp.installed || []).forEach(s => box.appendChild(h('div', 'sk-ok',
      `✅ 已安装 ${s.title || s.name}${s.upgraded ? ' (已升级, 旧版已备份)' : ''}`)));
    (resp.skipped || []).forEach(s => box.appendChild(h('div', 'sk-err', `⛔ ${s.name}: ${s.reason}`)));
    if (resp.need_confirm) {
      const c = h('label', 'sk-confirm');
      const cb = h('input'); cb.type = 'checkbox'; cb.className = 'sk-warn-ok';
      c.appendChild(cb); c.appendChild(document.createTextNode('我已查看上面的风险项, 确认安装'));
      const go = h('button', 'sk-btn pri sk-confirm-go', '确认安装'); go.type = 'button';
      go.onclick = async () => {
        if (!cb.checked) { box.appendChild(h('div', 'sk-err', '请先勾选确认')); return; }
        go.disabled = true;
        try {
          installResult(box, await api('/api/skills/install', {method: 'POST', json: {stage_id: resp.stage_id, confirm: true}}), after);
        } catch (e) { box.appendChild(h('div', 'sk-err', '安装失败: ' + e.message)); go.disabled = false; }
      };
      box.appendChild(c); box.appendChild(go);
    }
    if ((resp.installed || []).length && after) after(resp);
  }

  async function doInstall(box, opt, after) {
    box.innerHTML = '<div class="sk-empty">⏳ 下载并安全扫描中…</div>';
    try {
      const r = await fetch('/api/skills/install', opt);
      let d = {};
      try { d = await r.json(); } catch (e) { d = {error: 'HTTP ' + r.status}; }
      if (!r.ok && !d.error) d.error = (typeof d.detail === 'string' ? d.detail : '') || ('HTTP ' + r.status);
      installResult(box, d, after);
    } catch (e) { box.innerHTML = ''; box.appendChild(h('div', 'sk-err', '安装失败: ' + e.message)); }
  }
  const jsonOpt = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const refreshInstalled = () => { if (window.LCSkillsPanel) LCSkillsPanel.load(); };

  function mount(el) {
    if (mounted) return;
    mounted = true; root = el;
    el.innerHTML = '';
    const bar = h('div', 'sk-bar');
    const inp = h('input'); inp.id = 'skm-q'; inp.placeholder = '搜索在线技能, 如 pdf / 小说 / 周报';
    const sel = h('select'); sel.id = 'skm-src';
    SOURCES.forEach(([v, t]) => { const o = h('option', '', t); o.value = v; sel.appendChild(o); });
    const go = h('button', 'sk-btn pri', '搜索'); go.type = 'button'; go.id = 'skm-go';
    bar.appendChild(inp); bar.appendChild(sel); bar.appendChild(go);
    el.appendChild(bar);
    const err = h('div', 'sk-err'); err.id = 'skm-err';
    const list = h('div'); list.id = 'skm-list';
    el.appendChild(err);
    el.appendChild(list);
    go.onclick = () => search(inp.value.trim(), sel.value);
    inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); go.click(); } };
    sel.onchange = () => go.click();
    el.appendChild(otherWays());
    search('', 'all');
  }

  async function search(q, source, repo) {
    const list = root.querySelector('#skm-list'), err = root.querySelector('#skm-err');
    const my = ++seq;
    list.innerHTML = '<div class="sk-empty">⏳ 正在查询在线技能源…</div>';
    err.textContent = '';
    try {
      const p = new URLSearchParams({q: q || '', source: source || 'all'});
      if (repo) p.set('repo', repo);
      const d = await api('/api/skills/market?' + p);
      if (my !== seq) return;
      const es = Object.entries(d.errors || {});
      err.textContent = es.length ? es.map(([k, v]) => `${k}: ${v}`).join('；') : '';
      renderResults(list, d.items || []);
    } catch (e) {
      if (my !== seq) return;
      list.innerHTML = '';
      err.textContent = '查询失败: ' + e.message;
    }
  }

  function renderResults(list, items) {
    list.innerHTML = '';
    if (!items.length) { list.appendChild(h('div', 'sk-empty', '没有结果')); return; }
    const box = h('div', 'sk-group');
    items.forEach(it => box.appendChild(resultCard(it)));
    list.appendChild(box);
  }

  function resultCard(it) {
    const c = h('div', 'sk-card skm-item');
    c.dataset.id = it.id;
    const row = h('div', 'sk-row');
    row.innerHTML = `<span class="sk-title">${esc(it.title || it.name)}</span><span class="sk-name">${esc(it.name)}</span>` +
      `<span class="sk-badge">${esc(it.source_label || it.source)}</span>` +
      (it.version ? `<span class="sk-badge">v${esc(it.version)}</span>` : '') +
      (it.downloads ? `<span class="sk-badge">${it.downloads.toLocaleString()} 次下载</span>` : '') +
      (it.installed ? '<span class="sk-badge installed">已有同名</span>' : '') + '<span class="sk-sp"></span>';
    c.appendChild(row);
    c.appendChild(h('div', 'sk-desc', it.description || ''));
    if (it.author) c.appendChild(h('div', 'sk-files', '作者: ' + it.author));
    const acts = h('div', 'sk-row');
    const bDet = h('button', 'sk-btn', '详情'); bDet.type = 'button';
    const bIns = h('button', 'sk-btn pri skm-install', '安装'); bIns.type = 'button';
    acts.appendChild(bDet); acts.appendChild(bIns);
    if (it.url) { const a = h('a', 'sk-btn', '主页'); a.href = it.url; a.target = '_blank'; a.rel = 'noopener'; a.style.textDecoration = 'none'; acts.appendChild(a); }
    c.appendChild(acts);
    const det = h('div', 'sk-detail'), res = h('div', 'sk-res');
    c.appendChild(det); c.appendChild(res);
    bDet.onclick = async () => {
      if (!det.classList.toggle('on') || det.childNodes.length) return;
      det.textContent = '加载中…';
      try {
        const d = await api('/api/skills/market/' + it.id.split('/').map(encodeURIComponent).join('/'));
        det.textContent = '';
        det.appendChild(scanBox(d.scan));
        if (d.remote_security && Object.keys(d.remote_security).length) {
          det.appendChild(h('div', 'sk-files', '来源站安全检测: ' + Object.entries(d.remote_security).map(([k, v]) => `${k} ${v}`).join(' / ')));
        }
        det.appendChild(h('pre', 'sk-pre', (d.skill_md || '').slice(0, 6000)));
      } catch (e) { det.textContent = '加载失败: ' + e.message; }
    };
    bIns.onclick = () => doInstall(res, jsonOpt({market_id: it.id}), refreshInstalled);
    return c;
  }

  function otherWays() {
    const wrap = h('div');
    const more = h('div', 'sk-more');
    const forms = {};
    [['gh', '从 GitHub 仓库安装'], ['zip', '上传 zip'], ['md', '粘贴 SKILL.md']].forEach(([k, t]) => {
      const b = h('button', 'sk-btn', t); b.type = 'button'; b.id = 'skm-way-' + k;
      b.onclick = () => Object.entries(forms).forEach(([kk, f]) => f.classList.toggle('on', kk === k && !f.classList.contains('on')));
      more.appendChild(b);
    });
    wrap.appendChild(more);

    const gh = h('div', 'sk-form'); forms.gh = gh;
    gh.innerHTML = '<div class="sk-files">填 owner/repo 或 owner/repo/子目录 (也支持 https://github.com/... 链接)</div>';
    const ghIn = h('input'); ghIn.placeholder = 'anthropics/skills'; ghIn.id = 'skm-gh';
    const ghRow = h('div', 'sk-row');
    const ghBrowse = h('button', 'sk-btn', '浏览仓库里的技能'); ghBrowse.type = 'button';
    const ghGit = h('button', 'sk-btn pri', 'git 直接安装'); ghGit.type = 'button';
    const ghRes = h('div', 'sk-res');
    ghRow.appendChild(ghBrowse); ghRow.appendChild(ghGit);
    gh.appendChild(ghIn); gh.appendChild(ghRow); gh.appendChild(ghRes);
    ghBrowse.onclick = () => { if (ghIn.value.trim()) search('', 'all', ghIn.value.trim()); };
    ghGit.onclick = () => {
      let v = ghIn.value.trim();
      if (!v) return;
      if (!/^https:\/\//.test(v)) v = v.replace(/^github\.com\//, '');
      doInstall(ghRes, jsonOpt({git_url: v}), refreshInstalled);
    };

    const zip = h('div', 'sk-form'); forms.zip = zip;
    zip.innerHTML = '<div class="sk-files">zip 里可以是一个或多个技能目录 (各含 SKILL.md), 外面包一层目录也行</div>';
    const zf = h('input'); zf.type = 'file'; zf.accept = '.zip,application/zip'; zf.id = 'skm-zip';
    const zb = h('button', 'sk-btn pri', '上传并安装'); zb.type = 'button'; zb.id = 'skm-zip-go';
    const zr = h('div', 'sk-res');
    zip.appendChild(zf); zip.appendChild(zb); zip.appendChild(zr);
    zb.onclick = () => {
      const f = zf.files && zf.files[0];
      if (!f) { zr.textContent = '先选择 zip 文件'; return; }
      const fd = new FormData(); fd.append('file', f);
      doInstall(zr, {method: 'POST', body: fd}, refreshInstalled);
    };

    const md = h('div', 'sk-form'); forms.md = md;
    const mn = h('input'); mn.placeholder = '技能名 (小写英文, frontmatter 里有 name 可不填)'; mn.id = 'skm-md-name';
    const mt = h('textarea'); mt.placeholder = '---\nname: my-skill\ndescription: 什么时候用这个技能\n---\n\n# 步骤\n...'; mt.id = 'skm-md';
    const mb = h('button', 'sk-btn pri', '安装'); mb.type = 'button'; mb.id = 'skm-md-go';
    const mr = h('div', 'sk-res');
    md.appendChild(mn); md.appendChild(mt); md.appendChild(mb); md.appendChild(mr);
    mb.onclick = () => { if (mt.value.trim()) doInstall(mr, jsonOpt({skill_md: mt.value, name: mn.value.trim()}), refreshInstalled); };

    wrap.appendChild(gh); wrap.appendChild(zip); wrap.appendChild(md);
    return wrap;
  }

  window.LCSkillMarket = {mount, search, installResult};
})();
