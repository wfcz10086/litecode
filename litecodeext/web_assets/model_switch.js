/* model_switch.js — 对话内切换模型 (本会话) + 测试模型可用性 [per-chat-model 2026-10-19]
 *
 * 输入框旁的「🤖 模型」chip: 显示当前会话用的模型 (指定的 / 跟随默认)。
 * 点开是卡片列表 (不用 prompt): 「跟随默认」+ /api/models 里每个模型, 每行带「测试」按钮。
 * 选中 → PUT /api/sessions/{sid}/model, 只影响本会话, 不改全局默认 (全局切换仍在左下角徽标)。
 * 轮次进行中也能选: 本轮已定模型不变, 下一条消息起生效 (会提示)。
 * 测试 → POST /api/models/test {model_id}, 只读, 显示 ✓/✗ + 延迟。
 */
(function(){
  const S={models:[],info:null,sid:undefined,streaming:false,tests:{}};
  const $=id=>document.getElementById(id);
  const esc_=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const lab=id=>{const m=S.models.find(x=>x.id===id);return (m&&m.label)||id||'—';};
  const curSid=()=>{try{return window.cur||null;}catch(e){return null;}};
  const note=t=>{if(typeof toast==='function')toast(t);};

  async function loadModels(){
    try{const d=await(await fetch('/api/models')).json();S.models=(d.models||[]).filter(m=>m&&m.id);
      /* 同 id 多条 (直连+网关) 只列一次: 会话按 id 解析到第一条 */
      const seen=new Set();S.models=S.models.filter(m=>seen.has(m.id)?false:(seen.add(m.id),true));
      S.active=d.active||'';}catch(e){}
  }
  async function loadInfo(){
    const sid=curSid();
    if(!sid){S.info=null;render();return;}
    try{const r=await fetch(`/api/sessions/${encodeURIComponent(sid)}/model`);
      S.info=r.ok?await r.json():null;}catch(e){S.info=null;}
    render();
  }
  function render(){
    const c=$('msw-chip');if(!c)return;
    const i=S.info;
    let txt,tip;
    if(i&&!i.follows_default){txt=lab(i.effective);tip=`本会话模型: ${i.effective}`;}
    else{const d=(i&&i.default_model)||S.active;txt='默认·'+lab(d);tip=`跟随默认模型: ${d||'?'}`;}
    if(i&&i.missing)tip+=` (指定的 ${i.model_id} 已不在配置中, 回落默认)`;
    c.textContent='🤖 '+txt;c.title=tip+' · 点击切换本会话模型';
    c.classList.toggle('msw-on',!!(i&&!i.follows_default));
  }
  function ensureChip(){
    if($('msw-chip'))return;
    const anchor=$('thk');if(!anchor)return;
    const b=document.createElement('button');
    b.id='msw-chip';b.type='button';
    b.style.cssText='background:transparent;border:1px solid var(--bd2);color:var(--tx2);border-radius:12px;padding:2px 10px;font-size:11px;cursor:pointer;margin-left:4px;white-space:nowrap;max-width:180px;overflow:hidden;text-overflow:ellipsis';
    b.textContent='🤖 模型';
    b.onclick=e=>{e.stopPropagation();togglePicker();};
    anchor.parentNode.insertBefore(b,anchor);
    const st=document.createElement('style');
    st.textContent='#msw-chip.msw-on{border-color:var(--ac,#f0a500);color:var(--ac,#f0a500)}'+
      '#msw-card .msw-row{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:8px;border:1px solid transparent;cursor:pointer}'+
      '#msw-card .msw-row:hover{background:var(--hover-tint,rgba(127,127,127,.08))}'+
      '#msw-card .msw-row.sel{border-color:var(--ac,#f0a500)}'+
      '#msw-card .msw-t{flex:1;min-width:0}#msw-card .msw-n{font-size:12px;font-weight:600;color:var(--tx)}'+
      '#msw-card .msw-i{font-size:10px;color:var(--tx3);font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'+
      '#msw-card .msw-test{font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid var(--bd2);background:transparent;color:var(--tx2);cursor:pointer}'+
      '#msw-card .msw-r{font-size:10px;font-family:var(--mono);min-width:64px;text-align:right}';
    document.head.appendChild(st);
  }
  function rowHtml(id,name,sub,sel,testable){
    const t=S.tests[id];
    const res=!testable?'':t?(t.pending?'<span style="color:var(--wn)">测试中…</span>':
      t.ok?`<span style="color:var(--ac3,#22c55e)" title="${esc_(t.reply_preview||'')}">✓ ${t.latency_ms}ms</span>`:
      `<span style="color:var(--dg,#ef4444)" title="${esc_(t.error||'')}">✗ ${esc_((t.error||'失败').slice(0,18))}</span>`):'';
    return `<div class="msw-row${sel?' sel':''}" data-pick="${esc_(id)}">
      <div class="msw-t"><div class="msw-n">${sel?'✓ ':''}${esc_(name)}</div><div class="msw-i">${esc_(sub)}</div></div>
      <span class="msw-r" data-res="${esc_(id)}">${res}</span>
      ${testable?`<button class="msw-test" data-test="${esc_(id)}" title="发一条极短消息测试是否可用 (不切换模型)">测试</button>`:''}
    </div>`;
  }
  function cardHtml(){
    const i=S.info||{};const sel=i.follows_default===false?i.model_id:'';
    const d=i.default_model||S.active||'';
    const rows=[rowHtml('',`跟随默认`,`当前默认: ${lab(d)}${d&&lab(d)!==d?' · '+d:''}`,!sel,false)];
    for(const m of S.models){
      const meta=[m.id];if(m.supports_vision)meta.push('👁 视觉');if(m.context_window)meta.push('ctx '+m.context_window);
      rows.push(rowHtml(m.id,m.label||m.id,meta.join(' · '),sel===m.id,true));
    }
    const busy=!!window.streaming;
    return `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <div style="font-size:12px;font-weight:600;color:var(--tx)">本会话模型</div>
        <button data-close="1" style="background:transparent;border:none;color:var(--tx2);font-size:16px;cursor:pointer">×</button></div>
      <div style="font-size:10px;color:var(--tx3);margin-bottom:6px">只影响这个对话, 其它对话仍用默认模型。${busy?'<b style="color:var(--wn)">当前轮次进行中, 选择将从下一条消息起生效。</b>':'从下一条消息起生效。'}也可发送 <code>/model 名称</code>。</div>
      <div style="display:flex;flex-direction:column;gap:2px">${rows.join('')}</div>`;
  }
  function closePicker(){$('msw-card')?.remove();document.removeEventListener('click',outside,true);}
  function outside(e){const c=$('msw-card');if(c&&!c.contains(e.target)&&e.target.id!=='msw-chip')closePicker();}
  async function togglePicker(){
    if($('msw-card')){closePicker();return;}
    await loadModels();await loadInfo();
    const chip=$('msw-chip');const r=chip.getBoundingClientRect();
    const c=document.createElement('div');c.id='msw-card';
    const w=Math.min(360,window.innerWidth-24);
    const left=Math.max(12,Math.min(r.left,window.innerWidth-w-12));
    c.style.cssText=`position:fixed;left:${left}px;bottom:${window.innerHeight-r.top+6}px;width:${w}px;max-height:60vh;overflow-y:auto;background:var(--sf,#13181f);border:1px solid var(--bd2);border-radius:14px;padding:10px;z-index:10002;box-shadow:0 12px 40px rgba(0,0,0,.35)`;
    c.innerHTML=cardHtml();
    c.addEventListener('click',onCardClick);
    document.body.appendChild(c);
    setTimeout(()=>document.addEventListener('click',outside,true),0);
  }
  function rerenderCard(){const c=$('msw-card');if(c)c.innerHTML=cardHtml();}
  async function onCardClick(e){
    e.stopPropagation();
    if(e.target.closest('[data-close]')){closePicker();return;}
    const tb=e.target.closest('[data-test]');
    if(tb){testModel(tb.dataset.test);return;}
    const row=e.target.closest('[data-pick]');
    if(row)await pick(row.dataset.pick);
  }
  async function ensureSession(){
    if(curSid())return curSid();
    const d=await(await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'New Chat'})})).json();
    window.cur=d.id;
    try{sess.unshift({id:d.id,name:d.name,count:0});renderSL();}catch(e){}
    return d.id;
  }
  async function pick(mid){
    const sid=await ensureSession();
    try{
      const r=await fetch(`/api/sessions/${encodeURIComponent(sid)}/model`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id:mid||null})});
      const d=await r.json();
      if(!r.ok||!d.ok){note('❌ 切换失败: '+(d.detail||d.error||r.status));return;}
      S.info=d;S.sid=sid;render();closePicker();
      const tail=window.streaming?' (当前轮次不变, 下一条消息起生效)':'';
      note(mid?`✓ 本会话改用 ${lab(mid)}${tail}`:`✓ 本会话跟随默认模型${tail}`);
    }catch(ex){note('❌ '+ex);}
  }
  async function testModel(mid){
    S.tests[mid]={pending:true};rerenderCard();
    try{
      const ctrl=new AbortController();const tid=setTimeout(()=>ctrl.abort(),45000);
      const r=await fetch('/api/models/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id:mid}),signal:ctrl.signal});
      clearTimeout(tid);
      S.tests[mid]=await r.json();
    }catch(ex){S.tests[mid]={ok:false,error:ex.name==='AbortError'?'超时':String(ex)};}
    rerenderCard();
  }
  /* 切会话 / 轮次结束 (可能是 /model 指令) 后刷新 chip */
  function tick(){
    const sid=curSid(),st=!!window.streaming;
    if(!S.active&&!S.loading&&Date.now()-(S.lastTry||0)>5000){   /* 登录前 401 → 登录后补拉 */
      S.loading=true;S.lastTry=Date.now();loadModels().then(()=>{S.loading=false;render();});}
    if(sid!==S.sid||(S.streaming&&!st)){S.sid=sid;loadInfo();}
    S.streaming=st;
  }
  function boot(){
    ensureChip();if(!$('msw-chip'))return;
    loadModels().then(loadInfo);
    setInterval(tick,800);
    /* /model 指令的本地回复很快, 800ms 轮询可能看不到 streaming 翻转 → 发送结束后直接刷新 */
    if(typeof window.send==='function'&&!window.send.__msw){
      const orig=window.send;
      window.send=async function(){try{return await orig.apply(this,arguments);}finally{loadInfo();}};
      window.send.__msw=true;
    }
  }
  window.LCModelSwitch={refresh:()=>loadModels().then(loadInfo),test:testModel,pick};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
