// chat_stream.js — 发送 + SSE 流式轮次 (_streamTurn) / 中断 / 重试上一条 [原 chat_core.js 拆分 3/5]
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
  const bodyEl=_newAiBubble();
  await _streamTurn(bodyEl,()=>fetch(`/api/chat/${cur}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg, strategist_mode: !!window._stratMode})}));
}
/* [chat-runs 2026-10-16] 轮次在后台跑, 本页只是订阅者: 发消息 / 刷新重连 / 切回会话都走 _streamTurn.
 * _streamGen 每次切走 / 中断 +1, 旧订阅的收尾不再动按钮状态, 也不触发自动重连 (见 chat_attach.js) */
let _streamGen=0;
function _newAiBubble(){
  const uid='b'+Date.now()+Math.random().toString(36).slice(2,4);
  const aw=document.createElement('div');aw.className='msg ai';const ar2=document.createElement('div');ar2.className='mr';ar2.textContent='AI';const ab=document.createElement('div');ab.className='mb';ab.id=uid;ab.innerHTML='<div class="dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';aw.appendChild(ar2);aw.appendChild(ab);document.getElementById('msgs').appendChild(aw);sbot(true);
  return document.getElementById(uid);
}
function _resetStreamUi(){streaming=false;window.streaming=false;_sending=false;_curReader=null;document.getElementById('sdb').disabled=false;document.getElementById('ib').classList.remove('vis');}
async function _streamTurn(bodyEl,open){
  const sid=cur,gen=++_streamGen;
  try{localStorage.setItem('lc.cur',sid);}catch(e){}  /* 刷新后回到这个会话并接上进度 */
  streaming=true;window.streaming=true;_sending=true;document.getElementById('sdb').disabled=true;document.getElementById('ib').classList.add('vis');
  let ft='',sr_=null,fc=true,sdiffs=[],sdesktop=[];_lu=null;
  try{
    const resp=await open();
    if(resp.status===409){
      /* 别的标签页 / 别人已在本会话跑一轮: 不重复发, 直接接入实时进度 */
      bodyEl.closest('.msg')?.remove();_streamGen++;_resetStreamUi();
      toast('⏳ 该会话还在执行上一轮, 已接入实时进度');
      if(window.LCAttach)LCAttach.resume(sid);
      return;
    }
    if(!resp.ok||!resp.body)throw new Error('HTTP '+resp.status);
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
          if(st==='executing'&&det){_dropPrep(bodyEl);let tg=bodyEl.querySelector('.tg');if(!tg){bodyEl.querySelector('.dots')?.remove();tg=document.createElement('div');tg.className='tg';bodyEl.insertBefore(tg,bodyEl.firstChild);}const ti=document.createElement('div');ti.className='ti'+(delta[key].key==='preparing'?' ti-prep':'');const c=det.indexOf(': ');ti.innerHTML=(al?`<span style="color:var(--ac3);font-weight:600;font-size:11px">[${esc(al)}]</span>`:'')+(c>0?`<span class="tn" data-tool="${esc(det.slice(0,c))}">${esc(det.slice(0,c))}</span><span class="ta">${esc(det.slice(c+2).slice(0,90))}</span>`:`${esc(det.slice(0,90))}`);tg.appendChild(ti);const allTi=tg.querySelectorAll('.ti:not(.hid)');if(allTi.length>4){const h=allTi.length-4;for(let i=0;i<h;i++)allTi[i].classList.add('hid');let fb=tg.querySelector('.tfl');if(!fb){fb=document.createElement('div');fb.className='tfl';tg.insertBefore(fb,tg.firstChild);fb.onclick=()=>{tg.querySelectorAll('.hid').forEach(x=>x.classList.remove('hid'));fb.remove();};}fb.textContent=`... 展开 ${tg.querySelectorAll('.ti.hid').length} 条`;}sbot();}
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
        if(delta.desktop_step){const card=window.LCDesktopSteps?.render(bodyEl,delta.desktop_step);if(card)sdesktop.push(card.outerHTML);sbot();}
        if(delta.diff_view){const dv=delta.diff_view,df=dv?.diff;if(typeof df==='string'&&df){bodyEl.querySelector('.dots')?.remove();const fname=(typeof dv.filepath==='string'?dv.filepath:'file').split('/').pop();const adds=(df.match(/^\+[^+]/mg)||[]).length;const dels=(df.match(/^-[^-]/mg)||[]).length;const did='d'+Date.now()+Math.random().toString(36).slice(2,4);const dEl=document.createElement('div');dEl.className='dv';dEl.innerHTML=`<div class="dth" onclick="document.getElementById('${did}').classList.toggle('open')"><span style="color:var(--tx3);font-size:10px">&#x25BA;</span><span style="color:#93c5fd">${esc(fname)}</span><span style="color:var(--ac3)">+${adds}</span><span style="color:var(--dg)">-${dels}</span></div><div class="dbd" id="${did}"><pre><code>${rdiff(df)}</code></pre></div>`;bodyEl.appendChild(dEl);sdiffs.push(dEl.outerHTML);sbot();}}
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
          if(fc){bodyEl.querySelector('.dots')?.remove();_dropPrep(bodyEl);fc=false;sr_=document.createElement('div');sr_.className='sraw';bodyEl.appendChild(sr_);}ft+=ct;if(!_renderTimer){_renderTimer=setTimeout(()=>{try{if(sr_){sr_.innerHTML=marked.parse(ft);sr_.querySelectorAll('pre code').forEach(c=>{if(!c.classList.contains('hljs'))try{hljs.highlightElement(c)}catch(e){}});}}catch(e){}sbot();_renderTimer=null;},200);}
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
  _dropPrep(bodyEl);const tgEl=bodyEl.querySelector('.tg');
  /* [P46 修] 保留错误块, 流末重建时不抹掉 ❌ + 重试按钮 */
  const errEls=bodyEl.querySelectorAll('.msg-error');
  let errHtml='';errEls.forEach(e=>errHtml+=e.outerHTML);
  let fh=rsbHtml+(tgEl?tgEl.outerHTML:'')+sdesktop.join('');sdiffs.forEach(d=>fh+=d);if(ft)fh+=marked.parse(ft);
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
  if(gen===_streamGen){_resetStreamUi();if(window.LCAttach)LCAttach.afterTurn(sid);}
  sbot();
}
/* "推理中…" 占位状态只在等首个真实事件时有用, 之后移除; 移完 .tg 空了也一并删 */
function _dropPrep(body){body.querySelectorAll('.tg .ti-prep').forEach(x=>x.remove());const tg=body.querySelector('.tg');if(tg&&!tg.children.length)tg.remove();}
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
    _streamGen++;  /* 主动停止: 旧订阅收尾不触发自动重连 */
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
