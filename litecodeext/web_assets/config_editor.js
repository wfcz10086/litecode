// ── 配置编辑器 ───────────────────────────────────────────────
async function loadCfg(){
  const el=document.getElementById('cfg-bd');if(!el)return;
  try{
    const r=await fetch('/api/config');const d=await r.json();
    if(!d.ok){el.innerHTML=`<span style="color:var(--dg)">读取失败: ${esc(d.error||'')}</span>`;return;}
    const cfg=d.config||{};const editable=d.editable_sections||[];
    window._cfgData=cfg;
    let h='';
    // 每个白名单 section 渲染成一组可编辑字段
    const sectionLabels={
      model:'当前模型 (推理开关 / max_tokens / context)',
      agent:'Agent 运行参数',
      memory:'记忆/压缩阈值',
      wechat:'微信 Bot 参数',
      search:'搜索引擎配置',
      iteration_trace:'迭代追踪',
      web_ui:'Web UI',
    };
    editable.forEach(sec=>{
      const obj=cfg[sec];if(!obj||typeof obj!=='object')return;
      h+=`<details style="margin-bottom:8px" ${sec==='model'||sec==='agent'||sec==='memory'||sec==='wechat'?'open':''}>
        <summary style="cursor:pointer;font-family:var(--mono);font-size:11px;color:var(--ac);padding:3px 0">▸ ${esc(sectionLabels[sec]||sec)} (${sec})</summary>
        <div style="margin-top:4px;padding-left:8px;border-left:2px solid var(--bd2)">`;
      Object.keys(obj).forEach(k=>{
        const v=obj[k];const kid=`cfg-${sec}-${k}`;
        if(v===null||v===undefined)return;
        // search/auth 等嵌套对象 — 只读展示
        if(typeof v==='object'){
          h+=`<div style="margin:3px 0;font-family:var(--mono);font-size:10px;color:var(--tx3)">
            <span style="color:var(--tx2)">${esc(k)}</span>: <span style="color:var(--tx3)">[object, ${Array.isArray(v)?v.length+' items':'edit in config.json'}]</span>
          </div>`;
          return;
        }
        const isBool=typeof v==='boolean';
        const isNum=typeof v==='number';
        const isMasked=(typeof v==='string'&&v.startsWith('••••'));
        h+=`<div style="display:flex;align-items:center;gap:6px;margin:3px 0">
          <label style="flex:0 0 170px;font-family:var(--mono);font-size:10px;color:var(--tx2)" title="${esc(k)}">${esc(k)}</label>`;
        if(isBool){
          h+=`<input type="checkbox" id="${kid}" ${v?'checked':''} data-sec="${sec}" data-key="${esc(k)}" data-t="bool" style="accent-color:var(--ac3)">`;
        }else if(isNum){
          h+=`<input type="number" id="${kid}" value="${v}" data-sec="${sec}" data-key="${esc(k)}" data-t="num" style="flex:1;padding:3px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:3px;color:var(--tx);font-size:11px;font-family:var(--mono)">`;
        }else{
          h+=`<input type="text" id="${kid}" value="${esc(String(v))}" data-sec="${sec}" data-key="${esc(k)}" data-t="str" ${isMasked?'readonly title="敏感字段, 在 config.json 直接改"':''} style="flex:1;padding:3px 6px;background:${isMasked?'var(--sf2)':'var(--bg)'};border:1px solid var(--bd2);border-radius:3px;color:var(--tx);font-size:11px;font-family:var(--mono)">`;
        }
        h+=`</div>`;
      });
      h+=`</div></details>`;
    });
    h+=`<div style="display:flex;gap:6px;margin-top:8px">
      <button class="abtn" onclick="cfgSave()" style="font-size:10px;padding:3px 10px">💾 保存配置</button>
      <button class="abtn" onclick="loadCfg()" style="font-size:10px;padding:3px 10px">🔄 重载</button>
      <div style="flex:1"></div>
      <span id="cfg-hint" style="font-size:10px;color:var(--tx3);font-family:var(--mono)"></span>
    </div>`;
    el.innerHTML=h;
  }catch(ex){
    el.innerHTML=`<span style="color:var(--dg)">Error: ${ex}</span>`;
  }
}

async function cfgSave(){
  const hint=document.getElementById('cfg-hint');
  if(hint){hint.textContent='⏳ 保存中...';hint.style.color='var(--wn)';}
  const payload={};
  document.querySelectorAll('#cfg-bd input[data-sec]').forEach(inp=>{
    const sec=inp.dataset.sec,key=inp.dataset.key,t=inp.dataset.t;
    if(inp.readOnly)return;   // 脱敏字段跳过
    payload[sec]=payload[sec]||{};
    if(t==='bool')payload[sec][key]=inp.checked;
    else if(t==='num'){const n=parseFloat(inp.value);payload[sec][key]=isNaN(n)?inp.value:n;}
    else payload[sec][key]=inp.value;
  });
  try{
    const r=await fetch('/api/config',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.ok){
      if(hint){hint.textContent=d.needs_restart?'⚠️ 已保存 (重启后生效)':'✅ 已保存 (立即生效)';hint.style.color=d.needs_restart?'var(--wn)':'var(--ac3)';}
      toast(d.needs_restart?'已保存, 部分字段需重启':'配置已保存');
    }else{
      if(hint){hint.textContent='❌ '+(d.error||'保存失败');hint.style.color='var(--dg)';}
    }
  }catch(ex){
    if(hint){hint.textContent='❌ '+ex;hint.style.color='var(--dg)';}
  }
}
async function mdlSwitch(id, backend_url){
  // 必须把 backend_url 传给 server, 否则同名双 entry 服务端只能命中列表第一条, 切不动
  const body={model_id:id};
  if(backend_url) body.backend_url=backend_url;
  const r=await fetch('/api/models/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok){toast('✅ 已切换到 '+id);loadSet();if(typeof chkHealth==='function')chkHealth();if(typeof refreshSbfStatus==='function')refreshSbfStatus();}else toast('❌ '+JSON.stringify(d));}
async function mdlTest(i){
  const m=window._mdlList?.[i];if(!m)return;
  const el=document.getElementById('mtr'+i);const btn=document.getElementById('mt'+i);
  el.textContent='⏳ 测试中（vLLM 首次可能需 10~30s）...';el.style.color='var(--wn)';btn.disabled=true;
  try{const ctrl=new AbortController();const tid=setTimeout(()=>ctrl.abort(),35000);
  const r=await fetch('/api/models/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(m),signal:ctrl.signal});clearTimeout(tid);
  const d=await r.json();
  const parts=[];
  if(d.ok)parts.push('✅ 连通');else parts.push('❌ 失败: '+(d.error||''));
  if(d.latency_ms)parts.push(d.latency_ms+'ms');
  if(d.completion_test)parts.push('completion:'+d.completion_test);
  if(d.vision_test)parts.push('vision:'+d.vision_test);
  if(d.available_models?.length)parts.push('models:'+d.available_models.slice(0,3).join(','));
  el.textContent=parts.join(' · ');el.style.color=d.ok?'var(--ac3)':'var(--dg)';
  }catch(ex){el.textContent=ex.name==='AbortError'?'❌ 超时（30s），请检查后端地址和网络':'❌ '+ex;el.style.color='var(--dg)';}
  btn.disabled=false;}
async function mdlDel(id){if(!await confirmCard('删除模型',`将模型「${id}」从列表移除? 已有会话不受影响`,'删除'))return;
  await fetch('/api/models/'+encodeURIComponent(id),{method:'DELETE'});toast('已删除');loadSet();}
async function mdlAdd(){
  const id=document.getElementById('nm-id').value.trim();
  const btype=document.getElementById('nm-type').value;
  const body={id:id,
    backend_type:btype,
    backend_url:document.getElementById('nm-url').value.trim(),
    api_key:document.getElementById('nm-key').value.trim(),
    api_format:'openai-chat',
    context_window:parseInt(document.getElementById('nm-ctx').value)||200000,
    max_tokens:parseInt(document.getElementById('nm-max').value)||18000,
    supports_vision:document.getElementById('nm-vision').checked,
    enable_thinking:document.getElementById('nm-think').checked,
    thinking_budget:8192,
    /* [v12.7-fix] 旧代码这里写 label:body?.id... 引用尚未定义的 body, label 永远是 undefined.
       改成在 body 定义完成后用局部变量 id/btype 拼出. */
    label:id+' ('+btype+')'};
  if(!body.id||!body.backend_url){toast('请填写 Model ID 和 Backend URL');return;}
  await fetch('/api/models/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  toast('✅ 已添加');loadSet();}
async function doLogout(){await fetch('/api/logout',{method:'POST'});location.reload();}
async function chkHealth(){try{const d=await(await fetch('/api/health')).json();const p=document.getElementById('mpill');if(d.ok){p.textContent=d.model?.split('-')[0]||'ok';p.className='hp';}else{p.textContent='offline';p.className='hp off';}}catch{document.getElementById('mpill').className='hp off';}}
async function checkAuth(){const r=await fetch('/api/auth/status');const d=await r.json();if(d.enabled&&!d.ok)document.getElementById('ls').classList.add('show');else{init();startSbfStatusLoop();_refreshUploadLimit();}}
async function doLogin(){
  const pwd=document.getElementById('lpwd').value;
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pwd})});
  const errEl=document.getElementById('lerr');
  if(r.ok){
    errEl.style.display='none';
    document.getElementById('ls').classList.remove('show');
    init();startSbfStatusLoop();_refreshUploadLimit();_syncThinkChip();_stratChipRender();
    startIdleWatch();
    return;
  }
  /* [Q099 UI-Sprint-D] 429 = 登录限流 → 提示重试时间 */
  if(r.status===429){
    let msg='登录过多, 稍后重试';
    try{const d=await r.json();if(d.detail)msg=d.detail;}catch{}
    errEl.textContent=msg;errEl.style.display='block';
    return;
  }
  errEl.textContent='密码错误';errEl.style.display='block';
}

/* [Q100 UI-Sprint-D] 会话空闲超时: 默认 30 分钟无操作自动登出.
   触摸/键盘/鼠标/滚轮任一操作重置计时器. */
const IDLE_LIMIT_MS = 30 * 60 * 1000;
let _idleLast = Date.now();
let _idleTimer = null;
function _idleReset(){ _idleLast = Date.now(); }
async function _idleCheck(){
  if(Date.now() - _idleLast < IDLE_LIMIT_MS) return;
  try{
    await fetch('/api/logout',{method:'POST'});
  }catch{}
  location.reload();
}
function startIdleWatch(){
  if(_idleTimer) return;
  ['mousemove','mousedown','keydown','wheel','touchstart'].forEach(ev=>document.addEventListener(ev,_idleReset,{passive:true}));
  _idleTimer = setInterval(_idleCheck, 30 * 1000);
  /* 每 5 分钟心跳延签, 保持 cookie 不过期 */
  setInterval(()=>{fetch('/api/auth/heartbeat',{method:'POST'}).catch(()=>{});}, 5*60*1000);
}

/* [Q-think 2026-07-24] composer 🧠 chip: 循环 off/低(2K)/中(4K)/高(16K).
 * 状态存 window._thinkMode, chip 显示当前档位, click PATCH /api/config. */
const _THINK_MODES = [
  {key:'off',label:'off',enable:false,budget:0},
  {key:'low',label:'低',enable:true,budget:2048},
  {key:'mid',label:'中',enable:true,budget:4096},
  {key:'high',label:'高',enable:true,budget:16384},
];
window._thinkMode='mid';
function _thinkChipRender(){
  const btn=document.getElementById('thk');if(!btn)return;
  const m=_THINK_MODES.find(x=>x.key===window._thinkMode)||_THINK_MODES[2];
  const on=m.enable;
  btn.textContent='🧠 '+m.label;
  btn.style.color = on?'var(--ac)':'var(--tx3)';
  btn.style.borderColor = on?'var(--ac)':'var(--bd2)';
  btn.title=`推理强度: ${m.label} (budget=${m.budget}). 点击切换 off/低/中/高`;
}
async function _syncThinkChip(){
  try{
    const r=await fetch('/api/config');const d=await r.json();
    const m=d.config?.model||{};
    const en=!!m.enable_thinking;const bd=+m.thinking_budget||0;
    if(!en) window._thinkMode='off';
    else if(bd<=2048) window._thinkMode='low';
    else if(bd<=8000) window._thinkMode='mid';
    else window._thinkMode='high';
    _thinkChipRender();
  }catch(e){_thinkChipRender();}
}
/* [2026-09-03] 权谋/多方博弈推演 chip: per-message 开关, 状态存 window._stratMode.
 * 开启后本轮请求带 strategist_mode=true, 后端注入 STRATEGIST.md. */
window._stratMode=false;
function _stratChipRender(){
  const btn=document.getElementById('strat');if(!btn)return;
  const on=!!window._stratMode;
  btn.textContent='🎭 '+(on?'推演·开':'推演');
  btn.style.color = on?'var(--ac)':'var(--tx3)';
  btn.style.borderColor = on?'var(--ac)':'var(--bd2)';
}
function toggleStrat(){
  window._stratMode=!window._stratMode;_stratChipRender();
  toast(window._stratMode?'🎭 权谋推演: 开 (本轮拆利益/博弈/变量/正反面)':'🎭 权谋推演: 关');
}
async function cycleThink(){
  const idx=_THINK_MODES.findIndex(x=>x.key===window._thinkMode);
  const next=_THINK_MODES[(idx+1)%_THINK_MODES.length];
  window._thinkMode=next.key;_thinkChipRender();
  try{
    await fetch('/api/config',{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:{enable_thinking:next.enable, thinking_budget:next.budget}})});
    toast(`🧠 推理: ${next.label}${next.enable?` (budget ${next.budget})`:''}`);
  }catch(e){toast('保存失败: '+String(e).slice(0,80));}
}
document.getElementById('lpwd').addEventListener('keydown',ev=>{if(ev.key==='Enter')doLogin();});
/* [v1.13] ESC 智能层级:
   1. DAG 卡片表单打开 → 仅关卡片, 不关面板
   2. DAG 在 connect-mode (linkFrom != null) → 仅取消 link
   3. 输入框 (input/textarea) 内焦点 → 让浏览器默认行为, 不全局拦截
   4. 否则 → 关闭所有打开面板 (旧行为)
*/
document.addEventListener('keydown',ev=>{
  if(ev.key!=='Escape')return;
  /* L1 优先: DAG 卡片表单打开 */
  const dagCard=document.getElementById('dag-card');
  if(dagCard && getComputedStyle(dagCard).display!=='none'){
    ev.preventDefault();_hideDAGCard();return;
  }
  /* L2: DAG 连线模式 */
  if(_dagEditor && _dagEditor._state && _dagEditor._state.linkFrom){
    ev.preventDefault();
    _dagEditor._state.nodes.forEach(n=>n.el.classList.remove('dag-link-src'));
    _dagEditor._state.linkFrom=null;
    if(typeof toast==='function')toast('已取消连线');
    return;
  }
  /* L3: 输入框内焦点不拦截 (除非有面板, 由输入框自己 cancel rename 等) */
  const t=ev.target;
  if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.isContentEditable)){
    /* 如果是 #ct rename input, 让它自己处理 */
    return;
  }
  /* L4 fallback: 关闭打开面板 */
  const open=document.querySelectorAll('.pnl.open');
  if(open.length){ev.preventDefault();open.forEach(p=>{const id=p.id.replace(/^pnl-/,'');closeP(id);});return;}
  if(document.getElementById('sb')?.classList.contains('open')){ev.preventDefault();closeSB();}
});
/* [v1.10 UX] 全局快捷键: / 聚焦输入 / Ctrl+L 滚顶 / Ctrl+K toggle tool 截断 / Ctrl+O toggle thinking 折叠 */
let _showThinkFull=false, _showToolFull=false;
function _typingInForm(t){return t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.isContentEditable);}
document.addEventListener('keydown',ev=>{
  /* "/" 聚焦 #mi (除非已在 input 里) */
  if(ev.key==='/' && !_typingInForm(ev.target)){
    const mi=document.getElementById('mi');
    if(mi){ev.preventDefault();mi.focus();return;}
  }
  /* Ctrl+L 滚到顶 (避免 Cmd+L address bar 冲突, 只 ctrl) */
  if(ev.key==='l'&&ev.ctrlKey&&!ev.metaKey&&!ev.shiftKey){
    const mw=document.getElementById('mw');
    if(mw){ev.preventDefault();mw.scrollTop=0;toast('⬆ 滚到顶');return;}
  }
  /* Ctrl+K toggle tool 结果截断/全文 (兼容大小写 + macOS Cmd) */
  if((ev.key==='k'||ev.key==='K')&&(ev.ctrlKey||ev.metaKey)){
    ev.preventDefault();
    _showToolFull=!_showToolFull;
    document.querySelectorAll('.trc').forEach(el=>{
      el.parentElement?.classList.toggle('open', _showToolFull);
    });
    toast(_showToolFull?'tool_result: 全文':'tool_result: 截断');
    return;
  }
  /* Ctrl+O toggle thinking 折叠/展开 */
  if((ev.key==='o'||ev.key==='O')&&(ev.ctrlKey||ev.metaKey)){
    ev.preventDefault();
    _showThinkFull=!_showThinkFull;
    document.querySelectorAll('.rsh').forEach(el=>{
      el.parentElement?.classList.toggle('open', _showThinkFull);
    });
    toast(_showThinkFull?'thinking: 展开':'thinking: 折叠');
    return;
  }
});
/* [v1.10 UX] #mi 内 ↑/↓ 切历史 user 消息 + Tab 插入空格 (不切焦) */
let _histPtr=-1, _histDraft="";
function _userHistory(){
  return Array.from(document.querySelectorAll('.msg.user .mb')).map(e=>e.innerText.trim()).filter(Boolean);
}
document.getElementById('mi')?.addEventListener('keydown',ev=>{
  const mi=ev.target;
  /* Tab 插入 2 空格不切焦 */
  if(ev.key==='Tab'&&!ev.ctrlKey&&!ev.altKey){
    ev.preventDefault();
    const s=mi.selectionStart, e=mi.selectionEnd;
    mi.value=mi.value.slice(0,s)+'  '+mi.value.slice(e);
    mi.selectionStart=mi.selectionEnd=s+2;
    return;
  }
  /* ↑/↓ 切历史 — 仅当输入框为空 OR 在已浏览状态; 第一行 ↑ / 末行 ↓ 才生效 */
  if(ev.key==='ArrowUp'||ev.key==='ArrowDown'){
    const h=_userHistory();
    if(!h.length)return;
    const isStart=mi.selectionStart===0&&mi.selectionEnd===0;
    const isEnd=mi.selectionStart===mi.value.length;
    if(ev.key==='ArrowUp'&&isStart){
      if(_histPtr===-1)_histDraft=mi.value;
      _histPtr=Math.min(h.length-1,_histPtr+1);
      mi.value=h[h.length-1-_histPtr]||"";
      ev.preventDefault();arx(mi);
      mi.selectionStart=mi.selectionEnd=mi.value.length;
    }else if(ev.key==='ArrowDown'&&isEnd&&_histPtr>=0){
      _histPtr--;
      mi.value=_histPtr<0?_histDraft:(h[h.length-1-_histPtr]||"");
      ev.preventDefault();arx(mi);
      mi.selectionStart=mi.selectionEnd=mi.value.length;
    }
  }else{
    /* 任何其他输入重置历史指针 */
    _histPtr=-1;
  }
});
/* [2026-09-05] 刷新恢复: 页面加载时查有没有运行中的 DAG job, 有则自动重开 Build 实时视图 + 恢复轮询。
   后端 job/exec_log 本就持久, 这里补上"刷新后 UI 自动接回实时视图"这一层。 */
async function _resumeRunningDAG(){
  try{
    const d=await (await fetch('/api/dags/jobs')).json();
    const running=(d.jobs||[]).find(j=>j.status==='running');
    if(!running)return;
    const total=running.step_count||(running.agents||[]).length||0;
    if(_dagBuildView){_dagBuildView.close();_dagBuildView=null;}
    if(typeof window.createDAGBuildView==='function'){
      _dagBuildView=window.createDAGBuildView({jobId:running.job_id,dagName:running.name||'',stepCount:total});
      document.body.appendChild(_dagBuildView.el);
    }
    if(_dagPollTimer)clearInterval(_dagPollTimer);
    _pollDAGJob(running.job_id,total);
    _dagPollTimer=setInterval(()=>_pollDAGJob(running.job_id,total),1500);
    try{typeof toast==='function'&&toast('↻ 已恢复运行中的 DAG 实时视图');}catch(_){}
  }catch(e){console.warn('resume running dag:',e);}
}

async function init(){await loadSess();chkHealth();setInterval(chkHealth,30000);if(sess.length)await swS(sess[0].id);
    _resumeRunningDAG();  /* [2026-09-05] 刷新后自动恢复运行中 DAG 实时视图 */
  document.getElementById('mw').addEventListener('scroll',function(){const w=this;const atBottom=w.scrollHeight-w.scrollTop-w.clientHeight<80;document.getElementById('scroll-btn').classList.toggle('show',!atBottom);});
  _syncThinkChip();
  /* [audit-p0 #19] 网络 offline/online 提示 — 断网时告诉用户, 恢复时清掉 */
  try{
    const _netBanner=()=>{let b=document.getElementById('net-banner');if(b)return b;
      b=document.createElement('div');b.id='net-banner';
      b.style.cssText='position:fixed;top:0;left:0;right:0;z-index:9999;padding:8px 16px;background:#ef4444;color:#fff;text-align:center;font-size:13px;font-weight:600;box-shadow:0 2px 8px rgba(0,0,0,.3)';
      b.textContent='⚠ 网络已断开 — 消息发送将失败, 恢复后请点重试';
      document.body.appendChild(b);return b;};
    window.addEventListener('offline',()=>{_netBanner().style.display='block';});
    window.addEventListener('online',()=>{const b=document.getElementById('net-banner');if(b)b.remove();try{typeof toast==='function'&&toast('✔ 网络已恢复');}catch(_){}});
    if(!navigator.onLine){_netBanner();}
  }catch(e){console.warn('offline init:',e);}
  // [Sprint-A Q002/Q003 2026-07-24] 拖拽 + 粘贴附件挂载
  try{_initAttachDragDrop();_initAttachPaste();}catch(e){console.warn('attach init:',e);}
  // [Sprint-B Q017 2026-07-24] textarea 拖高检测: mouseup 后若 height > 160px 标记 usergrown
  try{
    const mi=document.getElementById('mi');
    if(mi && !mi._resizeHooked){
      mi._resizeHooked=1;
      mi.addEventListener('mouseup',()=>{
        const h=parseInt(mi.style.height||'0',10);
        if(h>160){mi.dataset.usergrown='1';}
        else if(h && h<=160){mi.dataset.usergrown='';}
      });
    }
  }catch(e){console.warn('resize hook:',e);}
  // [Sprint-B Q030 2026-07-24] tool chip 点击展开 args (仅对未内嵌 .targs 展开器的 .ti)
  try{
    document.getElementById('msgs').addEventListener('click',(e)=>{
      const ti=e.target.closest('.ti');
      if(!ti) return;
      if(e.target.closest('.targs')) return;
      if(!ti.querySelector('.ta')) return;
      ti.classList.toggle('exp');
    });
  }catch(e){console.warn('ti expand:',e);}
  // [T-02 2026-07-24] 拉 timer web_notify 通知队列, 60s 一次
  setInterval(async()=>{
    try{
      const r=await fetch('/api/timers/notifications');
      if(!r.ok) return;
      const j=await r.json();
      (j.notifications||[]).forEach(n=>{
        const icon=n.status==='error'?'❌':'✅';
        toast(`${icon} ⏰ ${n.name}: ${(n.message||'').split('\n').pop().slice(0,80)}`);
      });
    }catch(e){}
  }, 60000);
}
