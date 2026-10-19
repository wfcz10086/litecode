// chat_msg_actions.js — 消息操作: 卡片确认 / 定位截断 / 路径预览卡 / 复制·朗读·重试·继续·编辑 [原 chat_core.js 拆分 2/5]
/* [P54+] 卡片式确认 — 替代 confirm()，按 memory 规则不用 prompt/confirm 弹窗 */
function confirmCard(title,desc,confirmText='继续'){
  return new Promise(resolve=>{
    const m=document.createElement('div');m.className='cfm-mask';
    m.innerHTML=`<div class="cfm"><h3>⚠ ${esc(title)}</h3><p>${esc(desc).replace(/\n/g,'<br>')}</p><div class="cfm-act"><button class="cancel">取消</button><button class="primary">${esc(confirmText)}</button></div></div>`;
    document.body.appendChild(m);
    const close=v=>{m.remove();resolve(v);};
    m.querySelector('.cancel').onclick=()=>close(false);
    m.querySelector('.primary').onclick=()=>close(true);
    m.onclick=e=>{if(e.target===m)close(false);};
    document.addEventListener('keydown',function h(e){if(e.key==='Escape'){close(false);document.removeEventListener('keydown',h);}});
  });
}

/* [P54+] 提取消息纯文本（剥 markdown / reasoning / tool 等）*/
function _msgExtractText(wrap){
  const mb=wrap.querySelector('.mb');
  if(!mb)return wrap.textContent||'';
  /* 找 markdown 渲染区(子级 .mb) 优先，没有再用 bubble 本身 textContent */
  const mdInner=mb.querySelector('.mb');
  let t='';
  if(mdInner){t=mdInner.textContent||'';}
  else{
    /* user msg 直接放在 bubble textContent；过滤工具栏文字 */
    const clone=mb.cloneNode(true);
    clone.querySelectorAll('.mac,.rsb,.tg,.dv,.ub,.lmh').forEach(n=>n.remove());
    t=clone.textContent||'';
  }
  return t.trim();
}

/* [P54+] 找当前消息在 session 里的索引（按 role + content 前缀宽松匹配）
 * [BUG-FIX 2026-05-21] 流式 AI 消息含推理块/工具 chip/预览卡, _msgExtractText
 * 提取的 textContent 跟 server 端纯文本 content 完全不同 → norm 匹配失败 → -1
 * → "未在 session 定位" 报错. 现加 DOM 位置 fallback: 找 wrap 是同 role 第几条,
 *   在 msgs 数组里找第 N 条同 role msg. 文本匹配失败时兜底. */
function _normForMatch(s){return (s||'').replace(/\s+/g,'').slice(0,40);}
async function _findMsgIdx(wrap){
  if(!cur)return -1;
  const isUser=wrap.classList.contains('user');
  const role=isUser?'user':'assistant';
  let msgs=[];
  try{
    const r=await fetch(`/api/sessions/${cur}`);
    const d=await r.json();
    msgs=d.messages||[];
  }catch(e){return -1;}
  /* 策略 0 [perf 2026-10 分段渲染]: 渲染时打在块上的全局下标 (data-midx / data-midx-end) + 内容指纹 data-mkey,
   * 与完整列表逐条核对 role 和指纹, 一致才用 — 不受"只渲染了最近一段"影响, 也不会被同文消息串错 */
  const tag=isUser?wrap.dataset.midx:wrap.dataset.midxEnd;
  if(tag!==undefined&&tag!==''){
    const ti=+tag;
    if(msgs[ti]&&msgs[ti].role===role&&_lcKey(msgs[ti])===wrap.dataset.mkey)return ti;
  }
  /* 策略 1: 文本前 40 字符匹配 (准确但易失效) */
  const norm=_normForMatch(_msgExtractText(wrap));
  if(norm){
    for(let i=msgs.length-1;i>=0;i--){
      if(msgs[i].role!==role)continue;
      let c=msgs[i].content||'';
      if(Array.isArray(c))c=c.filter(p=>p&&p.type==='text').map(p=>p.text||'').join('\n');
      if(typeof c!=='string')c=String(c);
      if(_normForMatch(c)===norm)return i;
    }
  }
  /* 策略 2 (fallback): DOM 里同 role 的第 N 条 → 对应 msgs 里同 role 的第 N 条 */
  const domSel=isUser?'#msgs .msg.user':'#msgs .msg.ai';
  const domList=Array.from(document.querySelectorAll(domSel));
  const domIdx=domList.indexOf(wrap);
  if(domIdx>=0){
    // [BUG-FIX 2026-05-22] 之前直接数 role 的第 N 个 → 错位:
    //   server 一轮 user 后含 tool_call/tool/final 多个 assistant msg, 但 DOM 只 1 个 .msg.ai 块
    //   DOM .msg.ai 第 1 个 → 错位返回 msgs 数组第 1 个 assistant (可能是上一轮 final),
    //   而不是当前轮 final → msgRetry 找上一条 user 拿到上上轮 user → 重生成上一轮.
    // 修: DOM .msg.ai/.msg.user 在 server msgs 里都跟 user msg 1:1 配对.
    //   找第 (domIdx+1) 个 user msg, 然后:
    //     - 当前是 .msg.user → 返该 user msg idx
    //     - 当前是 .msg.ai → 返该 user 后到下个 user 前的最后一个 assistant idx
    /* [perf 2026-10] 分段渲染时 DOM 第 0 个 user 不是会话第 0 个: 加上片段之前的 user 数 */
    const userBase=_lcUserBase(msgs);
    let userSeen=0, targetUserIdx=-1;
    for(let i=0;i<msgs.length;i++){
      if(msgs[i].role==='user'){
        if(userSeen===domIdx+userBase){ targetUserIdx=i; break; }
        userSeen++;
      }
    }
    if(targetUserIdx<0) return -1;
    if(role==='user') return targetUserIdx;
    // ai: 找 targetUserIdx 之后到下个 user 之前 的最后一个 assistant
    let lastAsst=-1;
    for(let i=targetUserIdx+1;i<msgs.length;i++){
      if(msgs[i].role==='user') break;
      if(msgs[i].role==='assistant') lastAsst=i;
    }
    return lastAsst>=0 ? lastAsst : targetUserIdx;
  }
  return -1;
}

/* [P54+] 截断 session 到 keep 条 */
async function _truncateSession(keep){
  if(!cur)return null;
  const r=await fetch(`/api/sessions/${cur}/truncate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keep})});
  return await r.json();
}

/* [P54+] 扫消息文本里的绝对路径 → 渲染预览块 */
const _PV_EXT_IMG = /\.(png|jpe?g|gif|webp|bmp|svg|ico)$/i;
const _PV_EXT_VID = /\.(mp4|mov|webm)$/i;
const _PV_EXT_AUD = /\.(mp3|wav|ogg)$/i;
const _PV_EXT_PDF = /\.pdf$/i;
const _PV_EXT_TXT = /\.(md|txt|json|csv|tsv|log|yaml|yml|py|sh|js|ts|html|css)$/i;
const _PV_EXT_OFFICE = /\.(pptx?|docx?|xlsx?|odt|odp|ods)$/i;
/* [Q15 2026-07-24] 扩宽路径匹配 — /tmp/pptx_plugin_out /tmp/cad_plugin_out
 * /tmp/{任何}_plugin_out 都算合法工件路径, 支持 plugin 自建输出目录 */
const _PV_PATH_RE = /(?:^|[\s`"'(\[])(\/(?:tmp\/(?:openclaw_workspace|litecode_workspace|[\w-]+_plugin_out|[\w-]+_out)|root\/\.litecode|home\/[\w-]+\/\.litecode)\/[\w./_\-一-鿿]+\.\w{2,6})/g;

async function _scanAndPreview(container, rawText){
  if (!rawText || !container) return;
  const seen = new Set();
  const paths = [];
  let m;
  _PV_PATH_RE.lastIndex = 0;
  while ((m = _PV_PATH_RE.exec(rawText)) !== null){
    const p = m[1];
    if (!seen.has(p)) { seen.add(p); paths.push(p); }
    if (paths.length >= 6) break;
  }
  if (!paths.length) return;
  for (const p of paths){
    const card = document.createElement('div');
    card.className = 'pv-card';
    card.style.cssText = 'margin:8px 0;border:1px solid var(--bd);border-radius:6px;overflow:hidden;background:var(--bg1)';
    const name = p.split('/').pop();
    const header = document.createElement('div');
    header.style.cssText = 'padding:6px 10px;background:var(--sf2);font-size:11px;font-family:var(--mono);color:var(--tx2);display:flex;justify-content:space-between;align-items:center;cursor:pointer;user-select:none';
    let icon = '📄';
    if (_PV_EXT_IMG.test(name)) icon = '🖼';
    else if (_PV_EXT_VID.test(name)) icon = '🎬';
    else if (_PV_EXT_AUD.test(name)) icon = '🔊';
    else if (_PV_EXT_PDF.test(name)) icon = '📕';
    // [C 2026-05-22] 图片/视频卡默认展开 (用户最常想直接看图, 不用再点开)
    // 文本/PDF 类默认收起 (避免长文本撑爆消息列表)
    const _autoOpen = _PV_EXT_IMG.test(name) || _PV_EXT_VID.test(name);
    header.innerHTML = `<span><span class="arr" style="display:inline-block;transition:transform .15s${_autoOpen?';transform:rotate(90deg)':''}">&#x25BA;</span> ${icon} <span style="color:var(--ac)">${esc(name)}</span></span><span style="display:flex;gap:6px;align-items:center"><a href="/api/media?path=${encodeURIComponent(p)}" target="_blank" style="color:var(--tx3);font-size:10px;text-decoration:none" title="新标签打开">↗</a><a href="/api/media?path=${encodeURIComponent(p)}" download="${esc(name)}" style="color:var(--ac2);font-size:10px;text-decoration:none;font-weight:600" title="下载">↓</a></span>`;
    const body = document.createElement('div');
    body.style.cssText = `display:${_autoOpen?'block':'none'};padding:8px 10px;max-height:480px;overflow:auto`;
    header.onclick = async (e) => {
      if (e.target.tagName === 'A') return;
      const open = body.style.display !== 'none';
      body.style.display = open ? 'none' : 'block';
      const arr = header.querySelector('.arr');
      if (arr) arr.style.transform = open ? '' : 'rotate(90deg)';
      if (open || body._loaded) return;
      body._loaded = true;
      if (_PV_EXT_IMG.test(name)){
        body.innerHTML = _lcImgHtml(p, name, 480);  /* [perf] 缩略图 + lazy, 点击看原图 */
      } else if (_PV_EXT_VID.test(name)){
        body.innerHTML = `<video controls style="max-width:100%;border-radius:4px"><source src="/api/media?path=${encodeURIComponent(p)}"></video>`;
      } else if (_PV_EXT_AUD.test(name)){
        body.innerHTML = `<audio controls style="width:100%"><source src="/api/media?path=${encodeURIComponent(p)}"></audio>`;
      } else if (_PV_EXT_PDF.test(name)){
        body.innerHTML = `<iframe src="/api/media?path=${encodeURIComponent(p)}" style="width:100%;height:460px;border:none"></iframe>`;
      } else if (_PV_EXT_TXT.test(name)){
        try{
          const r = await fetch('/api/preview?path=' + encodeURIComponent(p));
          const d = await r.json();
          if (d.type === 'text'){
            const ext = (d.ext||'').toLowerCase();
            if (ext === '.md'){
              const div = document.createElement('div');div.className='mb';
              try{div.innerHTML = window.lcSafeMd(d.content||'');}catch(_){div.textContent = d.content||'';}
              body.appendChild(div);
            } else {
              const pre = document.createElement('pre');pre.style.cssText='margin:0;font-family:var(--mono);font-size:12px;white-space:pre-wrap';
              pre.textContent = d.content||'';body.appendChild(pre);
            }
          } else if (d.type === 'too_large'){
            body.innerHTML = `<div style="color:var(--wn);font-size:11px">文件 ${_fmtSz(d.size)} 过大, 不内联预览。<a href="/api/media?path=${encodeURIComponent(p)}" target="_blank">下载</a></div>`;
          } else if (d.detail){
            body.innerHTML = `<div style="color:var(--dg);font-size:11px">${esc(d.detail)}</div>`;
          } else {
            body.innerHTML = `<div style="color:var(--tx3);font-size:11px">类型 ${esc(d.type||'?')} 暂不支持预览</div>`;
          }
        }catch(ex){
          body.innerHTML = `<div style="color:var(--dg);font-size:11px">预览失败: ${esc(String(ex))}</div>`;
        }
      } else if (_PV_EXT_OFFICE.test(name)){
        const _oIcon = name.match(/\.(pptx?|odp)$/i)?'📊':name.match(/\.(docx?|odt)$/i)?'📝':'📈';
        const _oid = 'op-' + Math.random().toString(36).slice(2);
        body.innerHTML = `<div id="${_oid}" style="padding:16px;text-align:center;color:var(--tx3);font-size:12px">🔄 转换 PDF 预览中（首次约 5-15 秒）...</div>`;
        fetch('/api/preview_office?path=' + encodeURIComponent(p), {method:'HEAD'}).then(r => {
          const od = document.getElementById(_oid); if(!od) return;
          if(r.ok){
            od.outerHTML = `<iframe src="/api/preview_office?path=${encodeURIComponent(p)}" style="width:100%;height:420px;border:none"></iframe>`;
          } else {
            const tip = r.status===503?'LibreOffice 未装（需重 build 镜像）':`预览失败 (${r.status})`;
            od.innerHTML = `<div style="font-size:36px;line-height:1">${_oIcon}</div><div style="font-size:12px;margin:8px 0;color:var(--tx)">${tip}</div><a href="/api/media?path=${encodeURIComponent(p)}" download="${esc(name)}" style="color:var(--ac2);text-decoration:none">↓ 下载文件</a>`;
          }
        }).catch(e => { const od=document.getElementById(_oid); if(od) od.innerHTML=`<div style="color:var(--dg)">错误: ${esc(String(e))}</div>`; });
      } else {
        body.innerHTML = `<div style="color:var(--tx3);font-size:11px">${esc(name)} 不支持内联预览, <a href="/api/media?path=${encodeURIComponent(p)}" download="${esc(name)}" style="color:var(--ac2)">↓ 下载</a></div>`;
      }
    };
    card.appendChild(header);card.appendChild(body);
    container.appendChild(card);
    // [C 2026-05-22] autoOpen 时立即填充图片/视频 (跳过 onclick 的"先 toggle 再 load"逻辑)
    if (_autoOpen) {
      body._loaded = true;
      if (_PV_EXT_IMG.test(name)){
        body.innerHTML = _lcImgHtml(p, name, 480);  /* [perf] 缩略图 + lazy, 点击看原图 */
      } else if (_PV_EXT_VID.test(name)){
        body.innerHTML = `<video controls style="max-width:100%;border-radius:4px"><source src="/api/media?path=${encodeURIComponent(p)}"></video>`;
      }
    }
  }
  /* [download-panel 2026-07] 聚合下载入口 —
   * 用户以前得从正文里逐段翻附件路径, 现在 ≥1 个附件就在容器末尾追加一条
   * "📎 本轮产物 (N)" 栏, 一键下载全部文件. 幂等: 已存在则跳过, 防重复渲染. */
  if (paths.length >= 1 && !container.querySelector('.pv-download-panel')){
    const panel = document.createElement('div');
    panel.className = 'pv-download-panel';
    panel.style.cssText = 'margin:12px 0 4px;padding:8px 12px;border:1px solid var(--bd);border-radius:6px;background:var(--sf2);font-size:12px';
    const _icon = (nm) => {
      if (_PV_EXT_IMG.test(nm)) return '🖼';
      if (_PV_EXT_VID.test(nm)) return '🎬';
      if (_PV_EXT_AUD.test(nm)) return '🔊';
      if (_PV_EXT_PDF.test(nm)) return '📕';
      if (_PV_EXT_OFFICE.test(nm)) {
        if (/\.(pptx?|odp)$/i.test(nm)) return '📊';
        if (/\.(docx?|odt)$/i.test(nm)) return '📝';
        if (/\.(xlsx?|ods|csv)$/i.test(nm)) return '📈';
      }
      return '📄';
    };
    const _rows = paths.map(p => {
      const nm = p.split('/').pop();
      const u = encodeURIComponent(p);
      return `<div style="display:flex;align-items:center;gap:8px;padding:3px 0;border-top:1px dashed var(--bd)">
        <span style="font-size:13px">${_icon(nm)}</span>
        <span style="flex:1;font-family:var(--mono);font-size:11px;color:var(--tx2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(p)}">${esc(nm)}</span>
        <a href="/api/media?path=${u}" target="_blank" style="color:var(--tx3);font-size:10px;text-decoration:none;padding:2px 6px" title="新标签打开">↗</a>
        <a href="/api/media?path=${u}" download="${esc(nm)}" style="color:var(--ac2);font-weight:600;font-size:10px;text-decoration:none;padding:2px 6px;border:1px solid var(--ac2);border-radius:3px" title="下载">↓ 下载</a>
      </div>`;
    }).join('');
    panel.innerHTML = `<div style="font-weight:600;color:var(--ac2);margin-bottom:4px">📎 本轮产物 (${paths.length})</div>${_rows}`;
    container.appendChild(panel);
  }
}

/* [P54+ · Sprint-B Q034] 复制 AI 回复 — 优先服务端 raw markdown 源, 失败回退 textContent */
async function msgCopy(btn){
  const wrap=btn.closest('.msg');if(!wrap)return;
  let t='';
  try{
    const idx=await _findMsgIdx(wrap);
    if(idx>=0 && cur){
      const r=await fetch(`/api/sessions/${cur}`);
      const d=await r.json();
      const m=(d.messages||[])[idx];
      if(m){
        let c=m.content;
        if(Array.isArray(c)) c=c.filter(p=>p&&p.type==='text').map(p=>p.text||'').join('\n');
        if(typeof c==='string' && c.trim()) t=c;
      }
    }
  }catch(e){}
  if(!t) t=_msgExtractText(wrap);
  try{await navigator.clipboard.writeText(t);const o=btn.textContent;btn.textContent='✓ 已复制';setTimeout(()=>btn.textContent=o,1500);}
  catch(e){toast('复制失败: '+e);}
}

/* [P54+] 朗读 / 停止 — speechSynthesis */
let _ttsUtter=null;
function msgTTS(btn){
  const sp=window.speechSynthesis;if(!sp){toast('浏览器不支持 TTS');return;}
  if(sp.speaking||_ttsUtter){sp.cancel();_ttsUtter=null;document.querySelectorAll('.mab.tts.on').forEach(b=>b.classList.remove('on'));return;}
  const wrap=btn.closest('.msg');if(!wrap)return;
  const t=_msgExtractText(wrap);if(!t){toast('无内容可朗读');return;}
  _ttsUtter=new SpeechSynthesisUtterance(t);
  _ttsUtter.lang=/[一-鿿]/.test(t)?'zh-CN':'en-US';
  _ttsUtter.rate=1.05;
  _ttsUtter.onend=_ttsUtter.onerror=()=>{_ttsUtter=null;btn.classList.remove('on');};
  btn.classList.add('on');
  sp.speak(_ttsUtter);
}

/* [P54+] 重试 AI 消息 — 抹除该 AI 消息及之后所有 + 用上一条 user 消息重发 */
async function msgRetry(btn){
  /* [BUG-FIX 2026-05-21] 原版只 truncate(idx) 保留 prevUser, send 又 append 一条相同 user
   * → 历史里出现两条相同 user msg. 现在 truncate 到 prevUser idx, 连 prevUser 一起删,
   * 让 send 重新插, 等价"丢掉这次 AI 回答 + 用相同 user msg 重发". */
  if(streaming){toast('正在生成中, 请稍候');return;}
  const wrap=btn.closest('.msg');if(!wrap)return;
  const idx=await _findMsgIdx(wrap);
  if(idx<0){toast('未在 session 里定位到该消息');return;}
  /* 找上一条 user 消息作为新输入, 同时记录它的 idx */
  const r=await fetch(`/api/sessions/${cur}`);const d=await r.json();const msgs=d.messages||[];
  let prevUser=null, prevUserIdx=-1;
  for(let i=idx-1;i>=0;i--){if(msgs[i].role==='user'){prevUser=msgs[i];prevUserIdx=i;break;}}
  if(!prevUser){toast('找不到上一条 user 消息');return;}
  const drop=msgs.length-prevUserIdx;
  const ok=await confirmCard('重试该消息',`将抹除该 user 消息及之后 共 ${drop} 条消息，并重新生成。\n该操作不可撤销。`,'重新生成');
  if(!ok)return;
  await _truncateSession(prevUserIdx);
  /* [BUG-FIX 2026-05-22] 之前 allMsg[prevUserIdx] 按 server msgs idx 删 DOM 节点,
   * 但 server 一轮含多条 (user/asst/tool/final), DOM 只渲染 1 个 .msg.user + 1 个 .msg.ai 合并块.
   * 用 server idx 当 DOM idx 对不上 → 第 2 轮重试时 allMsg.length=4 但 prevUserIdx=4 → 不删任何 DOM →
   * send() 又 append 一份 user+ai → 用户看着"开了新对话".
   * 修: 算 prevUserIdx 是 server 第几个 user (0-based), 找 DOM 第 N 个 .msg.user, 删它和之后所有 .msg. */
  /* [perf 2026-10] 分段渲染后 DOM 序号 ≠ 全局序号 — 按 data-midx / 片段基数换算, 见 chat_paging.js */
  _lcDomRemoveFrom(msgs,prevUserIdx);
  /* 取 user content 文本作为新输入 */
  let uc=prevUser.content||'';if(Array.isArray(uc))uc=uc.filter(p=>p&&p.type==='text').map(p=>p.text||'').join('\n');
  document.getElementById('mi').value=uc;arx(document.getElementById('mi'));
  send();
}

/* [V9 2026-07-25] 从中断处继续 — 直接把 "请继续" 塞 input 走 send.
 * 服务端 chat 循环拿到 assistant 尾巴 + 新 user "请继续" 就会接着推 (Provider 自然续写). */
async function msgResume(btn){
  if(streaming){toast('正在生成中, 请稍候');return;}
  const wrap=btn.closest('.msg');if(!wrap)return;
  const resume_prompt='请从上次中断的位置继续';
  const mi=document.getElementById('mi');
  if(mi){mi.value=resume_prompt;arx(mi);}
  send();
}

/* [P54+] 编辑 user 消息 */
async function msgEditUser(btn){
  if(streaming){toast('正在生成中, 请稍候');return;}
  const wrap=btn.closest('.msg');if(!wrap)return;
  /* 找 bubble.textContent (排除附件 / 工具栏) */
  const mb=wrap.querySelector('.mb');if(!mb)return;
  /* 取原文 */
  const cloneEl=mb.cloneNode(true);cloneEl.querySelectorAll('.mac,.mfa,.wx-attach').forEach(n=>n.remove());
  const orig=(cloneEl.textContent||'').trim();
  /* 替换 bubble 内容为 textarea */
  const ta=document.createElement('textarea');
  ta.value=orig;
  ta.style.cssText='width:100%;min-height:80px;background:transparent;color:var(--tx);border:none;outline:none;font:inherit;resize:vertical;padding:0';
  /* 备份并清空 */
  const backup=mb.innerHTML;mb.innerHTML='';mb.classList.add('edit');mb.appendChild(ta);
  const bar=document.createElement('div');bar.className='medt-bar';
  bar.innerHTML=`<button class="mab" data-act="cancel">取消</button><button class="mab danger" data-act="save">💾 保存并重发</button>`;
  mb.appendChild(bar);
  ta.focus();ta.setSelectionRange(orig.length,orig.length);
  return new Promise(resolve=>{
    bar.onclick=async e=>{
      const act=e.target.dataset.act;if(!act)return;
      if(act==='cancel'){mb.innerHTML=backup;mb.classList.remove('edit');resolve(false);return;}
      const newTxt=ta.value.trim();
      if(!newTxt){toast('内容为空');return;}
      const idx=await _findMsgIdx(wrap);
      if(idx<0){toast('未在 session 里定位');return;}
      const r=await fetch(`/api/sessions/${cur}`);const d=await r.json();const msgs=d.messages||[];
      const drop=msgs.length-idx;
      const ok=await confirmCard('保存并重发',`将抹除该 user 消息及之后 共 ${drop} 条消息，并以新内容重新提问。\n该操作不可撤销。`,'确认重发');
      if(!ok)return;
      await _truncateSession(idx);
      let n=wrap;while(n){const nx=n.nextElementSibling;n.remove();n=nx;}
      if(_lcPage.sid===cur&&idx<_lcPage.start){_lcPage.start=idx;_lcPage.anchor=null;_lcUpdateMoreBar();}
      document.getElementById('mi').value=newTxt;arx(document.getElementById('mi'));
      send();
      resolve(true);
    };
  });
}
