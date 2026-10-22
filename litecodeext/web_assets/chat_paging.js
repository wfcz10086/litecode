// chat_paging.js — [perf 2026-10] 长会话分段渲染 + 聊天图片懒加载/缩略图
// 打开会话只拉最近 LC_PAGE_SIZE 条 (GET /api/sessions/{sid}?tail=N, 每条带全局 index),
// 顶部 "加载更早的 X 条消息" 按钮 / 滚到顶部附近自动拉上一段 (?before=<首条已渲染 index>&limit=N), 位置不跳。
// 索引正确性: 每个渲染块打 data-midx (首条全局下标) / data-midx-end (合并块末条) / data-mkey (内容指纹),
// 编辑/重试/复制定位 (_findMsgIdx / _lcDomRemoveFrom) 一律以完整列表的全局下标为准, 与已渲染片段无关。
const LC_PAGE_SIZE = 60;
const LC_AUTOLOAD_PX = 240;
const _lcPage = { sid: null, start: 0, anchor: null, loading: false };

/* 消息内容指纹: role + 正文前 40 个非空白字符 (与 _normForMatch 同口径), 用于校验下标未漂移 */
function _lcKey(m) {
  if (!m) return '';
  let c = m.content || '';
  if (Array.isArray(c)) c = c.filter(p => p && p.type === 'text').map(p => p.text || '').join('\n');
  if (typeof c !== 'string') c = String(c);
  return (m.role || '') + '|' + c.replace(/\s+/g, '').slice(0, 40);
}

/* 片段起点不是会话开头时, 跳过开头那几条非 user 消息 (它们属于更早一轮, 和上一段一起渲染,
 * 保证 "连续 assistant 合并" 不会被分段切开)。片段里一条 user 都没有就整段照渲。 */
function _lcSkipLead(msgs, off) {
  if (off <= 0) return 0;
  const i = msgs.findIndex(m => m && m.role === 'user');
  return i < 0 ? 0 : i;
}

/* 渲染一段消息 (base = msgs[0] 的全局下标); before 给定则插到它前面。
 * 末尾 keepReal 个块不加 content-visibility: 首屏贴底的那几条要用真实高度算 scrollHeight,
 * 否则 sbot 按 220px 估算高度滚到"底", 渲染后实际更高 → 停在半路 */
function _lcRenderBlocks(msgs, base, before, keepReal = 0) {
  const el = document.getElementById('msgs');
  const blocks = _mergeConsecutiveAssistants(msgs, base);
  blocks.forEach((m, k) => {
    const r = addMsg(m.role, m.content, m.tools || [], false, m.diffs || [], m.usage, m.source || '', m.wx_user || '',
      m.reasoning || '', m._tool_results || [], m.desktop_steps || [],
      {trace_steps: m.trace_steps || [], turn_error: m.turn_error || null});  /* [trace] 调用链路/错误卡片重绘 */
    const w = r.wrap;
    if (m.steer && typeof _lcMarkSteer === 'function') _lcMarkSteer(w, m.urgent);
    if (m.meta && m.meta.skills && window.LCSkills) LCSkills.tagMsg(w, m.meta.skills);
    w.dataset.midx = m._i0; w.dataset.midxEnd = m._i1; w.dataset.mkey = m._key;
    if (k < blocks.length - keepReal) w.classList.add('lc-cv');
    if (before) el.insertBefore(w, before);
  });
}

/* 首屏渲染 (renderMsgs 调用): msgs 带 index 则视为分段, 否则是完整列表 (老调用方) */
function _lcRenderInitial(msgs) {
  const off = (msgs.length && typeof msgs[0].index === 'number') ? msgs[0].index : 0;
  const skip = _lcSkipLead(msgs, off);
  _lcPage.sid = cur; _lcPage.start = off + skip; _lcPage.loading = false;
  _lcPage.anchor = _lcKey(msgs[skip]);
  _lcRenderBlocks(msgs.slice(skip), off + skip, null, 12);
  _lcUpdateMoreBar();
}

function _lcUpdateMoreBar() {
  const el = document.getElementById('msgs'); if (!el) return;
  let bar = document.getElementById('lc-more');
  const need = _lcPage.sid === cur && _lcPage.start > 0;
  if (!need) { bar && bar.remove(); return; }
  if (!bar) {
    bar = document.createElement('div'); bar.id = 'lc-more'; bar.className = 'lc-more';
    bar.innerHTML = '<button type="button" class="mab"></button>';
    bar.firstChild.onclick = () => lcLoadEarlier();
  }
  if (el.firstChild !== bar) el.insertBefore(bar, el.firstChild);
  bar.firstChild.disabled = _lcPage.loading;
  bar.firstChild.textContent = _lcPage.loading ? '加载中…' : `↑ 加载更早的 ${_lcPage.start} 条消息`;
}

/* 拉上一段并插到顶部; 插入前后按 scrollHeight 差值补偿 scrollTop, 视口内容不跳 */
async function lcLoadEarlier() {
  const sid = cur;
  if (_lcPage.sid !== sid || _lcPage.start <= 0 || _lcPage.loading) return false;
  _lcPage.loading = true; _lcUpdateMoreBar();
  try {
    const start = _lcPage.start, anchor = _lcPage.anchor;
    /* 多拉 1 条 (下标 = start) 做锚点: 会话被重导 / 下标漂移时内容对不上 → 整份重渲, 不错位 */
    const extra = anchor ? 1 : 0;
    const r = await fetch(`/api/sessions/${sid}?before=${start + extra}&limit=${LC_PAGE_SIZE + extra}`);
    const d = await r.json();
    if (cur !== sid || _lcPage.sid !== sid) return false;
    let page = d.messages || [];
    if (extra) {
      const last = page[page.length - 1];
      if (!last || last.index !== start || _lcKey(last) !== anchor) { await _lcReloadFull(sid); return false; }
      page = page.slice(0, -1);
    }
    if (!page.length) { _lcPage.start = 0; return false; }
    const off = page[0].index;
    const skip = _lcSkipLead(page, off);
    const mw = document.getElementById('mw'), el = document.getElementById('msgs');
    const firstMsg = el.querySelector(':scope > .msg');
    const h0 = mw.scrollHeight, t0 = mw.scrollTop;
    _lcRenderBlocks(page.slice(skip), off + skip, firstMsg);
    _lcPage.start = off + skip; _lcPage.anchor = _lcKey(page[skip]);
    mw.scrollTop = t0 + (mw.scrollHeight - h0);
    return true;
  } catch (e) {
    typeof toast === 'function' && toast('加载更早消息失败: ' + e);
    return false;
  } finally {
    if (_lcPage.sid === sid) { _lcPage.loading = false; _lcUpdateMoreBar(); }
  }
}

/* 兜底: 下标对不上时整份重渲 (少见: server 会话重导后条数变了) */
async function _lcReloadFull(sid) {
  const d = await (await fetch(`/api/sessions/${sid}`)).json();
  if (cur !== sid) return;
  renderMsgs(d.messages || []); sbot(true);
  typeof toast === 'function' && toast('会话内容已变化, 已重新加载全部消息');
}

/* 已渲染片段起点之前有多少条 user 消息 (按完整列表 msgs 数) — DOM 序号换算成全局序号用 */
function _lcUserBase(msgs) {
  if (_lcPage.sid !== cur) return 0;
  let n = 0;
  for (let i = 0; i < Math.min(_lcPage.start, msgs.length); i++) if (msgs[i].role === 'user') n++;
  return n;
}

/* 截断到全局下标 idx (该 user 消息及之后全删) 后同步 DOM: 优先按 data-midx 精确定位,
 * 没打标的 (流式新追加的) 按 user 序号 - 片段基数 定位; idx 在片段之前 → 清空已渲染消息并把起点挪到 idx */
function _lcDomRemoveFrom(msgs, idx) {
  let target = document.querySelector(`#msgs > .msg[data-midx="${idx}"]`);
  if (!target && _lcPage.sid === cur && idx < _lcPage.start) {
    document.querySelectorAll('#msgs > .msg').forEach(n => n.remove());
    _lcPage.start = idx; _lcPage.anchor = null; _lcUpdateMoreBar();
    return;
  }
  if (!target) {
    let ord = 0;
    for (let i = 0; i < idx; i++) if (msgs[i].role === 'user') ord++;
    target = document.querySelectorAll('#msgs .msg.user')[ord - _lcUserBase(msgs)];
  }
  if (target) { let n = target; while (n) { const nx = n.nextElementSibling; n.remove(); n = nx; } }
}

/* ── 图片: 懒加载 + 异步解码 + 缩略图 (原图留在 data-full, 点击看原图) ── */
const _LC_THUMB_EXT = /\.(png|jpe?g|webp|bmp)$/i;
function _lcThumbUrl(absPath, cssW) {
  if (!absPath || !/\/uploads\//.test(absPath) || !_LC_THUMB_EXT.test(absPath)) return '';
  const w = (window.devicePixelRatio || 1) > 1.25 ? cssW * 2 : cssW;
  return `/api/thumb?path=${encodeURIComponent(absPath)}&w=${w}`;
}
/* 设置顺序: loading/decoding 先于 src, 保证离屏图不发请求 */
function _lcPrepImg(img, fullSrc, absPath, cssW) {
  img.loading = 'lazy'; img.decoding = 'async';
  img.classList.add('lc-img');
  img.dataset.full = fullSrc;
  const t = _lcThumbUrl(absPath, cssW || 480);
  if (t) { img.classList.add('lc-thumb'); img.dataset.tw = String(cssW || 480); }
  img.src = t || fullSrc;
  return img;
}
function _lcImgHtml(absPath, name, cssW) {
  const full = `/api/media?path=${encodeURIComponent(absPath)}`;
  const t = _lcThumbUrl(absPath, cssW || 480);
  return `<img loading="lazy" decoding="async" class="lc-img lc-pv${t ? ' lc-thumb' : ''}" data-full="${full}"${t ? ` data-tw="${cssW || 480}"` : ''} src="${t || full}" alt="${esc(name)}" title="点击查看原图">`;
}
/* 兜底扫一遍容器里的 <img> (markdown 图片等): 补 lazy/async, /api/media 上传图换缩略图 */
function _lcOptimizeImgs(root) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll('img:not(.lc-img)').forEach(img => {
    const src = img.getAttribute('src') || '';
    const m = src.match(/^\/api\/media\?path=([^&]+)/);
    if (m) { let p = ''; try { p = decodeURIComponent(m[1]); } catch (e) {} _lcPrepImg(img, src, p, 480); }
    else { img.loading = 'lazy'; img.decoding = 'async'; img.classList.add('lc-img'); }
  });
}
/* 缩略图失败 (非 uploads / 403 / 解码失败) → 回退原图; 载入后按真实比例定型 */
document.addEventListener('error', e => {
  const img = e.target;
  if (img && img.tagName === 'IMG' && img.classList.contains('lc-thumb') && img.dataset.full && !img.dataset.fb) {
    img.dataset.fb = 'retry'; img.classList.remove('lc-thumb'); img.src = img.dataset.full;
  }
}, true);
document.addEventListener('load', e => {
  const img = e.target;
  if (!img || img.tagName !== 'IMG' || !img.classList.contains('lc-img') || !img.naturalWidth) return;
  img.style.setProperty('--lc-ar', `${img.naturalWidth}/${img.naturalHeight}`);
  if (img.classList.contains('lc-pv')) {
    const tw = +img.dataset.tw || 0, req = tw && (window.devicePixelRatio || 1) > 1.25 ? tw * 2 : tw;
    const cssW = req ? img.naturalWidth * tw / req : img.naturalWidth;
    if (cssW < 480) img.style.maxWidth = Math.round(cssW) + 'px';
  }
  img.classList.add('lc-loaded');
}, true);

(function () {
  function hook() {
    const mw = document.getElementById('mw'), el = document.getElementById('msgs');
    if (!mw || !el) return;
    /* 滚到顶部附近自动加载上一段 */
    mw.addEventListener('scroll', () => {
      if (mw.scrollTop < LC_AUTOLOAD_PX && _lcPage.sid === cur && _lcPage.start > 0 && !_lcPage.loading) lcLoadEarlier();
    }, { passive: true });
    /* 没有自带 onclick 的消息图片 (预览卡 / markdown) 点击看原图, 沿用 #imgModal */
    el.addEventListener('click', e => {
      const img = e.target.closest && e.target.closest('img.lc-img');
      if (!img || img.onclick || img.closest('a')) return;
      if (typeof _openImg === 'function') _openImg(img.dataset.full || img.src);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', hook); else hook();
})();
