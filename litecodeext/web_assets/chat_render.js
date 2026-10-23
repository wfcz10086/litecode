// chat_render.js — 会话列表 / 切换 / 重命名 + 历史消息渲染 (renderMsgs / addMsg) [原 chat_core.js 拆分 1/5]
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
    const icon=s.id.startsWith('wx-')?'💬 ':s.id.startsWith('cli-')?'🖥️ ':'';
    const isPinned=pinned.has(s.id);
    const star=isPinned?'★':'☆';
    const ago=_fmtAgo(s.last_used||s.created);
    return`<div class="si${s.id===cur?' act':''}" data-sid="${esc(s.id)}" onclick="swS('${s.id}')"><div style="flex:1;min-width:0"><div class="sn">${isPinned?'<span style="color:var(--ac);margin-right:2px">★</span>':''}${icon}${esc(_lcName(s.name))}</div><div class="sm">${s.count}条 · ${ago}</div></div><button class="sdl" onclick="event.stopPropagation();pinSess('${s.id}')" title="标星" style="margin-right:2px;color:${isPinned?'var(--ac)':'var(--tx3)'}">${star}</button><button class="sdl" onclick="event.stopPropagation();delS('${s.id}')">✕</button></div>`;
  }).join('');
  if (window.LCProjects) try { LCProjects.decorate(el); } catch (e) {}
  // 滚动到当前 active item
  const act = el.querySelector('.si.act');
  if (act && act.scrollIntoView) try { act.scrollIntoView({block:'nearest',behavior:'instant'}); } catch(e){}
}
/* [D 2026-05-22] 切 session 立即清屏 + loading state, 不再等 fetch 完才反应 */
async function swS(sid){
  if(sid===cur)return;
  /* [chat-runs] 进行中切走: 只断本页订阅, 后台轮次照跑, 切回来自动接上 */
  _detachStream();
  cur=sid;closeSB();
  try{localStorage.setItem('lc.cur',sid);}catch(e){}
  const s=sess.find(x=>x.id===sid);
  document.getElementById('ct').textContent=_lcName(s?.name)||sid;
  document.getElementById('exp-btn').style.display='inline-flex';
  renderSL();
  // 立即清屏 + loading
  const msgsEl=document.getElementById('msgs');
  if(msgsEl){
    msgsEl.innerHTML='<div style="padding:40px 20px;text-align:center;color:var(--tx3);font-size:12px"><div class="dots" style="justify-content:center;margin-bottom:8px"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>加载会话中…</div>';
  }
  try{
    /* [perf 2026-10] 只拉最近 LC_PAGE_SIZE 条 (每条带全局 index), 更早的滚到顶部再分段拉, 见 chat_paging.js */
    const r=await fetch(`/api/sessions/${sid}?tail=${LC_PAGE_SIZE}`);
    if(cur!==sid)return;  // 切换期间又点了别的会话, 丢弃旧 fetch
    if(r.status===404){  /* [ui-audit 2026-10-21] 会话已在别处删除: 提示并移出列表, 不再渲染成空白新会话 */
      sess=sess.filter(x=>x.id!==sid);renderSL();
      if(msgsEl)msgsEl.innerHTML='<div style="padding:40px 20px;text-align:center;color:var(--tx3);font-size:13px">这个会话不存在或已被删除</div>';
      return;
    }
    const d=await r.json();
    if(cur!==sid)return;
    /* [ui-v2 2026-10-21] 会话不在侧栏列表里 (刚建 / 列表未刷新) 时标题原来显示成 sid: 用会话数据里的名字补上 */
    if(!s){const nm=_lcName(d.name);if(nm){document.getElementById('ct').textContent=nm;if(!sess.some(x=>x.id===sid)){sess.unshift({id:sid,name:d.name,count:(d.messages||[]).length,created:d.created,last_used:d.last_used});renderSL();}}}
    renderMsgs(d.messages||[]);
    sbot(true);
    if(window.LCAttach)LCAttach.resumeIfRunning(sid,d.messages||[]);
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
/* [ui-v2 2026-10-21] 老会话标题里重导时加的 "🔌 " 前缀只是来源标记, 显示时去掉 */
function _lcName(n){return String(n==null?'':n).replace(/^🔌\s*/,'');}
/* [ui-audit 2026-10-21] 工具结果里给模型看的 "[SYSTEM: ...]" 提示不展示给用户 */
function _lcStripSys(t){const s=String(t==null?'':t);const i=s.indexOf('[SYSTEM:');return i>0?s.slice(0,i).trimEnd():s;}
/* [chat-runs] 进行中切走/新建会话: 只断本页订阅 (后台轮次照跑, 切回来自动接上), 复位输入区 */
function _detachStream(){if(!streaming)return;_streamGen++;try{_curReader&&_curReader.cancel();}catch(e){}_resetStreamUi();}
/* [2026-10-23 fix] 原来 if(streaming)return: 长任务进行中点「新对话」直接无反应 (页面像卡住)。
 * 现在与 swS 一致先断开当前会话的订阅再新建; _ncBusy 防连点建出多个空会话 */
let _ncBusy=false;
async function newChat(){
  if(_ncBusy)return;_ncBusy=true;
  try{
    _detachStream();
    const r=await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'New Chat'})});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();cur=d.id;
    try{localStorage.setItem('lc.cur',d.id);}catch(e){}
    sess.unshift({id:d.id,name:d.name,count:0,created:d.created,last_used:d.last_used||d.created});renderSL();
    document.getElementById('msgs').innerHTML='<div id="empty"><div class="elogo">⚡</div><div class="etitle">New Chat</div></div>';document.getElementById('ct').textContent='New Chat';closeSB();
  }catch(e){toast('新建会话失败: '+e);}
  finally{_ncBusy=false;}
}
async function delS(sid){if(!await confirmCard('删除会话','确定要删除此会话? 消息和上下文将永久移除','删除'))return;await fetch(`/api/sessions/${sid}`,{method:'DELETE'});sess=sess.filter(s=>s.id!==sid);renderSL();if(cur===sid){cur=null;document.getElementById('msgs').innerHTML='<div id="empty"><div class="elogo">⚡</div></div>';}}
/* [v12.9] 渲染前合并连续 assistant 消息 —— 多轮 iteration 持久化成多条 assistant 时,
   刷新后显示为多个独立 "AI" 气泡, spawn_agent/工具调用被拆散。合并后同一轮成一个气泡。*/
/* base: msgs[0] 的全局下标 (消息自带 index 时以 index 为准); 输出块附 _i0/_i1 (首/末条全局下标) 与 _key (定位用指纹) */
function _mergeConsecutiveAssistants(msgs,base=0){
  const out=[];
  (msgs||[]).forEach((m,k)=>{
    const gi=typeof m.index==='number'?m.index:base+k;
    const key=typeof _lcKey==='function'?_lcKey(m):'';
    const last=out[out.length-1];
    if(m.role==='assistant' && last && last.role==='assistant' && (last.source||'')===(m.source||'')){
      last._i1=gi;last._key=key;
      const joiner=(last.content && m.content)?'\n\n':'';
      last.content=(last.content||'')+joiner+(m.content||'');
      last.tools=[...(last.tools||[]),...(m.tools||[])];
      last.diffs=[...(last.diffs||[]),...(m.diffs||[])];last.desktop_steps=[...(last.desktop_steps||[]),...(m.desktop_steps||[])];
      last._tool_results=[...(last._tool_results||[]),...(m._tool_results||[])];
      /* [trace 2026-10-22] 调用链路 (每步成败/用时/摘要) 与整轮错误一起合并 */
      if(m.trace_steps&&m.trace_steps.length)last.trace_steps=[...(last.trace_steps||[]),...m.trace_steps];
      if(m.turn_error)last.turn_error=m.turn_error;
      (last._parts||(last._parts=[])).push(_lcPart(m));
      const lr=last.reasoning||'', mr=m.reasoning||'';
      last.reasoning=lr+(lr&&mr?'\n\n':'')+mr;
      if(m.usage) last.usage=m.usage;  /* 用最后一条 usage (含总 tokens) */
      if(m.meta) last.meta={...(last.meta||{}),...m.meta};  /* meta.skills: 本轮用到的技能 */
      last.ts=m.ts||last.ts;
    }else{
      out.push(_lcSteerView({...m, tools:[...(m.tools||[])], diffs:[...(m.diffs||[])], _tool_results:[...(m._tool_results||[])], _i0:gi, _i1:gi, _key:key, _parts:m.role==='assistant'?[_lcPart(m)]:undefined}));
    }
  });
  out.forEach(_lcSplitNarration);
  return out;
}
/* msgs 可以是完整列表, 也可以是 ?tail / ?before 返回的片段 (每条带全局 index) — 分段逻辑见 chat_paging.js */
/* [steer] 会话记录里的插话是给模型看的完整文本 (lib/agent/steer.py: 头标记 + 原话 + 说明),
 * 显示时只留用户原话并打「插话」标签 */
const _LC_STEER_HEAD='[用户在任务进行中插话]';
function _lcSteerView(m){
  if(m.role!=='user'||typeof m.content!=='string'||!m.content.startsWith(_LC_STEER_HEAD))return m;
  let t=m.content.slice(_LC_STEER_HEAD.length);const cut=t.lastIndexOf('\n\n（这是对当前任务');
  if(cut>=0)t=t.slice(0,cut);
  return {...m,content:t.trim(),steer:true};
}
/* [ui-v2 2026-10-21] 过程旁白归位: 一轮里「最后一次工具调用之前」的正文 (如"内存数据已拿到, 现在运行验证")
   是步骤间说明, 并进折叠时间线 (tools 里插 LC_NOTE 标记项, _tool_results 对应位置补空); 正文只留最后一次工具调用之后的结论。
   整轮没有工具 / 最后一段之后没有正文 (被中断) 时不动。流式路径见 chat_stream.js 同名逻辑。 */
const LC_NOTE='\u2063note:';
function _lcPart(m){return {c:typeof m.content==='string'?m.content:null, nt:(m.tools||[]).length, nr:(m._tool_results||[]).length};}
function _lcSplitNarration(it){
  const P=it._parts;delete it._parts;
  if(!P||P.length<2||P.some(p=>p.c===null))return;
  let L=-1;P.forEach((p,i)=>{if(p.nt)L=i;});
  if(L<0||!P.slice(L+1).some(p=>p.c.trim()))return;
  const tools=[],res=[],content=[];let tc=0,rc=0;
  P.forEach((p,i)=>{
    const segT=it.tools.slice(tc,tc+p.nt),segR=it._tool_results.slice(rc,rc+p.nr);tc+=p.nt;rc+=p.nr;
    while(segR.length<segT.length)segR.push({});
    if(i<=L){if(p.c.trim()){tools.push(LC_NOTE+p.c.trim());res.push({});}}
    else if(p.c.trim())content.push(p.c);
    tools.push(...segT);res.push(...segR);
  });
  it.tools=tools;it._tool_results=res;it.content=content.join('\n\n');
}
function renderMsgs(msgs){const el=document.getElementById('msgs');el.innerHTML='';if(!msgs.length){_lcPage.sid=cur;_lcPage.start=0;el.innerHTML='<div id="empty"><div class="elogo">⚡</div><div class="etitle">LiteCode</div></div>';return}_lcRenderInitial(msgs);sbot()}
function rdiff(df){if(typeof df!=='string')return '';return df.split('\n').map(l=>{const cl=l.startsWith('+')&&!l.startsWith('+++')?'da':l.startsWith('-')&&!l.startsWith('---')?'dd_':l.startsWith('@@')?'dm':'';return`<span class="${cl}">${esc(l)}</span>`;}).join('\n')}
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
    const img=document.createElement('img');img.className='wx-img';img.alt=fname;_lcPrepImg(img,apiUrl,p,280);
    img.onclick=()=>{const m=document.getElementById('imgModal');document.getElementById('imgModalImg').src=apiUrl;m.classList.add('show');};
    /* 缩略图失败时 chat_paging.js 先回退原图 (fb=retry), 原图也失败才隐藏 */
    img.onerror=()=>{if(img.dataset.fb==='retry'){img.dataset.fb='1';return;}img.style.display='none';};
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
/* [trace 2026-10-22] extra = {trace_steps, turn_error}: 刷新后重绘时间线状态/用时/摘要 + 错误卡片 (chat_timeline.js) */
function addMsg(role,content,tools=[],scroll=true,diffs=[],usage=null,source='',wx_user='',reasoning='',toolResults=[],desktopSteps=[],extra=null){
  tools=(tools||[]).filter(t=>t!=='推理中…'); /* 老会话里落库的等首包占位, 不是工具调用 */
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
            const img=document.createElement('img');img.alt=name;_lcPrepImg(img,src,p,220);img.onclick=()=>_openImg(src);ac.appendChild(img);
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
    if(tools.length){const tg=document.createElement('div');tg.className='tg';tools.forEach((t,_i)=>{if(typeof t==='string'&&t.startsWith(LC_NOTE)){const n=document.createElement('div');n.className='tnote';n.textContent=t.slice(LC_NOTE.length);tg.appendChild(n);return;}const ti=document.createElement('div');ti.className='ti';const c=t.indexOf(': ');if(c>0){const argsStr=t.slice(c+2);if(argsStr.length>TOOL_ARGS_PREVIEW){const aid='ta'+Date.now()+Math.random().toString(36).slice(2,4);ti.innerHTML=`<span class="tn" data-tool="${esc(t.slice(0,c))}">${esc(t.slice(0,c))}</span><span class="targs" id="${aid}" onclick="this.classList.toggle('open')"><span class="arr">&#x25BA;</span><span class="ta tap">${esc(argsStr.slice(0,TOOL_ARGS_PREVIEW))}…</span><span class="tac">${esc(argsStr)}</span></span>`;}else{ti.innerHTML=`<span class="tn" data-tool="${esc(t.slice(0,c))}">${esc(t.slice(0,c))}</span><span class="ta">${esc(argsStr)}</span>`;}}else ti.textContent=t.slice(0,TOOL_ARGS_PREVIEW);if(c>0)ti.dataset.ti=_i;/* [trace] 行↔tools 下标 */tg.appendChild(ti);if(toolResults&&toolResults[_i]&&toolResults[_i].result){const res=_lcStripSys(toolResults[_i].result);const tr=document.createElement('div');tr.className='tr_';if(res.length>TOOL_RESULT_PREVIEW){const rid='tr'+Date.now()+Math.random().toString(36).slice(2,4);tr.innerHTML=`<span class="trc" id="${rid}" onclick="this.classList.toggle('open')"><span class="arr">&#x25BA;</span><span class="trp">↳ ${esc(res.slice(0,TOOL_RESULT_PREVIEW))}…</span><span class="trf">↳ ${esc(res)}</span></span>`;}else{tr.textContent='↳ '+res;}tg.appendChild(tr);}});
      /* [#16 3段折叠] 工具调用 ≥3 条时收进折叠块; 1-2 条直接展开 (少量场景直看更快) */
      if(tools.length>=3){
        const tgb=document.createElement('div');tgb.className='tgb';
        const toolNames=Array.from(new Set(tools.map(t=>{const c=t.indexOf(': ');return c>0?t.slice(0,c):t;}))).slice(0,3);
        tgb.innerHTML=`<div class="tgh" onclick="this.parentElement.classList.toggle('open')"><span class="arr">&#x25BA;</span><span>🔧 工具调用 <b>${tools.length}</b></span><span class="tgi">${toolNames.join(' · ')}${toolNames.length<tools.length?'…':''}</span></div>`;
        const tgc=document.createElement('div');tgc.className='tgc';tgc.appendChild(tg);tgb.appendChild(tgc);
        bubble.appendChild(tgb);
      }else{bubble.appendChild(tg);}
    }
    /* [trace 2026-10-22] 中文名 + 状态 (✓ 用时 / ✗) + 展开详情; 老会话没有 trace_steps 时按结果是否 ERROR 判断 */
    if(window.LCTimeline){try{LCTimeline.decorateHistory(bubble,tools,toolResults||[],(extra&&extra.trace_steps)||[]);}catch(e){}}
    desktopSteps.forEach(step=>window.LCDesktopSteps?.render(bubble,step));
    diffs.forEach(dv=>{const df=dv?.diff;if(typeof df!=='string'||!df)return;const fname=(typeof dv.filepath==='string'?dv.filepath:'file').split('/').pop();const adds=(df.match(/^\+[^+]/mg)||[]).length;const dels=(df.match(/^-[^-]/mg)||[]).length;const did='d'+Math.random().toString(36).slice(2,7);const dEl=document.createElement('div');dEl.className='dv';dEl.innerHTML=`<div class="dth" onclick="document.getElementById('${did}').classList.toggle('open')"><span style="color:var(--tx3);font-size:10px">&#x25BA;</span><span style="color:#93c5fd">${esc(fname)}</span><span style="color:var(--ac3)">+${adds}</span><span style="color:var(--dg)">-${dels}</span></div><div class="dbd" id="${did}"><pre><code>${rdiff(df)}</code></pre></div>`;bubble.appendChild(dEl);});
    const _te=extra&&extra.turn_error;
    if(_te&&_te.content_echo&&typeof content==='string'&&content.trimEnd().endsWith(_te.content_echo)){content=content.trimEnd().slice(0,-_te.content_echo.length).trimEnd();}  /* [trace] 与错误卡片重复 */
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
      function _renderMd(container,txt){try{container.innerHTML=window.lcSafeMd(txt||'');}catch(e){container.textContent=txt;}_lcOptimizeImgs(container);container.querySelectorAll('pre').forEach(pre=>{const btn=document.createElement('button');btn.className='cpb';btn.textContent='copy';btn.onclick=()=>{navigator.clipboard.writeText(pre.querySelector('code')?.textContent||'');btn.textContent='✓';setTimeout(()=>btn.textContent='copy',1500);};pre.style.position='relative';pre.appendChild(btn);pre.querySelectorAll('code').forEach(c=>{try{hljs.highlightElement(c);}catch(e){}});});_scanAndPreview(container,txt);}
      /* [ui-v2 2026-10-21] 回答不再按长度折叠 (一线产品都不折叠回答; 原 >3000 字收进「长文消息」) */
      if(false){
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
    if(_te&&window.LCTimeline){try{bubble.appendChild(LCTimeline.errorCard(_te,{partial:!!(String(content||'').trim()||bubble.querySelector('.ti.lct-ok'))}));}catch(e){}}  /* [trace] 刷新后重绘错误卡片 */
    if(usage&&window.lcUsageLine){const ub=document.createElement('div');ub.className='ub';const r=lcUsageLine(usage,(tools||[]).filter(t=>!(typeof t==='string'&&t.startsWith(LC_NOTE))).length);ub.innerHTML=r.html;ub.dataset.el=r.el;bubble.appendChild(ub);}
    else if(usage){const ub=document.createElement('div');ub.className='ub';const f=n=>(n||0).toLocaleString();ub.innerHTML=`<span>P:<b>${f(usage.prompt_tokens)}</b></span><span>C:<b>${f(usage.completion_tokens)}</b></span><span>T:<b>${f(usage.total_tokens)}</b></span>${usage.iterations>1?`<span>${usage.iterations}轮</span>`:''}${usage.elapsed_seconds?`<span>${usage.elapsed_seconds}s</span>`:''}`;bubble.appendChild(ub);}
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
  _lcOptimizeImgs(bubble);  /* [perf] 兜底: 本气泡里所有 <img> 补 lazy/async + 上传图换缩略图 (同步执行, 早于取图) */
  wrap.appendChild(bubble);el.appendChild(wrap);if(scroll)sbot();return{wrap,bubble};
}

/* [2026-10-22] 侧边栏自动刷新: 微信/企微/后台任务新建或更新的会话, 网页不用手动刷新就能看到;
 * 正在看的会话 (自己没在流式输出时) 有新消息也自动重载。页面在后台时不轮询。 */
let _slSig='';
function _sessSig(list){return (list||[]).map(s=>s.id+':'+(s.last_used||0)+':'+(s.count||0)+':'+(s.name||'')).join('|');}
async function _pollSessList(){
  if(document.hidden)return;
  try{
    const r=await fetch('/api/sessions'); if(!r.ok)return;
    const list=await r.json(); const sig=_sessSig(list);
    if(!_slSig)_slSig=_sessSig(sess);   /* 基线 = 页面已渲染的列表 */
    if(sig===_slSig)return;
    const before=(sess||[]).find(x=>x.id===cur);
    _slSig=sig; sess=list; renderSL();
    const after=list.find(x=>x.id===cur);
    if(cur&&!streaming&&before&&after&&(after.count!==before.count||after.last_used!==before.last_used)){
      const c=cur; cur=null; await swS(c);
    }
  }catch(e){}
}
setInterval(_pollSessList, 8000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)_pollSessList();});
