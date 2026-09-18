// ── 核心聊天引擎 ─────────────────────────────────────────────
// 会话列表 / 消息渲染 / send+SSE 流处理 / 上传 / 项目 / 统计 / 记忆
// [2026-08-25 P2-4] 从 app.js 抽出. 原 app.js 里这段挂在 "noVNC 桌面预览"
// banner 之下, 但那个 banner 只覆盖了紧随其后的 renderVnc() 一个函数,
// 剩余 1200+ 行其实是核心聊天逻辑 —— banner 标错了, 抽出时按内容重新命名.
async function loadSess(){try{const r=await fetch('/api/sessions');sess=await r.json();renderSL();}catch{}}
/* [v1.12 P68] 标星会话: localStorage 存 pinned id 列表 */
function _pinnedSet(){try{return new Set(JSON.parse(localStorage.getItem('lc_pinned_sessions')||'[]'));}catch{return new Set();}}
function _pinnedSave(set){localStorage.setItem('lc_pinned_sessions',JSON.stringify([...set]));}
function pinSess(sid){const s=_pinnedSet();if(s.has(sid))s.delete(sid);else s.add(sid);_pinnedSave(s);renderSL();}
function renderSL(){
  const el=document.getElementById('sl');
  if(!sess.length){el.innerHTML='<div style="padding:14px;font-size:12px;color:var(--tx3);text-align:center">暂无会话</div>';return;}
  /* [v1.12 P66] 搜索过滤 */
  const q=(document.getElementById('sess-search')?.value||'').trim().toLowerCase();
  let arr=q ? sess.filter(s=>(s.name||'').toLowerCase().includes(q)||(s.id||'').toLowerCase().includes(q)) : sess.slice();
  /* [v1.12 P67] 排序 */
  const sortKey=document.getElementById('sess-sort')?.value||'recent';
  if(sortKey==='name'){arr.sort((a,b)=>(a.name||'').localeCompare(b.name||''));}
  else if(sortKey==='count'){arr.sort((a,b)=>(b.count||0)-(a.count||0));}
  /* recent 用后端默认顺序 */
  /* [v1.12 P68] pinned 上提 */
  const pinned=_pinnedSet();
  arr.sort((a,b)=>{const pa=pinned.has(a.id)?1:0,pb=pinned.has(b.id)?1:0;return pb-pa;});
  if(!arr.length){el.innerHTML='<div style="padding:14px;font-size:12px;color:var(--tx3);text-align:center">无匹配</div>';return;}
  // [D 2026-05-22] session item 加最近活跃时间, 双行布局更易扫
  const _fmtAgo = ts => {
    if (!ts) return '';
    const s = Math.max(0, (Date.now()/1000) - ts);
    if (s < 60) return '刚刚';
    if (s < 3600) return Math.floor(s/60)+'分前';
    if (s < 86400) return Math.floor(s/3600)+'h前';
    if (s < 604800) return Math.floor(s/86400)+'d前';
    return new Date(ts*1000).toISOString().slice(5,10);
  };
  el.innerHTML=arr.map(s=>{
    const icon=s.id.startsWith('wx-')?'💬 ':s.id.startsWith('cli-')?'🖥️ ':s.name.startsWith('🔌')?'':'';
    const isPinned=pinned.has(s.id);
    const star=isPinned?'★':'☆';
    const ago=_fmtAgo(s.last_used||s.created);
    return`<div class="si${s.id===cur?' act':''}" onclick="swS('${s.id}')"><div style="flex:1;min-width:0"><div class="sn">${isPinned?'<span style="color:var(--ac);margin-right:2px">★</span>':''}${icon}${esc(s.name)}</div><div class="sm">${s.count}条 · ${ago}</div></div><button class="sdl" onclick="event.stopPropagation();pinSess('${s.id}')" title="标星" style="margin-right:2px;color:${isPinned?'var(--ac)':'var(--tx3)'}">${star}</button><button class="sdl" onclick="event.stopPropagation();delS('${s.id}')">✕</button></div>`;
  }).join('');
  // 滚动到当前 active item
  const act = el.querySelector('.si.act');
  if (act && act.scrollIntoView) try { act.scrollIntoView({block:'nearest',behavior:'instant'}); } catch(e){}
}
/* [D 2026-05-22] 切 session 立即清屏 + loading state, 不再等 fetch 完才反应 */
async function swS(sid){
  if(streaming)return;
  if(sid===cur)return;
  cur=sid;closeSB();
  const s=sess.find(x=>x.id===sid);
  document.getElementById('ct').textContent=s?.name||sid;
  document.getElementById('exp-btn').style.display='inline-flex';
  renderSL();
  // 立即清屏 + loading
  const msgsEl=document.getElementById('msgs');
  if(msgsEl){
    msgsEl.innerHTML='<div style="padding:40px 20px;text-align:center;color:var(--tx3);font-size:12px"><div class="dots" style="justify-content:center;margin-bottom:8px"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>加载会话中…</div>';
  }
  try{
    const r=await fetch(`/api/sessions/${sid}`);
    const d=await r.json();
    if(cur!==sid)return;  // 切换期间又点了别的会话, 丢弃旧 fetch
    renderMsgs(d.messages||[]);
    sbot(true);
  }catch(e){
    if(msgsEl)msgsEl.innerHTML=`<div style="padding:20px;color:var(--dg);font-size:12px">加载失败: ${esc(String(e))}</div>`;
  }
}
/* [v1.12 P64] 双击 #ct 重命名: 替换为 input, Enter 保存, Esc 取消 */
function renameCurSession(){
  if(!cur||streaming)return;
  const ct=document.getElementById('ct');
  if(!ct||ct.querySelector('input'))return;
  const oldName=ct.textContent;
  ct.innerHTML=`<input id="ct-rename-input" value="${esc(oldName)}" style="font-size:14px;padding:2px 6px;border:1px solid var(--ac);background:var(--bg2);color:var(--tx);border-radius:3px;font-family:inherit">`;
  const inp=document.getElementById('ct-rename-input');
  inp.focus();inp.select();
  const cancel=()=>{ct.textContent=oldName;};
  const save=async()=>{
    const newName=inp.value.trim();
    if(!newName||newName===oldName){cancel();return;}
    try{
      await fetch(`/api/sessions/${cur}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:newName})});
      ct.textContent=newName;
      const s=sess.find(x=>x.id===cur);if(s)s.name=newName;
      renderSL();toast('✓ 已重命名');
    }catch(e){cancel();toast('❌ 重命名失败');}
  };
  inp.addEventListener('keydown',e=>{if(e.key==='Enter'){save();}else if(e.key==='Escape'){cancel();}});
  inp.addEventListener('blur',save);
}
async function newChat(){if(streaming)return;const r=await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'New Chat'})});const d=await r.json();cur=d.id;sess.unshift({id:d.id,name:d.name,count:0});renderSL();document.getElementById('msgs').innerHTML='<div id="empty"><div class="elogo">⚡</div><div class="etitle">New Chat</div></div>';document.getElementById('ct').textContent='New Chat';closeSB()}
async function delS(sid){if(!await confirmCard('删除会话','确定要删除此会话? 消息和上下文将永久移除','删除'))return;await fetch(`/api/sessions/${sid}`,{method:'DELETE'});sess=sess.filter(s=>s.id!==sid);renderSL();if(cur===sid){cur=null;document.getElementById('msgs').innerHTML='<div id="empty"><div class="elogo">⚡</div></div>';}}
/* [v12.9] 渲染前合并连续 assistant 消息 —— 多轮 iteration 持久化成多条 assistant 时,
   刷新后显示为多个独立 "AI" 气泡, spawn_agent/工具调用被拆散。合并后同一轮成一个气泡。*/
function _mergeConsecutiveAssistants(msgs){
  const out=[];
  for(const m of (msgs||[])){
    const last=out[out.length-1];
    if(m.role==='assistant' && last && last.role==='assistant' && (last.source||'')===(m.source||'')){
      const joiner=(last.content && m.content)?'\n\n':'';
      last.content=(last.content||'')+joiner+(m.content||'');
      last.tools=[...(last.tools||[]),...(m.tools||[])];
      last.diffs=[...(last.diffs||[]),...(m.diffs||[])];
      last._tool_results=[...(last._tool_results||[]),...(m._tool_results||[])];
      const lr=last.reasoning||'', mr=m.reasoning||'';
      last.reasoning=lr+(lr&&mr?'\n\n':'')+mr;
      if(m.usage) last.usage=m.usage;  /* 用最后一条 usage (含总 tokens) */
      last.ts=m.ts||last.ts;
    }else{
      out.push({...m, tools:[...(m.tools||[])], diffs:[...(m.diffs||[])], _tool_results:[...(m._tool_results||[])]});
    }
  }
  return out;
}
function renderMsgs(msgs){const el=document.getElementById('msgs');el.innerHTML='';if(!msgs.length){el.innerHTML='<div id="empty"><div class="elogo">⚡</div><div class="etitle">LiteCode</div></div>';return}const merged=_mergeConsecutiveAssistants(msgs);merged.forEach(m=>addMsg(m.role,m.content,m.tools||[],false,m.diffs||[],m.usage,m.source||'',m.wx_user||'',m.reasoning||'',m._tool_results||[]));sbot()}
function rdiff(df){return df.split('\n').map(l=>{const cl=l.startsWith('+')&&!l.startsWith('+++')?'da':l.startsWith('-')&&!l.startsWith('---')?'dd_':l.startsWith('@@')?'dm':'';return`<span class="${cl}">${esc(l)}</span>`;}).join('\n')}
/* ── 微信附件解析：从消息文本中提取图片/文件路径 ── */
const _IMG_EXTS=new Set(['jpg','jpeg','png','gif','webp','bmp','svg']);
const _PDF_EXT='pdf';
function _parseWxAttachments(text){
  const items=[];
  // 匹配新格式: 路径: /absolute/path/to/xxx.ext
  const pathRe=/路径:\s*(\S+)/gi;
  let m;
  while((m=pathRe.exec(text))!==null){
    const p=m[1];const ext=(p.split('.').pop()||'').toLowerCase();
    if(_IMG_EXTS.has(ext))items.push({type:'image',path:p});
    else if(['mp4','avi','mov','mkv','webm','flv'].includes(ext))items.push({type:'video',path:p});
    else items.push({type:'file',path:p,ext});
  }
  return items;
}
function _buildAttachNode(att){
  const p=att.path;const fname=p.split('/').pop()||p;
  const apiUrl=`/api/media?path=${encodeURIComponent(p)}`;
  if(att.type==='image'){
    const img=document.createElement('img');img.className='wx-img';img.src=apiUrl;img.alt=fname;
    img.onclick=()=>{const m=document.getElementById('imgModal');document.getElementById('imgModalImg').src=apiUrl;m.classList.add('show');};
    img.onerror=()=>{img.style.display='none';};
    return img;
  }
  if(att.type==='video'){
    const v=document.createElement('video');v.controls=true;v.style.cssText='max-width:280px;max-height:200px;border-radius:var(--rs);border:1px solid var(--bd2)';
    const s=document.createElement('source');s.src=apiUrl;v.appendChild(s);return v;
  }
  // 文件（含PDF）
  const ext=att.ext||'';
  const icon=ext==='pdf'?'📕':['doc','docx'].includes(ext)?'📝':['xls','xlsx'].includes(ext)?'📊':['zip','tar','gz'].includes(ext)?'📦':'📄';
  const a=document.createElement('a');a.className='wx-file';a.href=apiUrl;a.download=fname;a.target='_blank';
  if(ext==='pdf')a.onclick=e=>{e.preventDefault();window.open(apiUrl,'_blank');};
  a.innerHTML=`<span class="wx-file-icon">${icon}</span><div class="wx-file-info"><div class="wx-file-name">${esc(fname)}</div><div class="wx-file-size">${esc(ext.toUpperCase()||'FILE')}</div></div><span class="wx-file-dl">↓</span>`;
  return a;
}
function addMsg(role,content,tools=[],scroll=true,diffs=[],usage=null,source='',wx_user='',reasoning='',toolResults=[]){
  document.getElementById('empty')?.remove();const el=document.getElementById('msgs');
  const wrap=document.createElement('div');wrap.className=`msg ${role==='user'?'user':'ai'}`;
  const rl=document.createElement('div');rl.className='mr';
  const isWx=source==='wechat';
  if(role==='user'){rl.textContent=isWx?(wx_user||'微信用户'):'你';}else{rl.textContent=isWx?'AI (微信)':'AI';}
  if(isWx){const tag=document.createElement('span');tag.style.cssText='font-size:9px;color:var(--ac3);margin-left:4px';tag.textContent='💬';rl.appendChild(tag);}
  wrap.appendChild(rl);
  const bubble=document.createElement('div');bubble.className='mb';
  if(role==='user'){
    if(isWx){
      // 微信用户消息：先提取并渲染附件，再显示纯文本
      const atts=_parseWxAttachments(content||'');
      // 清理掉路径行，只保留实际文字
      /* [v1.4] 清理附件占位文本 — 纳入多模态新占位符 + 复杂压缩包概览新块 */
      let cleanText=(typeof content==='string'?content:'')
        .replace(/\[之前缓冲的附件[^\]]*\]\n?/g,'')
        .replace(/路径:\s*\S+\n?/g,'')
        .replace(/文件名:\s*[^\n]+\n?/g,'')
        .replace(/\[图片已作为多模态输入嵌入本消息[^\]]*\][^\[]*(?=\n\n|$)/gs,'')
        .replace(/请直接描述图片你看到的内容[^\n]*\n?/g,'')
        .replace(/那些工具读的是二进制文件[^\n]*\n?/g,'')
        .replace(/\[压缩包概览\][^\[]*/g,'')
        .replace(/\[类型分布\][^\n]*\n?/g,'')
        .replace(/\[文件清单[^\]]*\][^\[]*/g,'')
        .replace(/\[顶层文件预览\][^\[]*/g,'')
        .replace(/\[(?:图片内容识别|PDF内容预览|Word文档内容预览|Excel表格预览|CSV数据预览|PPT内容预览|文件内容预览|音频信息|视频信息|压缩包内容列表|图片|未知类型[^\]]*|预处理失败)[:\]：][^\[]*(?=\[|$)/gs,'')
        .replace(/---\n?/g,'')
        .replace(/(?:图片|文件|视频)路径:\s*\S+\n?/g,'')
        .replace(/\[收到图片\]\n?/g,'')
        .replace(/\[收到文件:[^\]]*\]\n?/g,'')
        .replace(/\[收到视频\]\n?/g,'')
        .trim();
      if(cleanText)bubble.textContent=cleanText;
      if(atts.length){const ac=document.createElement('div');ac.className='wx-attach';atts.forEach(a=>ac.appendChild(_buildAttachNode(a)));bubble.appendChild(ac);}
    }else{
      /* [multi-upload] 扫描 [file: /path/xxx] — 图片作 <img> 内嵌, 其他做附件卡片。
         content 可能是字符串, 也可能是多模态 array (历史回放时 LLM 接收格式) —
         array 模式下提取 text 部分再扫描, image_url 块作为 _preview 占位图标入 refs */
      let raw='';
      if(Array.isArray(content)){
        content.forEach(p=>{
          if(typeof p==='string')raw+=p;
          else if(p&&p.type==='text')raw+=(p.text||'');
          else if(p&&p.type==='image_url'&&p.image_url&&p.image_url.url){
            /* array 里有 image_url data-url → 直接作 img src 渲染, 不走 refs 流程 */
          }
        });
      }else if(typeof content==='string'){raw=content;}
      else if(content){raw=String(content);}
      const refs=[];const txtOnly=raw.replace(/\[file:\s*([^\]]+?)\]/gi,(_m,p)=>{refs.push(p.trim());return '';}).trim();
      if(txtOnly)bubble.textContent=txtOnly;
      if(refs.length){
        const ac=document.createElement('div');ac.className='mfa';
        refs.forEach(p=>{
          const name=p.split('/').pop()||p;
          const rel=p.replace(/^.*?\/tmp\/openclaw_workspace\//,'');
          const src=`/api/workspace/preview?path=${encodeURIComponent(rel)}`;
          if(_IMG_EXT.test(name)){
            const img=document.createElement('img');img.src=src;img.alt=name;img.onclick=()=>_openImg(src);ac.appendChild(img);
          }else{
            const a=document.createElement('a');a.className='mfc';a.href=`/api/workspace/download?path=${encodeURIComponent(rel)}`;a.target='_blank';
            a.innerHTML=`<span class="mft">${_fileBadge(name)}</span><span class="mfn" title="${esc(name)}">${esc(name)}</span>`;
            ac.appendChild(a);
          }
        });
        bubble.appendChild(ac);
      }
    }
  }else{
    /* [v12.7-fix] 历史消息恢复时, 把持久化的 reasoning 也渲染成折叠块 (默认折叠) */
    if(reasoning){
      const rsEl=document.createElement('div');rsEl.className='rsb';
      const rsid='r'+Date.now()+Math.random().toString(36).slice(2,4);
      rsEl.innerHTML=`<div class="rsh" onclick="this.parentElement.classList.toggle('open')"><span class="arr">&#x25BA;</span><span>💭 推理过程</span><span class="rsi">${reasoning.length} 字符</span></div><div class="rsc" id="${rsid}"></div>`;
      bubble.appendChild(rsEl);
      rsEl.querySelector('#'+rsid).textContent=reasoning;
    }
    if(tools.length){const tg=document.createElement('div');tg.className='tg';tools.forEach((t,_i)=>{const ti=document.createElement('div');ti.className='ti';const c=t.indexOf(': ');if(c>0){const argsStr=t.slice(c+2);if(argsStr.length>TOOL_ARGS_PREVIEW){const aid='ta'+Date.now()+Math.random().toString(36).slice(2,4);ti.innerHTML=`<span class="tn" data-tool="${esc(t.slice(0,c))}">${esc(t.slice(0,c))}</span><span class="targs" id="${aid}" onclick="this.classList.toggle('open')"><span class="arr">&#x25BA;</span><span class="ta tap">${esc(argsStr.slice(0,TOOL_ARGS_PREVIEW))}…</span><span class="tac">${esc(argsStr)}</span></span>`;}else{ti.innerHTML=`<span class="tn" data-tool="${esc(t.slice(0,c))}">${esc(t.slice(0,c))}</span><span class="ta">${esc(argsStr)}</span>`;}}else ti.textContent=t.slice(0,TOOL_ARGS_PREVIEW);tg.appendChild(ti);if(toolResults&&toolResults[_i]&&toolResults[_i].result){const res=toolResults[_i].result;const tr=document.createElement('div');tr.className='tr_';if(res.length>TOOL_RESULT_PREVIEW){const rid='tr'+Date.now()+Math.random().toString(36).slice(2,4);tr.innerHTML=`<span class="trc" id="${rid}" onclick="this.classList.toggle('open')"><span class="arr">&#x25BA;</span><span class="trp">↳ ${esc(res.slice(0,TOOL_RESULT_PREVIEW))}…</span><span class="trf">↳ ${esc(res)}</span></span>`;}else{tr.textContent='↳ '+res;}tg.appendChild(tr);}});
      /* [#16 3段折叠] 工具调用 ≥3 条时收进折叠块; 1-2 条直接展开 (少量场景直看更快) */
      if(tools.length>=3){
        const tgb=document.createElement('div');tgb.className='tgb';
        const toolNames=Array.from(new Set(tools.map(t=>{const c=t.indexOf(': ');return c>0?t.slice(0,c):t;}))).slice(0,3);
        tgb.innerHTML=`<div class="tgh" onclick="this.parentElement.classList.toggle('open')"><span class="arr">&#x25BA;</span><span>🔧 工具调用 <b>${tools.length}</b></span><span class="tgi">${toolNames.join(' · ')}${toolNames.length<tools.length?'…':''}</span></div>`;
        const tgc=document.createElement('div');tgc.className='tgc';tgc.appendChild(tg);tgb.appendChild(tgc);
        bubble.appendChild(tgb);
      }else{bubble.appendChild(tg);}
    }
    diffs.forEach(dv=>{const df=dv.diff||'';if(!df)return;const fname=(dv.filepath||'file').split('/').pop();const adds=(df.match(/^\+[^+]/mg)||[]).length;const dels=(df.match(/^-[^-]/mg)||[]).length;const did='d'+Math.random().toString(36).slice(2,7);const dEl=document.createElement('div');dEl.className='dv';dEl.innerHTML=`<div class="dth" onclick="document.getElementById('${did}').classList.toggle('open')"><span style="color:var(--tx3);font-size:10px">&#x25BA;</span><span style="color:#93c5fd">${esc(fname)}</span><span style="color:var(--ac3)">+${adds}</span><span style="color:var(--dg)">-${dels}</span></div><div class="dbd" id="${did}"><pre><code>${rdiff(df)}</code></pre></div>`;bubble.appendChild(dEl);});
    if(content){
      /* [v1.4] 防御: 多模态 content 可能是 array, 提取 text 部分再 marked */
      let textContent = content;
      if(Array.isArray(content)){
        textContent = content.map(p=>{
          if(typeof p==='string')return p;
          if(p&&p.type==='text')return p.text||'';
          if(p&&p.type==='image_url')return '[图片]';
          return '';
        }).filter(Boolean).join('\n');
      } else if(typeof content !== 'string'){
        textContent = String(content);
      }
      function _renderMd(container,txt){try{container.innerHTML=marked.parse(txt||'');}catch(e){container.textContent=txt;}container.querySelectorAll('pre').forEach(pre=>{const btn=document.createElement('button');btn.className='cpb';btn.textContent='copy';btn.onclick=()=>{navigator.clipboard.writeText(pre.querySelector('code')?.textContent||'');btn.textContent='✓';setTimeout(()=>btn.textContent='copy',1500);};pre.style.position='relative';pre.appendChild(btn);pre.querySelectorAll('code').forEach(c=>{try{hljs.highlightElement(c);}catch(e){}});});_scanAndPreview(container,txt);}
      if(textContent.length>LONG_MSG_THRESHOLD){
        const lmid='lm'+Date.now()+Math.random().toString(36).slice(2,4);
        const lmEl=document.createElement('div');lmEl.className='lmb';
        lmEl.innerHTML=`<div class="lmh"><span class="arr">&#x25BA;</span><span>📜 长文消息 ${textContent.length} 字符</span></div><div class="lmc" id="${lmid}"></div>`;
        const lmh=lmEl.querySelector('.lmh');const lmc=lmEl.querySelector('.lmc');
        lmh.onclick=()=>{lmEl.classList.toggle('open');if(lmEl.classList.contains('open')&&!lmc._rendered){lmc._rendered=true;_renderMd(lmc,textContent);}};
        bubble.appendChild(lmEl);
      }else{
        const md=document.createElement('div');md.className='mb';
        _renderMd(md,textContent);
        bubble.appendChild(md);
      }
    }
    if(usage){const ub=document.createElement('div');ub.className='ub';const f=n=>(n||0).toLocaleString();ub.innerHTML=`<span>P:<b>${f(usage.prompt_tokens)}</b></span><span>C:<b>${f(usage.completion_tokens)}</b></span><span>T:<b>${f(usage.total_tokens)}</b></span>${usage.iterations>1?`<span>${usage.iterations}轮</span>`:''}${usage.elapsed_seconds?`<span>${usage.elapsed_seconds}s</span>`:''}`;bubble.appendChild(ub);}
    /* 流式路径在 send() 末尾会 _scanAndPreview(bodyEl, bodyEl.textContent) — tool 结果里的图片路径在那时被扫到并渲染预览卡。
       但 renderMsgs → addMsg 重放历史时只走 _renderMd 扫 content 文本, tool result 里的路径不会被扫 → 刷新后图没了。
       这里补一次, 仅扫 toolResults, 不重复扫 content 已扫过的部分。 */
    if(toolResults && toolResults.length && window._scanAndPreview){
      const trText = toolResults.map(t => t && t.result ? String(t.result) : '').join('\n');
      if(trText){ try{ _scanAndPreview(bubble, trText); }catch(e){} }
    }
  }
  /* [P54+] 消息底部工具栏 — user: 编辑 / ai: 复制+朗读+重试 */
  if(!isWx){
    const mac=document.createElement('div');mac.className='mac';
    if(role==='user'){
      mac.innerHTML=`<button class="mab" title="编辑（会抹除该消息之后的整段消息串）" onclick="msgEditUser(this)">✏️ 编辑</button>`;
    }else{
      /* [V9 2026-07-25] 中断的 assistant 消息 → 额外显示 ▶ 继续 按钮 */
      const rawStr = (typeof content === 'string' ? content : '');
      const wasInterrupted = /\[任务已被用户中断\]\s*$/.test(rawStr);
      mac.innerHTML=`<button class="mab" title="复制全部" onclick="msgCopy(this)">📋 复制</button>`
                  + `<button class="mab tts" title="朗读 / 停止" onclick="msgTTS(this)">🔊 朗读</button>`
                  + (wasInterrupted?`<button class="mab resume-btn" title="从中断处继续" onclick="msgResume(this)">▶ 继续</button>`:'')
                  + `<button class="mab danger" title="重试（会抹除该消息及之后的所有消息）" onclick="msgRetry(this)">🔄 重试</button>`;
    }
    bubble.appendChild(mac);
  }
  wrap.appendChild(bubble);el.appendChild(wrap);if(scroll)sbot();return{wrap,bubble};
}

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
    let userSeen=0, targetUserIdx=-1;
    for(let i=0;i<msgs.length;i++){
      if(msgs[i].role==='user'){
        if(userSeen===domIdx){ targetUserIdx=i; break; }
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
        body.innerHTML = `<img src="/api/media?path=${encodeURIComponent(p)}" style="max-width:100%;border-radius:4px" alt="${esc(name)}">`;
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
              try{div.innerHTML = marked.parse(d.content||'');}catch(_){div.textContent = d.content||'';}
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
        body.innerHTML = `<img src="/api/media?path=${encodeURIComponent(p)}" style="max-width:100%;border-radius:4px" alt="${esc(name)}">`;
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
  let userOrdinal=0;
  for(let i=0;i<prevUserIdx;i++){if(msgs[i].role==='user')userOrdinal++;}
  const userDoms=document.querySelectorAll('#msgs .msg.user');
  const targetDom=userDoms[userOrdinal];
  if(targetDom){
    let n=targetDom;
    while(n){const nx=n.nextElementSibling;n.remove();n=nx;}
  }
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
      document.getElementById('mi').value=newTxt;arx(document.getElementById('mi'));
      send();
      resolve(true);
    };
  });
}
async function send(){
  if(streaming||_sending)return;const inp=document.getElementById('mi');let msg=inp.value.trim();
  /* [multi-upload] 只要有待发文件也可发 (允许空文字+文件) */
  const ready=_pendFiles.filter(f=>f.path&&!f.loading);
  if(!msg && !ready.length)return;
  if(_pendFiles.some(f=>f.loading)){toast('⏳ 还有文件在上传, 请稍候');return;}
  _sending=true;document.getElementById('sdb').disabled=true;
  if(ready.length){
    const refs=ready.map(f=>`[file: ${f.path}]`).join(' ');
    msg=(msg?msg+'\n':'')+refs;
  }
  _pendFiles.length=0; try{if(window._pendFiles!==_pendFiles) window._pendFiles=_pendFiles;}catch(e){} _renderPendFiles();
  inp.value='';arx(inp);addMsg('user',msg);
  if(!cur){const d=await(await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:msg.slice(0,40)+(msg.length>40?'…':'')})})).json();cur=d.id;sess.unshift({id:d.id,name:d.name,count:0});renderSL();}
  const s=sess.find(x=>x.id===cur);if(s&&s.name==='New Chat'){s.name=msg.slice(0,30)+(msg.length>30?'…':'');renderSL();}
  document.getElementById('ct').textContent=s?.name||cur;
  const uid='b'+Date.now()+Math.random().toString(36).slice(2,4);
  const aw=document.createElement('div');aw.className='msg ai';const ar2=document.createElement('div');ar2.className='mr';ar2.textContent='AI';const ab=document.createElement('div');ab.className='mb';ab.id=uid;ab.innerHTML='<div class="dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';aw.appendChild(ar2);aw.appendChild(ab);document.getElementById('msgs').appendChild(aw);sbot(true);
  const bodyEl=document.getElementById(uid);
  streaming=true;window.streaming=true;document.getElementById('ib').classList.add('vis');
  let ft='',sr_=null,fc=true,sdiffs=[];_lu=null;
  try{
    const resp=await fetch(`/api/chat/${cur}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg, strategist_mode: !!window._stratMode})});
    const reader=resp.body.getReader();_curReader=reader;const dec=new TextDecoder();let buf='';
    /* [audit-p0 #18] 60s idle watchdog: 若连续 60s 无任何 SSE 帧 (data 或 : 心跳) → abort 视为超时 */
    let _sseWatch=setTimeout(()=>{try{reader.cancel('sse_idle_timeout');}catch(_){}}, 60000);
    const _kick=()=>{clearTimeout(_sseWatch);_sseWatch=setTimeout(()=>{try{reader.cancel('sse_idle_timeout');}catch(_){}}, 60000);};
    while(true){
      let done, value;
      try { ({done,value}=await reader.read()); }
      catch(e){
        clearTimeout(_sseWatch);
        bodyEl.querySelector('.dots')?.remove();
        const eb=document.createElement('div');
        eb.className='msg-error';
        eb.style.cssText='padding:10px 12px;border:1px solid var(--dg);border-radius:6px;background:rgba(239,68,68,.08);color:var(--dg);font-size:13px;margin:8px 0';
        const isIdle=String(e).includes('sse_idle_timeout');
        eb.innerHTML=`<div style="font-weight:600;margin-bottom:6px">${isIdle?'⏱ 后端 60s 无响应, 连接断开':'❌ 连接中断'}</div>`+
          `<button class="abtn retry-btn" onclick="regenerateLast()" style="font-size:11px;padding:4px 10px;background:var(--ac);color:#000;border:none;border-radius:4px;cursor:pointer">🔄 重试</button>`;
        bodyEl.appendChild(eb);
        break;
      }
      if(done){clearTimeout(_sseWatch);break;}
      _kick();
      buf+=dec.decode(value,{stream:true});const lines=buf.split('\n');buf=lines.pop();
      for(const line of lines){
        /* [audit-p0 #10] SSE 注释帧 (心跳) 忽略, watchdog 已重置 */
        if(line.startsWith(':'))continue;
        if(!line.startsWith('data: '))continue;
        const raw=line.slice(6);if(raw==='[DONE]')break;let ck;try{ck=JSON.parse(raw);}catch{continue;}
        if(ck.error){bodyEl.innerHTML=`<span style="color:var(--dg)">Error: ${esc(ck.error)}</span>`;break;}
        const delta=ck.choices?.[0]?.delta||{};
        /* [P46 修] LLM 空响应错误事件 → 渲染 ❌ + 🔄 重试按钮 */
        if(delta.error){
          bodyEl.querySelector('.dots')?.remove();
          const er=delta.error;
          const eb=document.createElement('div');
          eb.className='msg-error';
          eb.style.cssText='padding:10px 12px;border:1px solid var(--dg);border-radius:6px;background:rgba(239,68,68,.08);color:var(--dg);font-size:13px;margin:8px 0';
          eb.innerHTML=`<div style="font-weight:600;margin-bottom:6px">❌ ${esc(er.reason||'LLM 返回空')}</div>`+
            (er.retry_hint?`<button class="abtn retry-btn" onclick="regenerateLast()" style="font-size:11px;padding:4px 10px;background:var(--ac);color:#000;border:none;border-radius:4px;cursor:pointer">🔄 重试</button>`:'');
          bodyEl.appendChild(eb);
          sbot();
        }
        for(const key of['task_exec','data_collect','task_analysis']){if(!delta[key])continue;const st=delta[key].status||'',det=delta[key].detail||'',al=delta[key].agent_label||delta[key].agent||'';
          if(st==='executing'&&det){let tg=bodyEl.querySelector('.tg');if(!tg){bodyEl.querySelector('.dots')?.remove();tg=document.createElement('div');tg.className='tg';bodyEl.insertBefore(tg,bodyEl.firstChild);}const ti=document.createElement('div');ti.className='ti';const c=det.indexOf(': ');ti.innerHTML=(al?`<span style="color:var(--ac3);font-weight:600;font-size:11px">[${esc(al)}]</span>`:'')+(c>0?`<span class="tn" data-tool="${esc(det.slice(0,c))}">${esc(det.slice(0,c))}</span><span class="ta">${esc(det.slice(c+2).slice(0,90))}</span>`:`${esc(det.slice(0,90))}`);tg.appendChild(ti);const allTi=tg.querySelectorAll('.ti:not(.hid)');if(allTi.length>4){const h=allTi.length-4;for(let i=0;i<h;i++)allTi[i].classList.add('hid');let fb=tg.querySelector('.tfl');if(!fb){fb=document.createElement('div');fb.className='tfl';tg.insertBefore(fb,tg.firstChild);fb.onclick=()=>{tg.querySelectorAll('.hid').forEach(x=>x.classList.remove('hid'));fb.remove();};}fb.textContent=`... 展开 ${tg.querySelectorAll('.ti.hid').length} 条`;}sbot();}
          else if(st==='done'&&det){const tg=bodyEl.querySelector('.tg');if(tg){const tr=document.createElement('div');tr.className='tr_'+(det.startsWith('[warn]')||det.includes('[STDERR')?' wn':'');tr.textContent='↳ '+det.replace(/\[warn\]\s*/,'').slice(0,80);tg.appendChild(tr);}}}
        if(delta.agent_status){const av=delta.agent_status,al=av.label||'?',ast_=av.status||'',adet=av.detail||'';const icons={pending:'⏳',running:'▶',done:'✅',error:'❌'};const cols={pending:'var(--tx3)',running:'var(--wn)',done:'var(--ac3)',error:'var(--dg)'};let tg=bodyEl.querySelector('.tg');if(!tg){bodyEl.querySelector('.dots')?.remove();tg=document.createElement('div');tg.className='tg';bodyEl.insertBefore(tg,bodyEl.firstChild);}let ar3=tg.querySelector('[data-agent="'+CSS.escape(al)+'"]');if(!ar3){ar3=document.createElement('div');ar3.className='arow';ar3.dataset.agent=al;tg.appendChild(ar3);}ar3.innerHTML=`<span>${icons[ast_]||'●'}</span><span style="color:${cols[ast_]||'var(--tx2)'};font-weight:600;font-family:var(--mono);font-size:12px">${esc(al)}</span><span style="color:var(--tx3);font-size:11px;font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${esc(adet.slice(0,80))}</span>`;sbot();}
        /* [v1.10 B1 fix] orchestrator_step 帧渲染 — DAG/spawn_agent 模式后端发这种帧, 复用 .arow 样式 */
        if(delta.orchestrator_step){
          const o=delta.orchestrator_step, key=o.step_id||o.label||'?', label=o.label||o.step_id||'?',
                st=o.status||'', det=o.detail||'',
                phase=o.phase, output_delta=o.output_delta, elapsed_ms=o.elapsed_ms;
          /* [P44-c] buf 管理 */
          if(phase==='start'){
            _dagNodeOutputBuf[key]='';
          } else if(phase==='chunk' && output_delta){
            const stream=o.stream;
            let buf=(_dagNodeOutputBuf[key]||'')+output_delta;
            if(buf.length>204800) buf=buf.slice(0,204800)+'\n[...截断]';
            _dagNodeOutputBuf[key]=buf;
            /* 若 modal 正显示该 step，实时 append */
            const modal=document.getElementById('dag-node-output-modal');
            if(modal && modal.style.display==='flex'){
              const pre=document.getElementById('dag-node-output-body');
              if(pre && (pre.dataset.stepId===key || pre.dataset.stepLabel===label)){
                if(stream==='stderr'){
                  const span=document.createElement('span');
                  span.style.color='var(--dg, #ef4444)';
                  span.textContent='[stderr] '+output_delta;
                  pre.appendChild(span);
                  pre.appendChild(document.createTextNode('\n'));
                } else {
                  pre.appendChild(document.createTextNode(output_delta));
                }
              }
            }
          } else if(phase==='end' && elapsed_ms!=null){
            _dagNodeOutputBuf[key]=(_dagNodeOutputBuf[key]||'')+'\n\n[完成 · 耗时 '+(elapsed_ms/1000).toFixed(1)+'s]';
          }
          /* 原有状态条渲染（仅在有 status/detail 实质内容时更新，避免 phase=chunk 的空帧刷新） */
          if(st && (det||phase==='start'||phase==='end'||!phase)){
            const icons={pending:'⏳',running:'▶',done:'✅',error:'❌',failed:'❌',skipped:'⏭'};
            const cols={pending:'var(--tx3)',running:'var(--wn)',done:'var(--ac3)',error:'var(--dg)',failed:'var(--dg)',skipped:'var(--tx3)'};
            let tg=bodyEl.querySelector('.tg');
            if(!tg){bodyEl.querySelector('.dots')?.remove();tg=document.createElement('div');tg.className='tg';bodyEl.insertBefore(tg,bodyEl.firstChild);}
            let ar=tg.querySelector('[data-step="'+CSS.escape(key)+'"]');
            if(!ar){ar=document.createElement('div');ar.className='arow';ar.dataset.step=key;tg.appendChild(ar);}
            ar.innerHTML=`<span>${icons[st]||'●'}</span><span style="color:${cols[st]||'var(--tx2)'};font-weight:600;font-family:var(--mono);font-size:12px">${esc(label)}</span><span style="color:var(--tx3);font-size:11px;font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${esc(det.slice(0,80))}</span>`;
          }
          sbot();
        }
        if(delta.diff_view){const dv=delta.diff_view,df=dv.diff||'';if(df){bodyEl.querySelector('.dots')?.remove();const fname=(dv.filepath||'file').split('/').pop();const adds=(df.match(/^\+[^+]/mg)||[]).length;const dels=(df.match(/^-[^-]/mg)||[]).length;const did='d'+Date.now()+Math.random().toString(36).slice(2,4);const dEl=document.createElement('div');dEl.className='dv';dEl.innerHTML=`<div class="dth" onclick="document.getElementById('${did}').classList.toggle('open')"><span style="color:var(--tx3);font-size:10px">&#x25BA;</span><span style="color:#93c5fd">${esc(fname)}</span><span style="color:var(--ac3)">+${adds}</span><span style="color:var(--dg)">-${dels}</span></div><div class="dbd" id="${did}"><pre><code>${rdiff(df)}</code></pre></div>`;bodyEl.appendChild(dEl);sdiffs.push(dEl.outerHTML);sbot();}}
        /* [v12.5] 推理内容折叠显示 — 统一走 delta.reasoning（服务器已归一化所有后端） */
        const _rz=delta.reasoning||'';
        if(_rz){
          bodyEl.querySelector('.dots')?.remove();
          let rsEl=bodyEl.querySelector('.rsb.live');
          if(!rsEl){
            /* 首次：创建折叠块，默认展开以便流式看到思考过程 */
            rsEl=document.createElement('div');
            rsEl.className='rsb live open';  /* live = 正在流 */
            const rsid='r'+Date.now()+Math.random().toString(36).slice(2,4);
            rsEl.innerHTML=`<div class="rsh" onclick="this.parentElement.classList.toggle('open')"><span class="arr">&#x25BA;</span><span>💭 推理过程</span><span class="rsi" data-rsi>思考中...</span></div><div class="rsc tk" id="${rsid}" data-rsc></div>`;
            /* 插入在最前面（工具调用组之前） */
            bodyEl.insertBefore(rsEl,bodyEl.firstChild);
          }
          const rsc=rsEl.querySelector('[data-rsc]');
          if(rsc){
            rsc._raw=(rsc._raw||'')+_rz;
            rsc.textContent=rsc._raw;
            const rsi=rsEl.querySelector('[data-rsi]');
            if(rsi) rsi.textContent=`${rsc._raw.length} 字符`;
          }
          sbot();
        }
        const ct=delta.content||'';if(ct){
          /* content 开始流 = 推理结束：把折叠块收起来，去掉脉冲动画 */
          const lrs=bodyEl.querySelector('.rsb.live');
          if(lrs){lrs.classList.remove('live','open');lrs.querySelector('.rsc')?.classList.remove('tk');}
          if(fc){bodyEl.querySelector('.dots')?.remove();fc=false;sr_=document.createElement('div');sr_.className='sraw';bodyEl.appendChild(sr_);}ft+=ct;if(!_renderTimer){_renderTimer=setTimeout(()=>{try{if(sr_){sr_.innerHTML=marked.parse(ft);sr_.querySelectorAll('pre code').forEach(c=>{if(!c.classList.contains('hljs'))try{hljs.highlightElement(c)}catch(e){}});}}catch(e){}sbot();_renderTimer=null;},200);}
        }
        if(delta.usage)_lu=delta.usage;
        if(delta.panel_refresh){const pr=delta.panel_refresh,ev=pr.event||'',op=pr.op||'',nm=pr.name||'';if(ev==='dag_changed'){if(typeof loadDAGList==='function')loadDAGList();const opLbl={create:'已创建',update:'已更新',delete:'已删除',run:'已启动'}[op]||op;toast(`DAG ${nm} ${opLbl}`);}else if(ev==='timer_changed'){if(typeof loadTimers==='function')loadTimers();const opLbl={create:'已创建',update:'已更新',delete:'已删除',run:'已触发'}[op]||op;toast(`定时器 ${nm} ${opLbl}`);}}
      }
    }
  }catch(ex){
    /* [Q1 2026-07-24] 网络/异常挂了 → 渲染 ❌ 错误块 + 🔄 重试按钮, 复用 regenerateLast() 触发同一条 msg 重发 */
    bodyEl.querySelector('.dots')?.remove();if(sr_){sr_.remove();sr_=null;}
    const eb=document.createElement('div');
    eb.className='msg-error';
    eb.style.cssText='padding:10px 12px;border:1px solid var(--dg);border-radius:6px;background:rgba(239,68,68,.08);color:var(--dg);font-size:13px;margin:8px 0';
    eb.innerHTML=`<div style="font-weight:600;margin-bottom:6px">❌ ${esc(String(ex).slice(0,240))}</div>`+
      `<button class="abtn retry-btn" onclick="regenerateLast()" style="font-size:11px;padding:4px 10px;background:var(--ac);color:#000;border:none;border-radius:4px;cursor:pointer">🔄 重试</button>`;
    bodyEl.appendChild(eb);
  }
  if(_renderTimer){clearTimeout(_renderTimer);_renderTimer=null;}
  bodyEl.querySelector('.dots')?.remove();if(sr_)sr_.remove();ft=ft.replace(/\n{3,}/g,'\n\n').trim();
  /* [v12.5] 保留推理折叠块（最终 HTML 重建时要带上） */
  const rsbEl=bodyEl.querySelector('.rsb');const rsbHtml=rsbEl?rsbEl.outerHTML:'';
  const tgEl=bodyEl.querySelector('.tg');
  /* [P46 修] 保留错误块, 流末重建时不抹掉 ❌ + 重试按钮 */
  const errEls=bodyEl.querySelectorAll('.msg-error');
  let errHtml='';errEls.forEach(e=>errHtml+=e.outerHTML);
  let fh=rsbHtml+(tgEl?tgEl.outerHTML:'');sdiffs.forEach(d=>fh+=d);if(ft)fh+=marked.parse(ft);
  fh+=errHtml;
  if(fh){bodyEl.innerHTML=fh;bodyEl.querySelectorAll('pre').forEach(pre=>{const btn=document.createElement('button');btn.className='cpb';btn.textContent='copy';btn.onclick=()=>{navigator.clipboard.writeText(pre.querySelector('code')?.textContent||'');btn.textContent='✓';setTimeout(()=>btn.textContent='copy',1500);};pre.style.position='relative';pre.appendChild(btn);pre.querySelectorAll('code').forEach(c=>{if(!c.classList.contains('hljs'))hljs.highlightElement(c);const lm=c.className.match(/language-(\w+)/);if(lm&&lm[1]){const lg=document.createElement('span');lg.className='clang';lg.textContent=lm[1];pre.appendChild(lg);}});});}
  else if(!bodyEl.querySelector('.tg')&&!sdiffs.length&&!bodyEl.querySelector('.msg-error')){bodyEl.innerHTML='<span style="color:var(--tx3)">(无响应)</span>';}
  if(_lu){const ub=document.createElement('div');ub.className='ub';const f=n=>(n||0).toLocaleString();const u=_lu;ub.innerHTML=`<span>P:<b>${f(u.prompt_tokens)}</b></span><span>C:<b>${f(u.completion_tokens)}</b></span><span>T:<b>${f(u.total_tokens)}</b></span>${u.iterations>1?`<span>${u.iterations}轮</span>`:''}${u.elapsed_seconds?`<span>${u.elapsed_seconds}s</span>`:''}`;bodyEl.appendChild(ub);}
  /* [P54+] 流式渲染走的是 bodyEl, 没经过 addMsg, 需要手动补 .mac 工具栏 (复制 / 朗读 / 重试) */
  if(!bodyEl.querySelector('.mac')){
    const mac=document.createElement('div');mac.className='mac';
    mac.innerHTML=`<button class="mab" title="复制全部" onclick="msgCopy(this)">📋 复制</button>`
                + `<button class="mab tts" title="朗读 / 停止" onclick="msgTTS(this)">🔊 朗读</button>`
                + `<button class="mab danger" title="重试（会抹除该消息及之后所有消息）" onclick="msgRetry(this)">🔄 重试</button>`;
    bodyEl.appendChild(mac);
  }
  /* [P54+] 同样补预览 — 扫文本里的绝对路径 */
  if(window._scanAndPreview){try{_scanAndPreview(bodyEl, bodyEl.textContent||'');}catch(e){}}
  streaming=false;window.streaming=false;_sending=false;_curReader=null;document.getElementById('sdb').disabled=false;document.getElementById('ib').classList.remove('vis');sbot();
}
function sug(t){document.getElementById('mi').value=t;arx(document.getElementById('mi'));document.getElementById('mi').focus();}
function onK(ev){if(ev.key==='Enter'&&!ev.shiftKey&&!ev.isComposing){ev.preventDefault();send();}}
let _interruptInFlight=false;
async function interrupt(){
  if(!cur)return;
  /* [interrupt-guard 2026-07] 防抖: 用户可能狂点停止,
   * 每次 POST 都会往 _interrupt_flags 塞一次 flag → 下条消息被残留 flag 伪中断. */
  if(_interruptInFlight)return;
  _interruptInFlight=true;
  const ib=document.getElementById('ib');
  if(ib) ib.disabled=true;
  try{
    try{await fetch(`/api/interrupt/${cur}`,{method:'POST'});}catch(e){}
    /* 立即取消 reader 让 stream 循环 break, 复位 streaming/_sending/UI */
    if(_curReader){try{await _curReader.cancel();}catch(e){}_curReader=null;}
    streaming=false;window.streaming=false;_sending=false;
    document.getElementById('sdb').disabled=false;
    ib&&ib.classList.remove('vis');
    toast('⏹ 中断已发送');
  }finally{
    _interruptInFlight=false;
    if(ib) ib.disabled=false;
  }
}
/* [P46 修] 重试上一次用户消息
 * [BUG-FIX 2026-05] 原版只把 user msg 填到输入框就 send, 导致同一条 user 消息被 append 两次
 *   (server 端 history 里出现两条相同的 ✏️ 编辑). 现在先 truncate session 到 user msg 之前,
 *   再让 send 重新插入, 等价于"丢掉上次中断的尾巴 + 重发". */
async function regenerateLast(){
  if(!cur||streaming)return;
  try{
    const r=await fetch(`/api/sessions/${cur}`);
    const d=await r.json();
    const msgs=d.messages||[];
    /* 倒序找最后一条 user msg 的索引 */
    let lastUserIdx=-1;
    for(let i=msgs.length-1;i>=0;i--){
      if(msgs[i].role==='user'){lastUserIdx=i;break;}
    }
    if(lastUserIdx<0){toast('无可重试的消息');return;}
    let uc=msgs[lastUserIdx].content||'';
    if(Array.isArray(uc)) uc=uc.filter(p=>p&&p.type==='text').map(p=>p.text||'').join('\n');
    /* 截断 history 到 user msg 之前 (这条 user msg 一起删, 由 send 重新插入) */
    await _truncateSession(lastUserIdx);
    /* [BUG-FIX 2026-05-22] 同 msgRetry: DOM 删按 user 节点位置, 不按 server msgs idx
     * (server 一轮多条, DOM 只 1 个 .msg.user 块; 用 server idx 当 DOM idx 会少删) */
    let userOrdinal=0;
    for(let i=0;i<lastUserIdx;i++){if(msgs[i].role==='user')userOrdinal++;}
    const userDoms=document.querySelectorAll('#msgs .msg.user');
    const targetDom=userDoms[userOrdinal];
    if(targetDom){
      let n=targetDom;
      while(n){const nx=n.nextElementSibling;n.remove();n=nx;}
    }
    document.getElementById('mi').value=uc;
    arx(document.getElementById('mi'));
    send();
  }catch(e){toast('重试失败: '+e);}
}
/* [multi-upload] 待发送的文件队列 — 上传成功后入列, send() 把路径拼到消息后, 发送后清空 */
let _pendFiles=[];
// [Sprint-A 2026-07-24] 暴露 _pendFiles (let 不上 window); _renderPendFiles 是 function 声明, 已自动上 window
try{window._pendFiles=_pendFiles;}catch(e){}
/* [multi-upload] 上传/多模态限制 — loadSet/loadStats 会从 /api/health 拉来覆盖, 启动时先用默认 */
let _upLimit={max_mb:20,max_images:6,supports_vision:false};
/* [v1.10 P34-d-10 修 401 console error] 改为登录后才拉 /api/health, 避免未登录时 401 噪音 */
function _refreshUploadLimit(){
  fetch('/api/health').then(r=>r.ok?r.json():null).then(d=>{if(d){_upLimit.max_mb=d.max_upload_mb||20;_upLimit.max_images=d.multimodal_max_images||6;_upLimit.supports_vision=!!d.supports_vision;}}).catch(()=>{});
}
const _IMG_EXT=/\.(png|jpe?g|gif|webp|bmp|svg|ico)$/i;
function _fmtSz(n){return n<1024?n+'B':n<1048576?(n/1024).toFixed(1)+'KB':(n/1048576).toFixed(1)+'MB';}
function _fileBadge(name){const m=name.match(/\.([a-z0-9]+)$/i);return (m?m[1]:'?').toUpperCase().slice(0,4);}
// [Sprint-A 2026-07-24] slot 状态: uploading(loading+progress%) / done / error(可 retry)
function _renderPendFiles(){
  const pfp=document.getElementById('pfp');if(!pfp)return;
  if(!_pendFiles.length){pfp.innerHTML='';return;}
  const chips=_pendFiles.map((f,i)=>{
    const isImg=_IMG_EXT.test(f.name);
    const rel=(f.path||'').replace(/^.*?\/tmp\/openclaw_workspace\//,'');
    const src=rel?`/api/workspace/preview?path=${encodeURIComponent(rel)}`:'';
    // 客户端本地 preview: 未上传完的图直接用 blob URL
    const localSrc=f._blobUrl||src;
    const rm=`<button class="pfup" onclick="_rmPendFile(${i})" title="移除">×</button>`;
    const rt=`<button class="pfrt" onclick="_retryPendFile(${i})" title="重试上传">↻</button>`;
    const bar=(f.loading && !f.error)?`<div class="pfbar" style="width:${f.progress||0}%"></div>`:'';
    const cls=[f.loading?'loading':'',f.error?'error':'',(!f.loading&&!f.error)?'done':''].filter(Boolean).join(' ');
    if(isImg && localSrc){
      return `<div class="pfc img ${cls}"><img src="${localSrc}" onclick="_openImg('${localSrc}')"><span class="pfn" title="${esc(f.name)}">${esc(f.name)}</span>${rt}${rm}${bar}</div>`;
    }
    return `<div class="pfc ${cls}"><span class="mft" style="background:var(--ac);color:#000;padding:1px 5px;border-radius:3px;font-size:9px;font-weight:700">${_fileBadge(f.name)}</span><span class="pfn" title="${esc(f.name)}">${esc(f.name)}</span><span class="pfs">${_fmtSz(f.size)}</span>${rt}${rm}${bar}</div>`;
  }).join('');
  // 快速动作栏: ≥2 文件全部上传完 → 显示总结/翻译/提要按钮 (匹配 doc summary 场景)
  const readyCount=_pendFiles.filter(f=>f.path&&!f.loading&&!f.error).length;
  const totalCount=_pendFiles.length;
  let actions='';
  const imgReady=_pendFiles.filter(f=>f.path&&!f.loading&&!f.error&&_IMG_EXT.test(f.name)).length;
  if(readyCount>=2 && readyCount===totalCount){
    const ocrBtn=imgReady>0?`<button class="pf-act" onclick="_pendQuickAction('ocr')" title="用 vision_ocr 对 ${imgReady} 张图片做 OCR 识别">🔤 OCR (${imgReady})</button>`:'';
    actions=`<div class="pf-actions">
      <span class="pf-actions-hint">${readyCount} 个文件就绪:</span>
      <button class="pf-act" onclick="_pendQuickAction('summarize')" title="逐文件总结 + 汇总要点">📊 批量总结</button>
      <button class="pf-act" onclick="_pendQuickAction('translate')" title="翻译成英文 (默认); 目标语言可在弹出提示编辑">🌐 翻译</button>
      <button class="pf-act" onclick="_pendQuickAction('extract')" title="提取每文件关键信息 (人物/时间/数字/结论)">🔍 提取要点</button>
      <button class="pf-act" onclick="_pendQuickAction('compare')" title="比较文件之间的差异 / 共同点">🔀 对比</button>
      ${ocrBtn}
    </div>`;
  } else if(readyCount===1 && totalCount===1 && imgReady===1){
    // 单张图片时也给个 OCR 快捷入口 (常见场景)
    actions=`<div class="pf-actions">
      <span class="pf-actions-hint">图片就绪:</span>
      <button class="pf-act" onclick="_pendQuickAction('ocr')" title="识别图中文字 (vision_ocr)">🔤 OCR 识别</button>
      <button class="pf-act" onclick="_pendQuickAction('ocr_translate')" title="识别文字后自动翻成中文">🌏 OCR + 翻译</button>
    </div>`;
  }
  pfp.innerHTML=`<div class="pf-chips">${chips}</div>${actions}`;
}

// 快速动作: 自动填 prompt 到输入框, 用户可修改后回车发送 (不自动 send, 留最后确认权)
function _pendQuickAction(kind){
  const files=_pendFiles.filter(f=>f.path && !f.loading && !f.error);
  if(!files.length){toast('❌ 无就绪文件');return;}
  const list=files.map(f=>'- '+f.name).join('\n');
  const imgs=files.filter(f=>_IMG_EXT.test(f.name));
  const imgList=imgs.map(f=>'- '+f.name).join('\n');
  const prompts={
    summarize: `请对以下 ${files.length} 个附件逐一总结要点 (每文件 3-5 条), 然后给出跨文件的整体结论:\n${list}`,
    translate: `请把以下 ${files.length} 个附件的内容翻译成英文 (如原文已为英文则翻译成中文), 保留原有段落结构:\n${list}`,
    extract:   `请从以下 ${files.length} 个附件中提取关键信息 (人物/时间/地点/数字/关键结论), 以结构化列表输出:\n${list}`,
    compare:   `请比较以下 ${files.length} 个附件之间的差异与共同点, 输出对比表格:\n${list}`,
    ocr:       `请用 vision_ocr 工具对以下 ${imgs.length} 张图片逐一识别文字内容, 按图片顺序输出:\n${imgList||list}`,
    ocr_translate: `请用 vision_ocr 工具识别以下 ${imgs.length} 张图片的文字, 然后翻译成中文 (若原本已是中文则翻成英文):\n${imgList||list}`,
  };
  const mi=document.getElementById('mi');
  if(mi){
    mi.value=prompts[kind]||prompts.summarize;
    mi.focus();
    arx(mi);  // 自动 resize
    toast(`✓ 已填 ${kind} 提示词, 回车或点 ↑ 发送`);
  }
}
function _rmPendFile(i){
  const s=_pendFiles[i]; if(s && s._blobUrl){try{URL.revokeObjectURL(s._blobUrl);}catch(e){}}
  if(s && s._xhr){try{s._xhr.abort();}catch(e){}}
  _pendFiles.splice(i,1);_renderPendFiles();
}
function _retryPendFile(i){
  const s=_pendFiles[i]; if(!s || !s._file){toast('❌ 缺原始文件, 无法重试');return;}
  s.error=null; s.loading=true; s.progress=0; _renderPendFiles();
  _uploadOne(s);
}
function _openImg(src){const m=document.getElementById('imgModal');if(m){m.querySelector('img').src=src;m.classList.add('show');}}

// [Sprint-A Q007/Q008] 单文件上传 (XHR 拿 upload.progress + 可 retry)
function _uploadOne(slot){
  return new Promise(resolve=>{
    const xhr=new XMLHttpRequest();
    slot._xhr=xhr;
    xhr.upload.onprogress=(e)=>{
      if(e.lengthComputable){
        slot.progress=Math.round(e.loaded/e.total*100);
        _renderPendFiles();
      }
    };
    xhr.onload=()=>{
      slot._xhr=null;
      try{
        const d=JSON.parse(xhr.responseText||'{}');
        if(xhr.status>=200 && xhr.status<300 && d.ok){
          slot.path=d.path; slot.loading=false; slot.error=null; slot.progress=100;
        }else{
          slot.loading=false; slot.error=d.error||`HTTP ${xhr.status}`;
        }
      }catch(e){slot.loading=false; slot.error=String(e);}
      _renderPendFiles();
      resolve();
    };
    xhr.onerror=()=>{slot._xhr=null; slot.loading=false; slot.error='网络错误'; _renderPendFiles(); resolve();};
    xhr.onabort=()=>{slot._xhr=null; resolve();};
    xhr.open('POST', `/api/upload/${cur}`);
    const fd=new FormData(); fd.append('file', slot._file);
    xhr.send(fd);
  });
}

// [Sprint-A Q001-Q005] upFile 现在接受 FileList 或 File[]; 从 input / drop / paste 三处入
async function upFile(input){
  const files = input instanceof FileList ? Array.from(input) :
                Array.isArray(input) ? input :
                input && input.files ? Array.from(input.files) : [];
  if(!files.length) return;
  if(input && input.value !== undefined) input.value='';
  const MAX_SIZE=(_upLimit.max_mb||20)*1024*1024;
  /* 预警: 选了一堆图但模型不支持视觉 */
  const imgCount=files.filter(f=>_IMG_EXT.test(f.name)).length + _pendFiles.filter(f=>_IMG_EXT.test(f.name)).length;
  if(imgCount>_upLimit.max_images && _upLimit.supports_vision){
    toast(`⚠ 已选 ${imgCount} 张图, 超出 ${_upLimit.max_images} 张多模态上限, 多出的仍会上传但只作文件, 让 agent 用 vision_ocr 挑`);
  }else if(imgCount>0 && !_upLimit.supports_vision){
    toast(`⚠ 当前模型不支持视觉, 图片会作为文件路径保留, agent 可用 vision_ocr 处理`,4000);
  }
  /* session 尚未创建 → 用首个文件名作 session 名 */
  if(!cur){
    const d=await(await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Files: '+files[0].name})})).json();
    cur=d.id;sess.unshift({id:d.id,name:d.name,count:0});renderSL();
  }
  // 建 slot + 立刻渲染 (含 blob 缩略预览), 并发上传但节流为 3
  const slots=[];
  for(const f of files){
    if(f.size>MAX_SIZE){toast(`❌ ${f.name} 过大 (${_fmtSz(f.size)}), 限制 ${_upLimit.max_mb}MB`);continue;}
    // Q010 简单去重: 同 name+size 已在队列则跳
    if(_pendFiles.some(x=>x.name===f.name && x.size===f.size && !x.error)){
      toast(`⚠ 跳过重复: ${f.name}`); continue;
    }
    const slot={name:f.name,size:f.size,path:'',loading:true,progress:0,_file:f,error:null};
    if(_IMG_EXT.test(f.name)){try{slot._blobUrl=URL.createObjectURL(f);}catch(e){}}
    _pendFiles.push(slot); slots.push(slot);
  }
  _renderPendFiles();
  // 并发上限 3
  const q=slots.slice();
  const workers=Array.from({length:Math.min(3,q.length)}, async ()=>{
    while(q.length){const s=q.shift(); await _uploadOne(s);}
  });
  await Promise.all(workers);
}

// [Sprint-A Q002] 全局拖拽: dragover 显 overlay, drop 到消息区/输入区都触发 upFile
function _initAttachDragDrop(){
  // overlay 元素
  let ov=document.getElementById('drop-overlay');
  if(!ov){ov=document.createElement('div');ov.id='drop-overlay';ov.innerHTML='<div class="di">📎</div><div>松开鼠标以上传附件</div>';document.body.appendChild(ov);}
  let dragCnt=0;
  window.addEventListener('dragenter', e=>{
    if(!e.dataTransfer || !Array.from(e.dataTransfer.types||[]).includes('Files')) return;
    e.preventDefault(); dragCnt++; ov.classList.add('on');
  });
  window.addEventListener('dragover', e=>{
    if(!e.dataTransfer || !Array.from(e.dataTransfer.types||[]).includes('Files')) return;
    e.preventDefault();
  });
  window.addEventListener('dragleave', e=>{
    dragCnt=Math.max(0,dragCnt-1); if(dragCnt===0) ov.classList.remove('on');
  });
  window.addEventListener('drop', async e=>{
    if(!e.dataTransfer) return;
    dragCnt=0; ov.classList.remove('on');
    const items=e.dataTransfer.items;
    if(items && items.length){
      e.preventDefault();
      const collected=[];
      for(const it of items){
        if(it.kind!=='file') continue;
        const entry = it.webkitGetAsEntry && it.webkitGetAsEntry();
        if(entry && entry.isDirectory){
          await _walkDirEntry(entry,'',collected);
        }else{
          const f=it.getAsFile(); if(f) collected.push(f);
        }
      }
      if(collected.length) upFile(collected);
    }else if(e.dataTransfer.files && e.dataTransfer.files.length){
      e.preventDefault(); upFile(e.dataTransfer.files);
    }
  });
}
// [Sprint-A Q002] 递归读取目录 (DataTransferItem.webkitGetAsEntry)
function _walkDirEntry(entry, pathPrefix, collected){
  return new Promise(resolve=>{
    if(entry.isFile){
      entry.file(f=>{
        // 保留相对路径 (改文件名, 后端仍按 basename 收)
        try{Object.defineProperty(f,'name',{value: pathPrefix + f.name});}catch(e){}
        collected.push(f); resolve();
      }, ()=>resolve());
    }else if(entry.isDirectory){
      const reader=entry.createReader();
      const readAll=(acc)=>reader.readEntries(async ents=>{
        if(!ents.length){
          for(const e of acc) await _walkDirEntry(e, pathPrefix+entry.name+'/', collected);
          resolve();
        }else readAll(acc.concat(Array.from(ents)));
      }, ()=>resolve());
      readAll([]);
    }else resolve();
  });
}
// [Sprint-A Q003] 主输入框 paste: 抓剪贴板里的 image blob 直接上传
function _initAttachPaste(){
  const mi=document.getElementById('mi'); if(!mi) return;
  mi.addEventListener('paste', e=>{
    const items=e.clipboardData && e.clipboardData.items; if(!items) return;
    const imgs=[];
    for(const it of items){
      if(it.kind==='file' && it.type && it.type.startsWith('image/')){
        const f=it.getAsFile();
        if(f){
          // 剪贴板里的图通常叫 "image.png", 加时间戳避免撞 dedupe
          const nm=`pasted_${Date.now()}_${imgs.length}.${(it.type.split('/')[1]||'png').split('+')[0]}`;
          try{Object.defineProperty(f,'name',{value:nm});}catch(e){}
          imgs.push(f);
        }
      }
    }
    if(imgs.length){e.preventDefault(); upFile(imgs);}
  });
}
const PROJECT_TYPE_LABELS={general:'📝 通用',code:'📦 代码',novel:'📖 小说',report:'📊 报告',research:'🔬 研究'};
const PROJECT_STATUS_LABELS={active:'活跃',archived:'已归档',deleted:'已删除'};
function _projTypeLabel(t){return PROJECT_TYPE_LABELS[t]||t||'?';}
function _projStatusLabel(s){return PROJECT_STATUS_LABELS[s]||s||'';}
async function loadProjects(){const el=document.getElementById('proj-bd');el.innerHTML='Loading...';try{const d=await(await fetch('/api/projects')).json();let h=`<div style="font-size:11px;color:var(--tx3);margin-bottom:8px;padding:8px;background:var(--bg2);border-radius:4px;line-height:1.5">📂 <b>项目</b> = 跨会话工作空间, 多会话同项目共享 PROJECT_MAP/DECISIONS<br><b>📝 通用</b> 任意话题 · <b>📦 代码</b> 编程项目 (auto git+PROJECT_MAP) · <b>📖 小说</b> 长篇创作 (long-novel skill) · <b>📊 报告</b> 自动归档 reports/projects/ · <b>🔬 研究</b> 探索性 (deep-search)</div><div style="margin-bottom:10px"><input id="prj-name" placeholder="项目名" style="padding:4px 8px;border-radius:4px;background:var(--bg2);border:1px solid var(--bd);color:var(--tx1);width:60%"><select id="prj-type" style="padding:4px;background:var(--bg2);border:1px solid var(--bd);color:var(--tx1)"><option value="general">📝 通用 (任意话题)</option><option value="code">📦 代码 (编程项目)</option><option value="novel">📖 小说 (长篇创作)</option><option value="report">📊 报告 (研究归档)</option><option value="research">🔬 研究 (探索性)</option></select><button class="abtn" onclick="createProject()">+ 新建</button></div>`;if(!d.projects||!d.projects.length){h+='<div style="color:var(--tx3);font-size:12px">暂无项目</div>';}else{h+=d.projects.map(p=>`<div class="sr" style="flex-direction:column;align-items:flex-start;border-bottom:1px solid var(--bd);padding:6px 0"><div style="display:flex;justify-content:space-between;width:100%"><span style="font-weight:bold">${esc(p.name)}</span><span style="font-size:10px;color:var(--tx3)">${esc(_projTypeLabel(p.project_type))} · ${esc(_projStatusLabel(p.status))}</span></div><div style="font-size:11px;color:var(--tx3)">${esc(p.project_id)} · ${esc(p.description||'')}</div></div>`).join('');}el.innerHTML=h;}catch(ex){el.innerHTML=`<div style="color:var(--dg)">${esc(String(ex))}</div>`;}}
async function createProject(){const name=document.getElementById('prj-name').value.trim();if(!name){toast('需要项目名');return;}const ptype=document.getElementById('prj-type').value;await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,type:ptype})});loadProjects();}
async function loadStats(){const el=document.getElementById('stats-bd');el.innerHTML='<div style="color:var(--tx3);font-size:12px;padding:8px 0;text-align:center"><div class="dots" style="justify-content:center;margin-bottom:6px"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>加载中…</div>';try{const d=await(await fetch('/api/stats')).json();if(d.error){el.innerHTML=`<div style="padding:10px;text-align:center"><div style="color:var(--dg);font-size:12px;margin-bottom:6px">${esc(d.error)}</div><div style="color:var(--tx3);font-size:10px;margin-bottom:8px">LiteCode Server 可能正忙或未启动</div><button class="abtn" onclick="loadStats()" style="font-size:10px;padding:3px 10px">🔄 重试</button></div>`;return;}const fmt=n=>(n||0).toLocaleString();const days=Object.entries(d.by_day||{}).sort().slice(-7);const mx=Math.max(1,...days.map(([,v])=>v.prompt+(v.completion||0)));el.innerHTML=`<div class="sr"><span class="sl_">Prompt</span><span class="sv_">${fmt(d.total_prompt_tokens)}</span></div><div class="sr"><span class="sl_">Completion</span><span class="sv_">${fmt(d.total_completion_tokens)}</span></div><div class="sr"><span class="sl_">Total</span><span class="sv_">${fmt(d.total_tokens)}</span></div><div class="sr"><span class="sl_">Calls</span><span class="sv_">${fmt(d.total_calls)}</span></div><div class="sr"><span class="sl_">Avg/call</span><span class="sv_">${fmt(d.avg_tokens_per_call)}</span></div>${d.estimated_cost_usd!==undefined?`<div class="sr"><span class="sl_">Est. Cost (USD)</span><span class="sv_">\$${d.estimated_cost_usd.toFixed(4)}</span></div><div style="font-size:10px;color:var(--tx3);font-family:var(--mono);margin:-2px 0 8px">按 ${esc(d.price_model||'?')} 计 (${(d.price_per_mtok||{}).input||'?'}/${(d.price_per_mtok||{}).output||'?'} USD/Mt)</div>`:''}<div style="margin:14px 0 6px;font-size:11px;color:var(--tx3);font-family:var(--mono)">LAST 7 DAYS</div>${days.map(([day,v])=>{const tot=v.prompt+(v.completion||0),w=Math.round(tot/mx*160);return`<div class="dbr"><span class="dbd2">${day.slice(5)}</span><div class="dbf" style="width:${w}px"></div><span class="dbn_">${fmt(tot)}</span></div>`;}).join('')}${d.top_sessions?.length?`<div style="margin:14px 0 6px;font-size:11px;color:var(--tx3);font-family:var(--mono)">TOP SESSIONS</div>${d.top_sessions.slice(0,5).map(s=>`<div class="sr"><span class="sl_" style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(s.sid?.slice(0,22)||'?')}</span><span class="sv_">${fmt((s.prompt||0)+(s.completion||0))}</span></div>`).join('')}`:''}`;} catch(ex){el.innerHTML=`<div style="padding:10px;text-align:center"><div style="color:var(--dg);font-size:12px">${esc(String(ex))}</div><button class="abtn" onclick="loadStats()" style="font-size:10px;padding:3px 10px;margin-top:6px">🔄 重试</button></div>`;}}
async function loadMem(){if(!cur){document.getElementById('mem-bd').innerHTML='<div style="color:var(--tx3)">Select a session.</div>';return;}document.getElementById('mtitle').textContent='🧠 Memory: '+cur.slice(0,12);const el=document.getElementById('mem-bd');el.innerHTML='Loading...';try{const d=await(await fetch(`/api/memory/${cur}`)).json();const pct=d.pct||0;const bc=pct<60?'var(--ac3)':pct<80?'var(--wn)':'var(--dg)';const token_estimate=(d.token_estimate||0).toLocaleString();const hard_limit=(d.hard_limit||0).toLocaleString();let h=`<div class="mpb"><div class="mpbf" style="width:${pct}%;background:${bc}"></div></div><div style="font-size:11px;color:var(--tx3);font-family:var(--mono);margin-bottom:11px">Context: ${pct}% (${token_estimate}/${hard_limit}, ctx ${(d.context_window||0).toLocaleString()}t)</div>`;if(d.l1){h+=`<div class="ms"><div class="mst">Memory — ${d.l1.lines}L</div>`;const sects=d.l1.sections||{};if(Object.keys(sects).length)Object.entries(sects).forEach(([s,items])=>{h+=`<div class="mi2"><b style="color:var(--ac2)">${esc(s)}</b><br>${items.map(i=>`• ${esc(i)}`).join('<br>')}</div>`;});else h+=`<div class="mi2" style="color:var(--tx3)">空</div>`;h+=`</div>`;}else h+=`<div style="color:var(--tx3);font-size:12px;padding:6px 0">尚无记忆</div>`;if(d.context_files?.length){h+=`<div class="ms"><div class="mst">Context Files</div>`;d.context_files.forEach(f=>{h+=`<div class="mi2">${esc(f.name)} <span style="color:var(--tx3);font-size:11px">${f.lines}L</span></div>`;});h+=`</div>`;}if(d.l2?.length){h+=`<div class="ms"><div class="mst" onclick="this.parentElement.classList.toggle('open')">📚 L2 主题记忆 (${d.l2.length}) <span class="arr">&#x25BA;</span></div><div class="mc_">`;d.l2.forEach(it=>{h+=`<div class="mi2"><b>${esc(it.topic||it.name)}</b> <span style="color:var(--tx3);font-size:11px">${it.lines}L · ${it.tokens}t</span><br><span style="color:var(--tx3);font-size:11px">${esc(it.preview||'')}</span></div>`;});h+=`</div></div>`;}if(d.archive?.length){h+=`<div class="ms"><div class="mst" onclick="this.parentElement.classList.toggle('open')">📦 历史归档 (${d.archive.length}) <span class="arr">&#x25BA;</span></div><div class="mc_">`;d.archive.forEach(a=>{h+=`<div class="mi2"><span style="color:var(--tx3);font-size:11px">${esc(a.date)}</span> ${esc(a.name)}</div>`;});h+=`</div></div>`;}const cstate=d.compress_running?'running':(d.compress_pending?'pending':'idle');const cIcon=cstate==='running'?'🔄':(cstate==='pending'?'⏳':'✓');h+=`<div style="margin-top:14px;display:flex;gap:8px;align-items:center"><span style="font-size:11px;color:var(--tx3)">压缩: ${cIcon} ${cstate}</span><button class="abtn" onclick="compressMem()">🔄 Compress Now</button><button class="abtn dng" onclick="clrMem()">Clear Memory</button></div>`;el.innerHTML=h;}catch(ex){el.innerHTML=`<div style="color:var(--dg)">Error: ${ex}</div>`;}}
async function clrMem(){if(!cur)return;if(!await confirmCard('清空 MEMORY.md','将会话记忆全部删除, 不可恢复','清空'))return;await fetch(`/api/memory/${cur}`,{method:'DELETE'});toast('Memory cleared');loadMem();}
async function compressMem(){if(!cur)return;await fetch(`/api/memory/${cur}/compress`,{method:'POST'});toast('Compress requested');setTimeout(loadMem,500);}
async function loadSet(){const el=document.getElementById('set-bd');try{
  const [hRes,mRes]=await Promise.all([fetch('/api/health'),fetch('/api/models')]);
  const h=await hRes.json(), md=await mRes.json();
  const active=md.active||h.model||'?', models=md.models||[];
  const activeBackend=(md.active_backend||h.backend_url||'').replace(/\/+$/,'');
  let html=`<div class="sr"><span class="sl_">Server</span><span style="color:${h.ok?'var(--ac3)':'var(--dg)'}">${h.ok?'● Online':'● Offline'}</span></div>`;
  html+=`<div class="sr"><span class="sl_">Active Model</span><span style="font-family:var(--mono);font-size:12px;color:var(--ac)">${esc(active)}</span></div>`;
  html+=`<div class="sr"><span class="sl_">Backend</span><span style="font-family:var(--mono);font-size:11px;color:var(--tx3)">${esc(activeBackend)}</span></div>`;
  // Model list
  if(models.length){
    html+=`<div style="margin:14px 0 6px;font-size:11px;color:var(--tx3);font-family:var(--mono)">AVAILABLE MODELS</div>`;
    models.forEach((m,i)=>{
      // 同 id 不同 backend 的双 entry (如 deepseek-v4-pro 直连 + gether 网关) 必须按
      // (id, backend_url) 双键判定 ACTIVE, 否则两条都会被点亮
      const mUrl=(m.backend_url||'').replace(/\/+$/,'');
      const isActive=m.id===active && (!activeBackend || !mUrl || mUrl===activeBackend);
      const bc=isActive?'var(--ac)':'var(--tx3)';
      const btype=m.backend_type||'openai';
      const vis=m.supports_vision?'👁️':'';
      html+=`<div style="padding:8px 10px;background:${isActive?'rgba(240,165,0,.08)':'var(--sf)'};border:1px solid ${isActive?'rgba(240,165,0,.3)':'var(--bd)'};border-radius:var(--rs);margin-bottom:6px">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
          <span style="font-family:var(--mono);font-size:12px;font-weight:600;color:${bc}">${esc(m.label||m.id)}</span>
          <span style="font-size:9px;padding:1px 5px;border-radius:3px;background:var(--sf2);color:var(--tx3)">${btype}</span>
          ${vis?`<span style="font-size:11px" title="支持图片">${vis}</span>`:''}
          ${isActive?'<span style="font-size:9px;color:var(--ac);font-weight:600">✓ ACTIVE</span>':''}
        </div>
        <div style="font-size:10px;color:var(--tx3);font-family:var(--mono);margin-bottom:4px">${esc(m.backend_url||'')} · ctx:${(m.context_window||0).toLocaleString()} · max:${m.max_tokens||'?'}</div>
        <div style="display:flex;gap:4px">
          ${!isActive?`<button class="abtn" onclick="mdlSwitch('${esc(m.id)}', '${esc(m.backend_url||'')}')" style="font-size:10px;padding:2px 8px">切换</button>`:''}
          <button class="abtn" onclick="mdlTest(${i})" style="font-size:10px;padding:2px 8px" id="mt${i}">🔍 测试</button>
          <button class="abtn dng" onclick="mdlDel('${esc(m.id)}')" style="font-size:10px;padding:2px 8px">删除</button>
        </div>
        <div id="mtr${i}" style="font-size:10px;margin-top:4px;color:var(--tx3);font-family:var(--mono)"></div>
      </div>`;
    });
  }
  // Add model form
  html+=`<div style="margin:14px 0 6px;font-size:11px;color:var(--tx3);font-family:var(--mono)">ADD NEW MODEL</div>
  <div style="display:flex;flex-direction:column;gap:5px;padding:8px 10px;background:var(--sf);border:1px solid var(--bd);border-radius:var(--rs)">
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      <input id="nm-id" placeholder="Model ID" style="flex:1;min-width:120px;padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:11px;font-family:var(--mono)">
      <select id="nm-type" style="padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:11px">
        <option value="vllm">vLLM</option><option value="ollama">Ollama</option><option value="openai">OpenAI</option><option value="generic">通用</option>
      </select>
    </div>
    <input id="nm-url" placeholder="Backend URL (http://host:port/v1)" style="width:100%;padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:11px;font-family:var(--mono)">
    <div style="display:flex;gap:6px">
      <input id="nm-key" placeholder="API Key" style="flex:1;padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:11px;font-family:var(--mono)">
      <input id="nm-ctx" type="number" placeholder="ctx" value="200000" style="width:80px;padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:11px;font-family:var(--mono)">
      <input id="nm-max" type="number" placeholder="max_tokens" value="18000" style="width:80px;padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:11px;font-family:var(--mono)">
    </div>
    <div style="display:flex;gap:6px;align-items:center">
      <label style="font-size:10px;color:var(--tx2)"><input type="checkbox" id="nm-vision" style="accent-color:var(--ac3)"> 视觉</label>
      <label style="font-size:10px;color:var(--tx2)"><input type="checkbox" id="nm-think" checked style="accent-color:var(--ac3)"> Thinking</label>
      <div style="flex:1"></div>
      <button class="abtn" onclick="mdlAdd()" style="font-size:10px;padding:3px 10px">添加模型</button>
    </div>
  </div>`;
  // [v1.1] 配置编辑器 - 允许在 UI 编辑 config.json 的非敏感字段
  html+=`<div style="margin:14px 0 6px;font-size:11px;color:var(--tx3);font-family:var(--mono)">CONFIGURATION</div>
  <div id="cfg-bd" style="padding:8px 10px;background:var(--sf);border:1px solid var(--bd);border-radius:var(--rs);font-size:11px;color:var(--tx3)">Loading config…</div>`;
  html+=`<div style="margin-top:14px"><button class="abtn" onclick="doLogout()">Sign Out</button></div>`;
  el.innerHTML=html;
  // store models ref for test
  window._mdlList=models;
  // 延迟加载配置, 不阻塞主 UI
  loadCfg();
}catch(ex){el.innerHTML=`<div style="color:var(--dg)">Error: ${ex}</div>`;}}

