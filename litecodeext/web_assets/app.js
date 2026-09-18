let cur=null,sess=[],streaming=false,_sending=false,_lu=null,_renderTimer=null,_curReader=null;
// [v1.10 P34-d-10] 暴露 cur 到 window 让 e2e/playwright 能读
Object.defineProperty(window, 'cur', {get(){return cur},set(v){cur=v},configurable:true});
const TOOL_ARGS_PREVIEW=90,TOOL_RESULT_PREVIEW=120,LONG_MSG_THRESHOLD=3000;
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
// [Sprint-B Q017] textarea 支持用户拖高: 若用户手动 resize (data-usergrown), arx 不再收缩
function arx(el){
  if(el.dataset.usergrown==='1') return;
  el.style.height='auto';
  el.style.height=Math.min(el.scrollHeight,160)+'px';
}
function sbot(force){const w=document.getElementById('mw');const gap=w.scrollHeight-w.scrollTop-w.clientHeight;if(force||gap<150){w.scrollTop=w.scrollHeight;document.getElementById('scroll-btn').classList.remove('show')}else{document.getElementById('scroll-btn').classList.add('show')}}
function toast(msg,d=2200){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),d)}
function togSB(){const sb=document.getElementById('sb'),ov=document.getElementById('ov');const o=sb.classList.toggle('open');ov.classList.toggle('show',o)}
function closeSB(){document.getElementById('sb').classList.remove('open');document.getElementById('ov').classList.remove('show')}
function openP(n){closeSB();document.getElementById('pnl-'+n).classList.add('open');if(n==='stats')loadStats();if(n==='memory')loadMem();if(n==='settings')loadSet();if(n==='wechat')loadWx();if(n==='wecom')loadWc();if(n==='timer')loadTimers();if(n==='sfiles')loadSFiles();if(n==='vnc')renderVnc();if(n==='project')loadProjects();if(n==='dag')loadDAGList();if(n==='artifacts')loadArtifacts();if(n==='bg')loadBG();if(n==='docker')renderDocker();}
// ── Timer 管理 ──────────────────────────────────────────────
async function loadTimers(){
  const el=document.getElementById('timer-bd');
  el.innerHTML='<div style="color:var(--tx3);font-size:12px;padding:8px 0;text-align:center">加载中...</div>';
  try{
    const d=await(await fetch('/api/timers')).json();
    if(!d.available){el.innerHTML='<div style="color:var(--tx3);padding:12px">定时器模块不可用，请确认 timer_manager.py 已安装。</div>';return;}
    const timers=d.timers||[];
    const typeLabels={once:'单次',cron:'周期'};
    const actLabels={shell:'Shell',agent:'Agent',wechat_msg:'微信',web_notify:'通知'};
    let h='';
    h+=`<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <span style="font-size:12px;font-weight:600;color:var(--tx);font-family:var(--mono)">定时任务 (${timers.length})</span>
      <button class="abtn" onclick="tmrShowAdd()" style="font-size:11px;padding:4px 12px">+ 新建</button>
    </div>`;
    // [T-02/T-03 2026-07-24] Timer 表单大改: content rows=8 + cron 五段拆分 + 高级面板 (通知/描述/超时)
    const _tin='padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:11px';
    h+=`<div id="tmr-add-form" style="display:none;padding:10px 12px;background:var(--sf2);border-radius:var(--rs);border:1px solid var(--bd);margin-bottom:10px">
      <div style="display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap">
        <input id="tmr-name" placeholder="定时器名称" style="flex:1;min-width:100px;${_tin}">
        <select id="tmr-type" style="${_tin}" onchange="tmrTypeChange()">
          <option value="cron">周期(cron)</option><option value="once">单次</option></select>
      </div>
      <!-- 调度: cron 五段 / once ISO -->
      <div id="tmr-sched-cron" style="margin-bottom:6px">
        <div style="display:flex;gap:4px;align-items:center;font-size:10px;color:var(--tx3);margin-bottom:3px;font-family:var(--mono)">
          <span style="width:60px">分</span><span style="width:60px">时</span><span style="width:60px">日</span><span style="width:60px">月</span><span style="width:60px">周</span>
          <span style="margin-left:6px;color:var(--tx2)">→ 预览</span>
        </div>
        <div style="display:flex;gap:4px;align-items:center">
          <input id="tmr-cron-min"  value="0" placeholder="0-59|*|*/5" style="width:60px;${_tin};font-family:var(--mono)" oninput="tmrCronPreview()">
          <input id="tmr-cron-hour" value="9" placeholder="0-23|*"     style="width:60px;${_tin};font-family:var(--mono)" oninput="tmrCronPreview()">
          <input id="tmr-cron-dom"  value="*" placeholder="1-31|*"     style="width:60px;${_tin};font-family:var(--mono)" oninput="tmrCronPreview()">
          <input id="tmr-cron-mon"  value="*" placeholder="1-12|*"     style="width:60px;${_tin};font-family:var(--mono)" oninput="tmrCronPreview()">
          <input id="tmr-cron-dow"  value="*" placeholder="0-6|*"      style="width:60px;${_tin};font-family:var(--mono)" oninput="tmrCronPreview()">
          <code id="tmr-cron-out" style="margin-left:6px;color:var(--ac2);font-size:11px;font-family:var(--mono)">0 9 * * *</code>
        </div>
        <div style="font-size:10px;color:var(--tx3);margin-top:3px">
          快选:
          <a onclick="tmrCronSet(0,9,'*','*','*')" style="color:var(--ac2);cursor:pointer">每天9:00</a> ·
          <a onclick="tmrCronSet('*/30','*','*','*','*')" style="color:var(--ac2);cursor:pointer">每30分钟</a> ·
          <a onclick="tmrCronSet(0,'*','*','*','*')" style="color:var(--ac2);cursor:pointer">每小时整点</a> ·
          <a onclick="tmrCronSet(0,9,'*','*',1)" style="color:var(--ac2);cursor:pointer">每周一9:00</a> ·
          <a onclick="tmrCronSet(0,0,1,'*','*')" style="color:var(--ac2);cursor:pointer">每月1号</a>
        </div>
      </div>
      <div id="tmr-sched-once" style="display:none;margin-bottom:6px">
        <input id="tmr-sched-dt" type="datetime-local" style="width:100%;${_tin};font-family:var(--mono)">
        <div style="font-size:10px;color:var(--tx3);margin-top:3px">选一次性执行时间, 触发后自动禁用</div>
      </div>
      <div style="display:flex;gap:6px;margin-bottom:6px">
        <select id="tmr-action" style="${_tin}" onchange="tmrActChange()">
          <option value="shell">Shell 命令</option><option value="agent">Agent 任务</option><option value="wechat_msg">微信消息</option><option value="web_notify">Web 通知</option><option value="dag">DAG 触发</option></select>
        <input id="tmr-target" placeholder="目标 (可选)" style="flex:1;${_tin}">
      </div>
      <textarea id="tmr-content" placeholder="执行内容 (支持多行 · shell 命令 / agent prompt / 消息文本 / DAG 名)" rows="8" style="width:100%;${_tin};font-family:var(--mono);resize:vertical;margin-bottom:6px"></textarea>
      <details style="margin-bottom:6px;border:1px dashed var(--bd);border-radius:4px;padding:6px 8px">
        <summary style="cursor:pointer;font-size:10px;color:var(--tx2);user-select:none">🔔 通知 / 描述 (高级)</summary>
        <div style="margin-top:6px;display:grid;grid-template-columns:auto auto 1fr;gap:6px;align-items:center;font-size:10px">
          <label style="display:flex;align-items:center;gap:4px;color:var(--tx2)"><input type="checkbox" id="tmr-notify-err"> 失败通知</label>
          <label style="display:flex;align-items:center;gap:4px;color:var(--tx2)"><input type="checkbox" id="tmr-notify-ok"> 成功通知</label>
          <span></span>
          <span style="color:var(--tx3)">通道</span>
          <select id="tmr-notify-ch" style="${_tin}"><option value="web">Web 站内</option><option value="wechat">微信</option></select>
          <input id="tmr-notify-tgt" placeholder="wechat: bot_id:user_id (Web 通道留空)" style="${_tin}">
        </div>
        <textarea id="tmr-desc" placeholder="备注 / 描述 (可选, 便于回顾)" rows="2" style="width:100%;${_tin};margin-top:6px;resize:vertical"></textarea>
      </details>
      <div style="display:flex;gap:6px;justify-content:flex-end">
        <button class="abtn" onclick="document.getElementById('tmr-add-form').style.display='none'" style="font-size:10px;padding:3px 10px">取消</button>
        <button class="abtn" onclick="tmrAdd()" style="font-size:10px;padding:3px 10px;border-color:var(--ac3);color:var(--ac3)">创建</button>
      </div>
    </div>`;
    if(!timers.length){
      h+='<div style="color:var(--tx3);font-size:12px;padding:16px 0;text-align:center">暂无定时任务</div>';
    }else{
      timers.forEach(t=>{
        const act=t.action||{};
        const en=t.enabled!==false;
        const sc=en?'var(--ac3)':'var(--tx3)';
        const last=t.last_run?new Date(t.last_run*1000).toLocaleString('zh-CN',{hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'未执行';
        // [T-02 2026-07-24] 状态 pill: 优先 last_status, 无则由 enabled/last_run 推断
        let stTxt='⏳待执行', stClr='var(--tx3)', stBg='var(--sf2)';
        if(!en){stTxt='⏸暂停';stClr='var(--tx3)';}
        else if(t.last_status==='ok'){stTxt='✅成功';stClr='#22c55e';stBg='rgba(34,197,94,.12)';}
        else if(t.last_status==='error'){stTxt='❌失败';stClr='#ef4444';stBg='rgba(239,68,68,.12)';}
        else if(t.last_run){stTxt='✅成功';stClr='#22c55e';stBg='rgba(34,197,94,.12)';}
        const dur=t.last_duration_ms?(t.last_duration_ms>=1000?(t.last_duration_ms/1000).toFixed(1)+'s':t.last_duration_ms+'ms'):'';
        const nBadges=[];
        if(t.notify_on_error) nBadges.push('🔔失败');
        if(t.notify_on_success) nBadges.push('🔔成功');
        const notifyLine=nBadges.length?`<span style="font-size:9px;color:var(--ac2);margin-left:6px" title="通道: ${esc(t.notify_channel||'web')} / 目标: ${esc(t.notify_target||'-')}">${nBadges.join(' ')}</span>`:'';
        const resPrev=t.last_result?esc(String(t.last_result).slice(0,120)):'';
        const errPrev=t.last_error?esc(String(t.last_error).slice(0,120)):'';
        h+=`<div style="padding:8px 10px;background:var(--sf);border:1px solid var(--bd);border-radius:var(--rs);margin-bottom:6px;opacity:${en?1:.6}">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap">
            <span style="font-size:12px;font-weight:600;color:var(--tx)">${esc(t.name)}</span>
            <span style="font-size:9px;padding:1px 5px;border-radius:3px;background:var(--sf2);color:${sc}">${typeLabels[t.type]||t.type}</span>
            <span style="font-size:9px;padding:1px 5px;border-radius:3px;background:var(--sf2);color:var(--tx3)">${actLabels[(act).type]||(act).type}</span>
            <span style="font-size:9px;padding:1px 5px;border-radius:3px;background:${stBg};color:${stClr};font-weight:600">${stTxt}</span>
            ${notifyLine}
            <span style="font-size:10px;color:var(--tx3);margin-left:auto;font-family:var(--mono)">${t.run_count||0}次</span>
          </div>
          <div style="font-size:10px;color:var(--tx3);font-family:var(--mono);margin-bottom:3px">${esc(t.schedule)} ${t.next_run?'→ next: '+t.next_run:''}</div>
          ${t.description?`<div style="font-size:10px;color:var(--tx3);font-style:italic;margin-bottom:3px">📝 ${esc(t.description)}</div>`:''}
          <div style="font-size:10px;color:var(--tx2);margin-bottom:4px">${esc(((act).content||'').slice(0,80))}</div>
          <div style="font-size:9px;color:var(--tx3);margin-bottom:3px">
            上次: ${last}${dur?' · '+dur:''}
          </div>
          ${resPrev?`<div style="font-size:10px;color:var(--tx2);font-family:var(--mono);background:var(--bg);padding:3px 6px;border-radius:3px;margin-bottom:3px;white-space:pre-wrap;word-break:break-all;max-height:60px;overflow:auto" title="上次执行输出">▸ ${resPrev}</div>`:''}
          ${errPrev?`<div style="font-size:10px;color:#ef4444;background:rgba(239,68,68,.08);padding:3px 6px;border-radius:3px;margin-bottom:3px;white-space:pre-wrap;word-break:break-all;max-height:60px;overflow:auto">❌ ${errPrev}</div>`:''}
          <div style="display:flex;gap:4px;margin-top:4px">
            <button class="abtn" onclick="tmrRun('${t.id}')" style="font-size:9px;padding:2px 8px">▶ 执行</button>
            <button class="abtn" onclick="tmrToggle('${t.id}',${!en})" style="font-size:9px;padding:2px 8px">${en?'暂停':'启用'}</button>
            <button class="abtn" onclick="tmrCopy('${t.id}')" style="font-size:9px;padding:2px 8px" title="复制定时器到表单">📋 复制</button>
            <button class="abtn dng" onclick="tmrDel('${t.id}')" style="font-size:9px;padding:2px 8px">删除</button>
          </div>
          <div class="thb" id="thb-${t.id}" style="margin-top:4px;border-top:1px solid var(--bd);padding-top:3px">
            <div class="thh" onclick="tmrHistory('${t.id}')">
              <span style="font-size:9px;color:var(--tx3);cursor:pointer;user-select:none">📋 历史 (${(t.history||[]).length}) <span class="arr">▶</span></span>
            </div>
            <div class="thc" id="thc-${t.id}" style="display:none"></div>
          </div>
        </div>`;
      });
    }
    el.innerHTML=h;
  }catch(ex){el.innerHTML=`<div style="color:var(--dg)">Error: ${ex}</div>`;}
}
function tmrShowAdd(){document.getElementById('tmr-add-form').style.display='block';tmrCronPreview();}
// [T-03 2026-07-24] cron/once 面板切换
function tmrTypeChange(){
  const isC=document.getElementById('tmr-type').value==='cron';
  const c=document.getElementById('tmr-sched-cron'), o=document.getElementById('tmr-sched-once');
  if(c) c.style.display=isC?'block':'none';
  if(o) o.style.display=isC?'none':'block';
}
function tmrActChange(){/* target 占位提示随 action 变化, 保留扩展位 */
  const a=document.getElementById('tmr-action').value;
  const tgt=document.getElementById('tmr-target');
  if(!tgt) return;
  tgt.placeholder = a==='wechat_msg' ? 'bot_id:user_id' :
                    a==='agent'      ? 'session_id (可选)' :
                    a==='dag'        ? 'DAG 名 (也可写在内容)' :
                                       '目标 (可选)';
}
function tmrCronPreview(){
  const m=(document.getElementById('tmr-cron-min')||{}).value||'*';
  const h=(document.getElementById('tmr-cron-hour')||{}).value||'*';
  const d=(document.getElementById('tmr-cron-dom')||{}).value||'*';
  const mo=(document.getElementById('tmr-cron-mon')||{}).value||'*';
  const w=(document.getElementById('tmr-cron-dow')||{}).value||'*';
  const out=document.getElementById('tmr-cron-out');
  if(out) out.textContent=`${m} ${h} ${d} ${mo} ${w}`;
}
function tmrCronSet(m,h,d,mo,w){
  document.getElementById('tmr-cron-min').value=m;
  document.getElementById('tmr-cron-hour').value=h;
  document.getElementById('tmr-cron-dom').value=d;
  document.getElementById('tmr-cron-mon').value=mo;
  document.getElementById('tmr-cron-dow').value=w;
  tmrCronPreview();
}
// [T-02 2026-07-24] 收 notify/description 新字段, 组装 cron 五段或 ISO
async function tmrAdd(){
  const name=(document.getElementById('tmr-name').value||'').trim();
  if(!name){toast('❌ 名称必填');return;}
  const type=document.getElementById('tmr-type').value;
  let schedule='';
  if(type==='cron'){
    const p=['min','hour','dom','mon','dow'].map(k=>(document.getElementById('tmr-cron-'+k).value||'*').trim()||'*');
    schedule=p.join(' ');
  }else{
    const dt=document.getElementById('tmr-sched-dt').value;
    if(!dt){toast('❌ 单次执行需要指定时间');return;}
    schedule=dt.length===16?dt+':00':dt;  // datetime-local → ISO
  }
  const body={
    name, type, schedule,
    action_type:document.getElementById('tmr-action').value,
    action_target:document.getElementById('tmr-target').value,
    action_content:document.getElementById('tmr-content').value,
    description:document.getElementById('tmr-desc').value||'',
    notify_on_error:document.getElementById('tmr-notify-err').checked,
    notify_on_success:document.getElementById('tmr-notify-ok').checked,
    notify_channel:document.getElementById('tmr-notify-ch').value,
    notify_target:document.getElementById('tmr-notify-tgt').value||'',
  };
  const r=await fetch('/api/timers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){const j=await r.json().catch(()=>({}));toast('❌ '+(j.error||j.detail||'创建失败'));return;}
  document.getElementById('tmr-add-form').style.display='none';toast('✅ 定时器已创建');loadTimers();
}
async function tmrRun(id){const d=await(await fetch(`/api/timers/${id}/run`,{method:'POST'})).json();toast(d.ok?'✅ 执行完成':'❌ '+(d.error||'失败'));loadTimers();}
async function tmrToggle(id,en){await fetch(`/api/timers/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:en})});loadTimers();}
async function tmrDel(id){if(!await confirmCard('删除定时器','将此定时任务及其历史记录移除, 不可恢复','删除'))return;await fetch(`/api/timers/${id}`,{method:'DELETE'});toast('已删除');loadTimers();}
/* [v1.11 P59] 复制定时器: 把现有 timer 的字段填到新建表单, 用户改完点 [创建] 即得新副本 */
async function tmrCopy(id){
  try{
    const d=await(await fetch('/api/timers')).json();
    const t=(d.timers||[]).find(x=>x.id===id);
    if(!t){toast('未找到');return;}
    tmrShowAdd();
    document.getElementById('tmr-name').value=(t.name||'')+' (副本)';
    document.getElementById('tmr-type').value=t.type||'cron';
    tmrTypeChange();
    if((t.type||'cron')==='cron'){
      const p=(t.schedule||'* * * * *').split(/\s+/);
      while(p.length<5) p.push('*');
      document.getElementById('tmr-cron-min').value=p[0];
      document.getElementById('tmr-cron-hour').value=p[1];
      document.getElementById('tmr-cron-dom').value=p[2];
      document.getElementById('tmr-cron-mon').value=p[3];
      document.getElementById('tmr-cron-dow').value=p[4];
      tmrCronPreview();
    }else{
      document.getElementById('tmr-sched-dt').value=(t.schedule||'').slice(0,16);
    }
    const act=t.action||{};
    document.getElementById('tmr-action').value=act.type||'shell';
    tmrActChange();
    document.getElementById('tmr-target').value=act.target||'';
    document.getElementById('tmr-content').value=act.content||'';
    document.getElementById('tmr-desc').value=t.description||'';
    document.getElementById('tmr-notify-err').checked=!!t.notify_on_error;
    document.getElementById('tmr-notify-ok').checked=!!t.notify_on_success;
    document.getElementById('tmr-notify-ch').value=t.notify_channel||'web';
    document.getElementById('tmr-notify-tgt').value=t.notify_target||'';
    toast('📋 已填入表单, 改完点[创建]');
  }catch(e){toast('复制失败: '+e);}
}
async function tmrHistory(id){
  const thb=document.getElementById('thb-'+id);
  const thc=document.getElementById('thc-'+id);
  if(!thb||!thc)return;
  const isOpen=thb.classList.contains('open');
  if(isOpen){thb.classList.remove('open');thc.style.display='none';return;}
  thb.classList.add('open');
  thc.style.display='block';
  thc.innerHTML='<div style="color:var(--tx3);font-size:10px;padding:4px 0">加载中...</div>';
  try{
    const d=await(await fetch(`/api/timers/${id}/history`)).json();
    const hist=d.history||[];
    if(!hist.length){thc.innerHTML='<div style="color:var(--tx3);font-size:10px;padding:4px 0">暂无历史记录</div>';return;}
    let h='';
    hist.slice().reverse().forEach(e=>{
      const dt=new Date(e.ts*1000).toLocaleString('zh-CN',{hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
      const sc=e.status==='error'?'var(--dg)':'var(--ac3)';
      h+=`<div style="padding:2px 0;border-bottom:1px solid var(--bd);font-size:9px;font-family:var(--mono)">`;
      h+=`<span style="color:var(--tx3)">${dt}</span> `;
      h+=`<span style="color:${sc}">[${e.status}]</span> `;
      h+=`<span style="color:var(--tx2)">${esc((e.result||'').slice(0,80))}</span> `;
      h+=`<span style="color:var(--tx3)">(${e.duration_ms}ms)</span>`;
      if(e.error)h+=`<div style="color:var(--dg);padding-left:8px">${esc(e.error.slice(0,80))}</div>`;
      h+='</div>';
    });
    thc.innerHTML=h;
  }catch(ex){thc.innerHTML=`<div style="color:var(--dg);font-size:10px">Error: ${ex}</div>`;}
}
// ── Session 导出 ────────────────────────────────────────────
async function exportSession(){
  if(!cur){toast('请先选择会话');return;}
  window.open(`/api/sessions/${cur}/export?fmt=md`,'_blank');
  toast('📥 导出中...');
}
// ── Artifacts 面板 (S1-N /api/artifacts_v2) ─────────────────
let _artOwnerFilter='';
function _artKindIcon(k){return {image:'🖼',png:'🖼',jpg:'🖼',jpeg:'🖼',svg:'🖼',pdf:'📕',html:'🌐',json:'📄',txt:'📄',log:'📜',pptx:'📊',docx:'📝',dxf:'📐',stl:'📦',step:'📦',bytes:'📎'}[k]||'📎';}
function _artFmtSz(n){return n<1024?n+'B':n<1048576?(n/1024).toFixed(1)+'K':(n/1048576).toFixed(1)+'M';}
async function loadArtifacts(){
  const el=document.getElementById('art-bd');
  if(!el)return;
  el.innerHTML='<div style="color:var(--tx3);padding:8px;text-align:center">加载中…</div>';
  try{
    const [ownRes,listRes]=await Promise.all([
      fetch('/api/artifacts_v2/owners'),
      fetch('/api/artifacts_v2'+(_artOwnerFilter?('?owner='+encodeURIComponent(_artOwnerFilter)):''))
    ]);
    const owners=(await ownRes.json()).owners||[];
    const items=(await listRes.json()).items||[];
    let h=`<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;font-size:11px">
      <span class="art-chip${_artOwnerFilter===''?' on':''}" onclick="_artSetOwner('')">全部 (${items.length})</span>`;
    owners.forEach(o=>{
      h+=`<span class="art-chip${_artOwnerFilter===o?' on':''}" onclick="_artSetOwner('${esc(o)}')" title="${esc(o)}">${esc(o.length>28?o.slice(0,26)+'…':o)}</span>`;
    });
    h+=`</div>`;
    if(!items.length){el.innerHTML=h+'<div style="color:var(--tx3);font-size:12px;padding:20px 0;text-align:center">暂无产出物</div>';return;}
    h+='<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px">';
    items.forEach(a=>{
      const dt=new Date(a.ts*1000).toLocaleString('zh-CN',{hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
      const isImg=['image','png','jpg','jpeg','gif','webp','svg'].includes(a.kind);
      const preview=isImg?`<img src="/api/artifacts_v2/blob/${a.aid}" style="width:100%;height:100px;object-fit:cover;border-radius:6px;background:#eee">`:`<div style="height:100px;display:flex;align-items:center;justify-content:center;font-size:36px;background:var(--bg2);border-radius:6px">${_artKindIcon(a.kind)}</div>`;
      h+=`<div style="border:1px solid var(--bd);border-radius:8px;padding:8px;background:var(--bg1);display:flex;flex-direction:column;gap:6px">
        ${preview}
        <div style="font-size:11px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(a.name||a.aid)}">${esc(a.name||a.aid)}</div>
        <div style="font-size:10px;color:var(--tx3);font-family:var(--mono);display:flex;justify-content:space-between"><span>${esc(a.kind)}</span><span>${_artFmtSz(a.size||0)}</span></div>
        <div style="font-size:10px;color:var(--tx3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(a.owner||'')}">${esc(a.owner||'')}</div>
        <div style="font-size:10px;color:var(--tx3)">${dt}</div>
        <div style="display:flex;gap:4px;margin-top:2px">
          <a class="abtn" href="/api/artifacts_v2/blob/${a.aid}" target="_blank" style="font-size:10px;padding:3px 8px;flex:1;text-align:center">🔍</a>
          <a class="abtn" href="/api/artifacts_v2/blob/${a.aid}?download=1" style="font-size:10px;padding:3px 8px;flex:1;text-align:center">⬇</a>
          <button class="abtn dng" onclick="artDel('${a.aid}')" style="font-size:10px;padding:3px 8px;flex:1">✕</button>
        </div>
      </div>`;
    });
    h+='</div>';
    if(_artOwnerFilter){
      h+=`<div style="margin-top:12px"><button class="abtn dng" onclick="artPurgeOwner('${esc(_artOwnerFilter)}')" style="font-size:11px">🗑 清空该 owner (${items.length})</button></div>`;
    }
    el.innerHTML=h;
  }catch(ex){el.innerHTML=`<div style="color:var(--dg);padding:8px">Error: ${esc(String(ex))}</div>`;}
}
function _artSetOwner(o){_artOwnerFilter=o;loadArtifacts();}
async function artDel(aid){if(!await confirmCard('删除产出物','该文件将从磁盘移除, 不可恢复','删除'))return;await fetch('/api/artifacts_v2/'+aid,{method:'DELETE'});toast('已删除');loadArtifacts();}
async function artPurgeOwner(o){if(!await confirmCard('清空 owner',`将 owner「${o}」下所有产出物一次性删除? 不可恢复`,'清空'))return;const d=await(await fetch('/api/artifacts_v2/owner/'+encodeURIComponent(o),{method:'DELETE'})).json();toast(`✅ 已清空 ${d.purged_files||0} 项`);_artOwnerFilter='';loadArtifacts();}
// ── BG procs 面板 (/api/bgprocs) ────────────────────────────
async function loadBG(){
  const el=document.getElementById('bg-bd');
  if(!el)return;
  el.innerHTML='<div style="color:var(--tx3);padding:8px;text-align:center">加载中…</div>';
  try{
    const d=await(await fetch('/api/bgprocs')).json();
    const procs=d.procs||d.bg||[];
    if(!procs.length){el.innerHTML='<div style="color:var(--tx3);font-size:12px;padding:20px 0;text-align:center">暂无后台任务</div><div style="text-align:center;margin-top:8px"><button class="abtn" onclick="loadBG()" style="font-size:11px">🔄 刷新</button></div>';return;}
    let h=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><span style="font-size:12px;color:var(--tx2)">共 ${procs.length} 个后台任务</span><button class="abtn" onclick="loadBG()" style="font-size:10px;padding:3px 8px">🔄</button></div>`;
    h+='<div style="display:flex;flex-direction:column;gap:8px">';
    procs.forEach(p=>{
      const pid=p.pid||p.id||'?';
      const st=p.status||p.state||'running';
      const stCol=st==='running'?'var(--ac3)':(st==='exited'||st==='done'?'var(--tx3)':(st==='error'||st==='killed'?'var(--dg)':'var(--wn)'));
      const started=p.started_at?new Date(p.started_at*1000).toLocaleTimeString('zh-CN',{hour12:false}):'';
      const cmd=p.cmd||p.command||p.name||'';
      h+=`<div style="border:1px solid var(--bd);border-radius:8px;padding:10px;background:var(--bg1)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <span style="font-family:var(--mono);font-size:11px"><b>#${esc(pid)}</b> <span style="color:${stCol};margin-left:6px">● ${esc(st)}</span></span>
          <div style="display:flex;gap:4px">
            <button class="abtn" onclick="bgTail('${esc(pid)}')" style="font-size:10px;padding:3px 8px">📜 log</button>
            <button class="abtn dng" onclick="bgKill('${esc(pid)}')" style="font-size:10px;padding:3px 8px">✕ kill</button>
          </div>
        </div>
        <div style="font-family:var(--mono);font-size:11px;color:var(--tx2);word-break:break-all;padding:6px;background:var(--bg2);border-radius:4px">${esc(cmd)}</div>
        ${started?`<div style="font-size:10px;color:var(--tx3);margin-top:4px">起于 ${started}</div>`:''}
        <div id="bg-tail-${esc(pid)}" style="display:none;margin-top:6px;padding:6px;background:#111;color:#8f8;font-family:var(--mono);font-size:10px;max-height:200px;overflow:auto;border-radius:4px;white-space:pre-wrap"></div>
      </div>`;
    });
    h+='</div>';
    el.innerHTML=h;
  }catch(ex){el.innerHTML=`<div style="color:var(--dg);padding:8px">Error: ${esc(String(ex))}</div>`;}
}
async function bgKill(pid){if(!await confirmCard('结束后台任务',`确定要 kill 进程 #${pid}? 正在运行的输出会中断`,'Kill'))return;await fetch('/api/bgprocs/'+encodeURIComponent(pid),{method:'DELETE'});toast('已 kill');setTimeout(loadBG,300);}
async function bgTail(pid){
  const box=document.getElementById('bg-tail-'+pid);
  if(!box)return;
  if(box.style.display==='block'){box.style.display='none';return;}
  box.style.display='block';box.textContent='拉取日志中…';
  try{
    const d=await(await fetch('/api/bgprocs/'+encodeURIComponent(pid)+'/tail?lines=100').catch(()=>null));
    if(d && d.ok){const j=await d.json();box.textContent=(j.log||j.tail||'(空)');}
    else box.textContent='(tail 端点尚未实现, 见 execute_shell tail_log)';
  }catch(ex){box.textContent='Error: '+ex;}
}
// ── Workspace 文件浏览器 ────────────────────────────────────
let _wsCurPath='';
const _imgExts=new Set(['png','jpg','jpeg','gif','webp','svg','bmp','ico']);
const _txtExts=new Set(['txt','md','py','js','ts','json','yaml','yml','toml','ini','sh','css','html','xml','csv','log','conf','env','sql','go','rs','c','cpp','h','java','rb','php']);
function _fmtSize(n){return n<1024?n+'B':n<1048576?(n/1024).toFixed(1)+'KB':(n/1048576).toFixed(1)+'MB';}
async function loadSFiles(){
  _wsCurPath='';
  await _loadWsDir('');
}
async function _loadWsDir(path){
  _wsCurPath=path;
  const el=document.getElementById('sf-bd');
  document.getElementById('sf-title').textContent='📁 '+(path||'/workspace');
  el.innerHTML='<div style="color:var(--tx3);padding:8px;text-align:center">加载中...</div>';
  try{
    const d=await(await fetch(`/api/workspace/files?path=${encodeURIComponent(path)}`)).json();
    const files=d.files||[];
    let h='';
    // 导航条
    const parts=path?path.split('/').filter(Boolean):[];
    h+=`<div style="display:flex;align-items:center;gap:4px;margin-bottom:8px;flex-wrap:wrap;font-size:11px;font-family:var(--mono)">
      <span style="color:var(--ac2);cursor:pointer" onclick="_loadWsDir('')">workspace</span>`;
    let acc='';
    parts.forEach(p=>{acc+=(acc?'/':'')+p;const a=acc;h+=` <span style="color:var(--tx3)">/</span> <span style="color:var(--ac2);cursor:pointer" onclick="_loadWsDir('${esc(a)}')">${esc(p)}</span>`;});
    h+=`</div>`;
    // Session files 快捷入口
    if(!path&&cur){
      h+=`<div style="margin-bottom:8px;display:flex;gap:6px;flex-wrap:wrap">
        <button class="abtn" onclick="_loadWsDir('sessions/${cur}')" style="font-size:10px;padding:3px 8px">📄 当前会话文件</button>
        <button class="abtn" onclick="exportSession()" style="font-size:10px;padding:3px 8px">📥 导出MD</button>
        <button class="abtn" onclick="window.open('/api/sessions/${cur}/export?fmt=json','_blank')" style="font-size:10px;padding:3px 8px">📥 JSON</button>
      </div>`;
    }
    if(!files.length){h+='<div style="color:var(--tx3);padding:16px;text-align:center;font-size:12px">空目录</div>';el.innerHTML=h;return;}
    // 分目录和文件
    const dirs=files.filter(f=>f.is_dir).sort((a,b)=>a.name.localeCompare(b.name));
    const fls=files.filter(f=>!f.is_dir).sort((a,b)=>b.mtime-a.mtime);
    dirs.forEach(f=>{
      h+=`<div style="display:flex;align-items:center;gap:6px;padding:6px 8px;border-bottom:1px solid var(--bd);font-size:12px;cursor:pointer" onclick="_loadWsDir('${esc(f.path)}')">
        <span style="font-size:14px">📁</span>
        <span style="color:var(--tx);flex:1">${esc(f.name)}/</span>
      </div>`;
    });
    fls.forEach(f=>{
      const ext=(f.name.split('.').pop()||'').toLowerCase();
      const icon=_imgExts.has(ext)?'🖼️':_txtExts.has(ext)?'📄':ext==='pdf'?'📕':'📦';
      const t=new Date(f.mtime*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
      h+=`<div style="display:flex;align-items:center;gap:6px;padding:5px 8px;border-bottom:1px solid var(--bd);font-size:12px">
        <span style="font-size:13px">${icon}</span>
        <span style="color:var(--tx);flex:1;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" onclick="_previewFile('${esc(f.path)}','${ext}')">${esc(f.name)}</span>
        <span style="color:var(--tx3);font-size:10px;flex-shrink:0">${_fmtSize(f.size)}</span>
        <span style="color:var(--tx3);font-size:10px;flex-shrink:0">${t}</span>
        <a href="/api/workspace/download?path=${encodeURIComponent(f.path)}" style="color:var(--ac2);font-size:10px;text-decoration:none;flex-shrink:0" download>↓</a>
      </div>`;
    });
    el.innerHTML=h;
  }catch(ex){el.innerHTML=`<div style="color:var(--dg);padding:8px">Error: ${ex}</div>`;}
}
async function _previewFile(path,ext){
  const el=document.getElementById('sf-bd');
  const backBtn=`<button class="abtn" onclick="_loadWsDir('${esc(_wsCurPath)}')" style="font-size:10px;padding:2px 8px;margin-bottom:8px">← 返回</button>`;
  const dlBtn=`<a href="/api/workspace/download?path=${encodeURIComponent(path)}" class="abtn" style="font-size:10px;padding:2px 8px;text-decoration:none;margin-left:4px" download>↓ 下载</a>`;
  const fname=path.split('/').pop();
  // 图片: 直接显示
  if(_imgExts.has(ext)){
    el.innerHTML=`${backBtn}${dlBtn}<div style="font-size:11px;color:var(--tx2);margin:6px 0;font-family:var(--mono)">${esc(fname)}</div>
      <img src="/api/workspace/preview?path=${encodeURIComponent(path)}" style="max-width:100%;max-height:60vh;border-radius:var(--rs);border:1px solid var(--bd)" alt="${esc(fname)}">`;
    return;
  }
  // PDF: iframe
  if(ext==='pdf'){
    el.innerHTML=`${backBtn}${dlBtn}<div style="font-size:11px;color:var(--tx2);margin:6px 0">${esc(fname)}</div>
      <iframe src="/api/workspace/preview?path=${encodeURIComponent(path)}" style="width:100%;height:60vh;border:1px solid var(--bd);border-radius:var(--rs)"></iframe>`;
    return;
  }
  // [preview-2026-05] 视频
  if(['mp4','webm','mov','m4v'].includes(ext)){
    el.innerHTML=`${backBtn}${dlBtn}<div style="font-size:11px;color:var(--tx2);margin:6px 0;font-family:var(--mono)">🎬 ${esc(fname)}</div>
      <video controls preload="metadata" style="max-width:100%;max-height:60vh;border-radius:var(--rs);border:1px solid var(--bd);background:#000"><source src="/api/workspace/preview?path=${encodeURIComponent(path)}"></video>`;
    return;
  }
  // [preview-2026-05] 音频
  if(['mp3','wav','ogg','m4a','flac'].includes(ext)){
    el.innerHTML=`${backBtn}${dlBtn}<div style="font-size:11px;color:var(--tx2);margin:6px 0;font-family:var(--mono)">🎵 ${esc(fname)}</div>
      <audio controls style="width:100%;margin-top:8px"><source src="/api/workspace/preview?path=${encodeURIComponent(path)}"></audio>`;
    return;
  }
  // [preview-2026-05] Office 文档: 优先转 PDF 在线预览 (LibreOffice), 503 降级到下载
  if(['pptx','ppt','docx','doc','xlsx','xls','odt','odp','ods'].includes(ext)){
    const icon={pptx:'📊',ppt:'📊',docx:'📝',doc:'📝',xlsx:'📈',xls:'📈',odp:'📊',odt:'📝',ods:'📈'}[ext]||'📄';
    el.innerHTML=`${backBtn}${dlBtn}<div style="font-size:11px;color:var(--tx2);margin:6px 0;font-family:var(--mono)">${icon} ${esc(fname)}</div>
      <div id="office-preview" style="padding:20px;text-align:center;color:var(--tx3);font-size:12px">🔄 转换 PDF 预览中（首次约 5-15 秒）...</div>`;
    try{
      const r=await fetch(`/api/workspace/preview_office?path=${encodeURIComponent(path)}`,{method:'HEAD'});
      if(r.ok){
        document.getElementById('office-preview').outerHTML =
          `<iframe src="/api/workspace/preview_office?path=${encodeURIComponent(path)}" style="width:100%;height:60vh;border:1px solid var(--bd);border-radius:var(--rs)"></iframe>`;
      } else {
        const tip = r.status===503 ? 'LibreOffice 未装（需重 build 镜像）' : `预览失败 (${r.status})`;
        document.getElementById('office-preview').innerHTML =
          `<div style="font-size:48px;line-height:1">${icon}</div>
           <div style="font-size:13px;color:var(--tx);margin:8px 0">${tip}</div>
           <a href="/api/workspace/download?path=${encodeURIComponent(path)}" class="abtn" style="font-size:12px;padding:6px 18px;text-decoration:none;border-color:var(--ac3);color:var(--ac3)" download>↓ 下载查看</a>`;
      }
    }catch(e){
      document.getElementById('office-preview').innerHTML = `<div style="color:var(--dg)">网络错误: ${e}</div>`;
    }
    return;
  }
  // 文本: fetch + 代码高亮
  if(_txtExts.has(ext)){
    try{
      const d=await(await fetch(`/api/workspace/preview?path=${encodeURIComponent(path)}`)).json();
      if(d.type==='text'){
        const content=d.content||'';
        const editable=fname.endsWith('.md')&&path.includes('sessions/');
        el.innerHTML=`${backBtn}${dlBtn}<div style="font-size:11px;color:var(--tx2);margin:6px 0;font-family:var(--mono)">${esc(fname)} (${_fmtSize(d.size)})</div>
          <textarea id="ws-editor" style="width:100%;min-height:300px;max-height:60vh;padding:8px;background:#0d1117;border:1px solid var(--bd);border-radius:var(--rs);color:var(--tx);font-size:12px;font-family:var(--mono);resize:vertical;line-height:1.5">${esc(content)}</textarea>
          ${editable?`<div style="display:flex;gap:6px;margin-top:4px;justify-content:flex-end">
            <button class="abtn" onclick="_saveWsFile('${esc(path)}')" style="font-size:10px;padding:3px 10px;border-color:var(--ac3);color:var(--ac3)">💾 保存</button>
          </div>`:''}`;
        return;
      }
    }catch(e){}
  }
  // 其他: 提示下载
  el.innerHTML=`${backBtn}${dlBtn}<div style="padding:20px;text-align:center;color:var(--tx3);font-size:12px">
    ${esc(fname)}<br>此文件类型暂不支持预览，请下载后查看</div>`;
}
async function _saveWsFile(path){
  const parts=path.split('/');const sessIdx=parts.indexOf('sessions');
  if(sessIdx<0){toast('只能编辑 session 目录内的文件');return;}
  const sid=parts[sessIdx+1];const name=parts.slice(sessIdx+2).join('/');
  const content=document.getElementById('ws-editor').value;
  const r=await fetch(`/api/sessions/${sid}/files/${name}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({content})});
  const d=await r.json();
  toast(d.ok?'✅ 已保存':'❌ 保存失败: '+(d.error||''));
}

// ── v1.10 P34-d-10: 左下角模型徽标 + 服务器状态 + 快捷切换 ────
// 全局模型缓存 — refreshSbfStatus / quickSwitchModel 共用,
// 用 id→label 映射,避免徽标显示超长 ollama id (如 nutboy02/Qwen3.6...:latest)
let _modelsCache=[];
async function _loadModelsCache(){
  try{const r=await fetch('/api/models');const d=await r.json();_modelsCache=d.models||[];return _modelsCache;}
  catch(e){return _modelsCache;}
}
function _labelFor(id){
  if(!id)return '—';
  const m=_modelsCache.find(x=>x.id===id);
  return (m&&m.label)?m.label:id;
}
async function refreshSbfStatus(){
  try{
    const r=await fetch('/api/health',{cache:'no-cache'});
    const d=await r.json();
    document.getElementById('sbf-srv-dot')?.classList.remove('offline');
    // 首次 / 未命中 → 异步补缓存再刷一次
    if(d.model && !_modelsCache.find(x=>x.id===d.model)){
      _loadModelsCache().then(()=>{
        document.getElementById('sbf-model-id').textContent=_labelFor(d.model);
      });
    }
    document.getElementById('sbf-model-id').textContent=_labelFor(d.model);
    document.getElementById('sbf-srv-status').textContent='Online';
    document.getElementById('sbf-srv-status').style.color='var(--ac3)';
  }catch(e){
    document.getElementById('sbf-srv-dot')?.classList.add('offline');
    document.getElementById('sbf-model-id').textContent='— 离线 —';
    document.getElementById('sbf-srv-status').textContent='Offline';
    document.getElementById('sbf-srv-status').style.color='var(--dg)';
  }
}

async function quickSwitchModel(ev){
  if(ev) ev.stopPropagation();
  let models=await _loadModelsCache();
  if(!models.length){toast('无可用模型');return;}
  let cur='';
  try{cur=(await(await fetch('/api/health')).json()).model;}catch(e){}
  /* [P0-4 2026-07-24] prompt() → 卡片列表点击直选 (合 memory feedback_no_prompt_use_card) */
  _showModelPickerCard(models, cur);
}
function _showModelPickerCard(models, cur){
  const existing=document.getElementById('mdl-picker');
  if(existing)existing.remove();
  const ov=document.createElement('div');
  ov.id='mdl-picker';
  ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10001;display:flex;align-items:center;justify-content:center;padding:20px';
  const items=models.map(m=>{
    const isCur=m.id===cur;
    const meta=[];
    if(m.ctx)meta.push('ctx '+m.ctx);
    if(m.vision)meta.push('👁 视觉');
    if(m.think)meta.push('🧠 思考');
    return `<button class="mdl-item" data-mid="${esc(m.id)}" style="display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:10px 12px;background:${isCur?'var(--ac2,#3b82f6)':'var(--bg)'};border:1px solid ${isCur?'var(--ac3,#fbbf24)':'var(--bd)'};border-radius:6px;cursor:pointer;text-align:left;color:${isCur?'#fff':'var(--tx)'};font-size:12px;width:100%">
      <span style="font-weight:600;font-family:var(--mono)">${esc(m.label||m.id)}${isCur?' · [当前]':''}</span>
      <span style="font-size:10px;color:${isCur?'rgba(255,255,255,.75)':'var(--tx2)'};font-family:var(--mono)">${esc(m.id)}${meta.length?' · '+meta.join(' · '):''}</span>
    </button>`;
  }).join('');
  ov.innerHTML=`<div style="background:var(--sf2,#0f172a);border:1px solid var(--ac2,#3b82f6);border-radius:10px;padding:16px 18px;max-width:480px;width:100%;max-height:80vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.5)">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <div style="font-size:13px;font-weight:600;color:var(--ac3,#fbbf24)">切换模型 (${models.length})</div>
      <button style="background:transparent;border:none;color:var(--tx2);font-size:18px;cursor:pointer" onclick="document.getElementById('mdl-picker')?.remove()">×</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:6px">${items}</div>
    <div style="font-size:10px;color:var(--tx3);margin-top:10px">点击卡片直接切换 · Esc 关闭</div>
  </div>`;
  document.body.appendChild(ov);
  ov.addEventListener('click',e=>{if(e.target===ov)ov.remove();});
  ov.querySelectorAll('.mdl-item').forEach(btn=>{
    btn.onclick=async ()=>{
      const mid=btn.dataset.mid;
      if(mid===cur){toast('已是当前模型');ov.remove();return;}
      ov.remove();
      toast(`切换到 ${mid}...`);
      try{
        const r=await fetch('/api/models/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id:mid})});
        const d=await r.json();
        if(d.ok){toast(`✓ 已切到 ${d.model}`);await refreshSbfStatus();}
        else{toast('❌ 切换失败: '+(d.error||'unknown'));}
      }catch(e){toast('❌ '+e);}
    };
  });
  const escHandler=e=>{if(e.key==='Escape'){ov.remove();document.removeEventListener('keydown',escHandler);}};
  document.addEventListener('keydown',escHandler);
}

// 启动 + 每 30s 刷新（包装让 init 后调一次）
let _sbf_interval=null;
function startSbfStatusLoop(){
  refreshSbfStatus();
  if(!_sbf_interval) _sbf_interval=setInterval(refreshSbfStatus, 30000);
}

/* [theme 2026-05-22] 主题切换 — 5 个预设, localStorage 持久化, 启动时自动应用 */
const THEMES = [
  {id:'',        name:'🌑 经典暗黑',  desc:'深邃黑底 + 橙色高亮 (默认)'},
  {id:'pptx',    name:'💼 企业风',    desc:'PPT Studio 暖白 + 企业蓝 #4F7EF7 + 黑主色'},
  {id:'kimi',    name:'🍊 Kimi 暖白', desc:'#FBFBF9 + Inter + 22px 圆角, 参考 kimi.com'},
  {id:'light',   name:'🍎 苹果白',   desc:'纯白 + 苹果蓝/绿, 高对比度'},
  {id:'sky',    name:'☁️ 天空蓝',   desc:'浅蓝底 + 橙色高亮, 像晴天'},
  {id:'aurora',  name:'🌌 极光',     desc:'深紫 + 荧光绿紫, 渐变感'},
  {id:'minimal', name:'📖 极简米白', desc:'暖米底 + 砖红, 阅读感'},
];
function applyTheme(id){
  const cur = localStorage.getItem('theme') || '';
  if(id) document.documentElement.dataset.theme = id;
  else delete document.documentElement.dataset.theme;
  try{localStorage.setItem('theme', id||'');}catch(e){}
  // 关闭菜单
  const m=document.getElementById('theme-menu'); if(m) m.remove();
  /* Chrome 对 `background:var(--x)` 简写有 style cache: --x 换值后不重算.
   * 首次装载时 CSS var 已被烧进简写, 之后 applyTheme 换 --x 不重绘.
   * 换主题 (非首次) 必须硬 reload 一次, 让所有简写从新 var 起点解析. */
  if(cur !== (id||'') && !window.__themeApplyingInitial){
    setTimeout(()=>location.reload(), 40);
  }
}
function toggleThemeMenu(ev){
  ev && ev.stopPropagation();
  const existing=document.getElementById('theme-menu');
  if(existing){existing.remove();return;}
  const btn=document.getElementById('theme-btn');
  const rect=btn.getBoundingClientRect();
  const cur=localStorage.getItem('theme')||'';
  const m=document.createElement('div');
  m.id='theme-menu';
  m.style.cssText=`position:fixed;top:${rect.bottom+4}px;right:${innerWidth-rect.right}px;
    background:var(--sf,#13181f);border:1px solid var(--ac,#f0a500);border-radius:8px;
    padding:6px;z-index:10000;box-shadow:var(--shadow-confirm,0 12px 40px rgba(0,0,0,.6));
    min-width:240px;font-family:var(--mono);font-size:12px;color:var(--tx)`;
  m.innerHTML=THEMES.map(t=>`
    <div onclick="applyTheme('${t.id}')" style="
      padding:8px 10px;border-radius:5px;cursor:pointer;
      background:${cur===t.id?'var(--hover-tint,rgba(240,165,0,.1))':'transparent'};
      border:1px solid ${cur===t.id?'var(--ac,#f0a500)':'transparent'};
      margin-bottom:2px;transition:background .12s">
      <div style="color:var(--tx);font-weight:${cur===t.id?'600':'400'}">${t.name}${cur===t.id?' ✓':''}</div>
      <div style="color:var(--tx3);font-size:10px;margin-top:2px">${t.desc}</div>
    </div>`).join('');
  m.onclick=e=>e.stopPropagation();
  document.body.appendChild(m);
  // hover 高亮
  m.querySelectorAll('div[onclick]').forEach(item=>{
    item.onmouseenter=()=>{if(!item.style.borderColor.includes('rgb')) item.style.background='var(--sf2)';};
    item.onmouseleave=()=>{if(!item.style.borderColor.includes('rgb')) item.style.background='transparent';};
  });
  // 点别处关菜单
  setTimeout(()=>document.addEventListener('click',function h(){m.remove();document.removeEventListener('click',h);},{once:true}),50);
}
// 启动时应用上次保存的主题 (首次不 reload)
(function(){try{const t=localStorage.getItem('theme');if(t){window.__themeApplyingInitial=true;applyTheme(t);window.__themeApplyingInitial=false;}}catch(e){}})();

checkAuth();

// /* E2E-polish:C-076 */
function fmtNext(t){return t? new Date(t*1000).toLocaleString('zh-CN'):'--';}

// /* E2E-polish:C-077 — DAG_AGENT_COLORS lives canonically in dag_editor.js */

// /* E2E-polish:C-078 */
function memCompressBar(p){const w=Math.max(0,Math.min(100,p|0));return `<div class='memBar'><i style='width:${w}%'></i></div>`;}

// ─── Docker panel (#44) ───────────────────────────────────
// 三端入口之 Web 端: ps 列表 + 内嵌 stats/logs + restart 用 confirmCard 二次确认
let _dockerStats = {};
async function renderDocker(all){
  const bd = document.getElementById('docker-bd');
  if(!bd) return;
  bd.innerHTML = '<div style="padding:16px;color:var(--tx3);font-size:12px">🔄 加载中…</div>';
  try{
    const [ps, st] = await Promise.all([
      fetch('/api/docker/ps?all=' + (all?1:0)).then(r=>r.json()),
      fetch('/api/docker/stats').then(r=>r.json()).catch(()=>({stats:[]})),
    ]);
    if(!ps.ok){ bd.innerHTML = `<div style="color:var(--dg);padding:14px">${esc(ps.error||'加载失败')}</div>`; return; }
    _dockerStats = {};
    (st.stats||[]).forEach(s => { _dockerStats[s.name] = s; });
    const rows = (ps.containers||[]).map(c => {
      const s = _dockerStats[c.name] || {};
      const stateBadge = c.state==='running'
        ? '<span style="color:var(--ac2,#4F7EF7);font-size:11px">● running</span>'
        : `<span style="color:var(--tx3);font-size:11px">○ ${esc(c.state||'?')}</span>`;
      const scopeTag = c.in_scope
        ? '<span style="font-size:10px;color:var(--ac,#4F7EF7);border:1px solid var(--ac,#4F7EF7);padding:1px 6px;border-radius:8px">scope</span>'
        : '<span style="font-size:10px;color:var(--tx3);border:1px solid var(--bd2);padding:1px 6px;border-radius:8px">-</span>';
      const cpuMem = c.state==='running' && s.cpu_pct !== undefined
        ? `<span style="font-family:var(--mono);font-size:10px;color:var(--tx2)">CPU ${s.cpu_pct.toFixed(1)}%  MEM ${s.mem_mb.toFixed(0)}MB (${s.mem_pct.toFixed(1)}%)</span>`
        : '';
      const actions = c.in_scope
        ? `<button class="abtn" style="font-size:10px;padding:2px 8px" onclick="dockerAction('${esc(c.name)}','logs')">📄 logs</button>
           <button class="abtn" style="font-size:10px;padding:2px 8px" onclick="dockerAction('${esc(c.name)}','inspect')">🔍 inspect</button>
           <button class="abtn" style="font-size:10px;padding:2px 8px;color:var(--wn,#c69026)" onclick="dockerAction('${esc(c.name)}','restart')">🔄 restart</button>
           ${c.state==='running'
             ? `<button class="abtn" style="font-size:10px;padding:2px 8px;color:var(--dg,#c94a4a)" onclick="dockerAction('${esc(c.name)}','stop')">⏹ stop</button>`
             : `<button class="abtn" style="font-size:10px;padding:2px 8px" onclick="dockerAction('${esc(c.name)}','start')">▶ start</button>`}`
        : `<span style="font-size:10px;color:var(--tx3)">out of scope</span>`;
      return `<div style="border-bottom:1px solid var(--bd2);padding:10px 12px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
          <span style="font-weight:600;color:var(--tx)">${esc(c.name)}</span>
          ${stateBadge} ${scopeTag}
          <span style="margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--tx3)">${esc(c.id||'')}</span>
        </div>
        <div style="font-size:11px;color:var(--tx2);margin-bottom:4px">${esc(c.image||'')}  ·  <span style="color:var(--tx3)">${esc(c.status||'')}</span></div>
        ${cpuMem?`<div style="margin-bottom:6px">${cpuMem}</div>`:''}
        <div style="display:flex;gap:6px;flex-wrap:wrap">${actions}</div>
        <div id="docker-out-${esc(c.name)}"></div>
      </div>`;
    }).join('');
    bd.innerHTML = `
      <div style="padding:10px 14px;background:var(--sf2,#f5f5f0);border-bottom:1px solid var(--bd2);display:flex;align-items:center;gap:8px">
        <span style="font-size:12px;color:var(--tx2)">共 ${ps.count} 个容器</span>
        <label style="font-size:11px;color:var(--tx2);display:flex;align-items:center;gap:4px;margin-left:auto;cursor:pointer">
          <input type="checkbox" ${all?'checked':''} onchange="renderDocker(this.checked)"> 包含已停止
        </label>
      </div>
      ${rows || '<div style="padding:20px;text-align:center;color:var(--tx3);font-size:12px">无容器</div>'}
      <div style="padding:10px 14px;color:var(--tx3);font-size:10px;border-top:1px solid var(--bd2)">
        📖 白名单前缀走 <code>LITECODE_DOCKER_SCOPE</code> 环境变量, 默认 <code>litecode,searxng,pptx,cad</code>
      </div>`;
  }catch(e){
    bd.innerHTML = `<div style="color:var(--dg);padding:14px">加载错误: ${esc(String(e))}</div>`;
  }
}

async function dockerAction(name, action){
  const out = document.getElementById('docker-out-' + name);
  if(action === 'logs'){
    if(out) out.innerHTML = '<div style="margin-top:8px;padding:8px;background:#000;color:#0f0;font-family:var(--mono);font-size:10px;max-height:280px;overflow:auto;white-space:pre-wrap">读取中…</div>';
    try{
      const r = await (await fetch(`/api/docker/logs?name=${encodeURIComponent(name)}&tail=200`)).json();
      if(out) out.innerHTML = `<pre style="margin-top:8px;padding:8px;background:#000;color:#0f0;font-family:var(--mono);font-size:10px;max-height:280px;overflow:auto;white-space:pre-wrap;border-radius:4px">${esc(r.text||r.error||'(empty)')}</pre>`;
    }catch(e){ if(out) out.innerHTML = `<div style="color:var(--dg);margin-top:6px">${esc(String(e))}</div>`; }
    return;
  }
  if(action === 'inspect'){
    if(out) out.innerHTML = '<div style="margin-top:8px;color:var(--tx3);font-size:11px">读取中…</div>';
    try{
      const r = await (await fetch(`/api/docker/inspect?name=${encodeURIComponent(name)}`)).json();
      if(out) out.innerHTML = `<pre style="margin-top:8px;padding:8px;background:var(--sf2,#f5f5f0);border:1px solid var(--bd2);font-size:10px;max-height:280px;overflow:auto;border-radius:4px">${esc(JSON.stringify(r.inspect||r, null, 2))}</pre>`;
    }catch(e){ if(out) out.innerHTML = `<div style="color:var(--dg);margin-top:6px">${esc(String(e))}</div>`; }
    return;
  }
  // 破坏性: restart / stop / start — 走 confirmCard 二次确认
  const verb = {restart:'重启', stop:'停止', start:'启动'}[action];
  const doIt = async () => {
    try{
      const r = await (await fetch(`/api/docker/${action}`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name}),
      })).json();
      if(r.ok){
        if(typeof toast==='function') toast(`✅ ${verb} ${name} 成功`);
        setTimeout(renderDocker, 1500);
      } else {
        if(typeof toast==='function') toast(`❌ ${verb} 失败: ${r.error||''}`);
      }
    }catch(e){
      if(typeof toast==='function') toast(`❌ ${verb} 错误: ${e}`);
    }
  };
  if(typeof confirmCard === 'function'){
    confirmCard({
      title: `${verb}容器: ${name}`,
      body: `即将 ${verb} 容器 <b>${esc(name)}</b>. 确认继续?`,
      okText: verb, okStyle: action==='stop'?'danger':'warn',
      onOk: doIt,
    });
  } else if(confirm(`${verb} ${name}?`)){
    doIt();
  }
}

// ── noVNC 桌面预览 (P2-4 拆分时从 chat_core 段挪回, 它是 panel renderer)
function renderVnc(){
  // noVNC 端口：优先用 window._vncPort（由后端注入），否则 webPort+10
  const webPort = parseInt(location.port || '18790');
  const vncPort = window._vncPort || (webPort + 10);
  const host = location.hostname;
  const proto = location.protocol;
  const vncUrl = `${proto}//${host}:${vncPort}/vnc.html?autoconnect=true&resize=scale&quality=6&compression=2`;
  const frame = document.getElementById('novnc-frame');
  const status = document.getElementById('vnc-status');
  const newwin = document.getElementById('vnc-newwin');
  if(!frame) return;
  if(frame.src === vncUrl) return; // 已加载，不重复
  if(status) status.textContent = '连接中...';
  if(newwin) newwin.href = vncUrl;
  frame.src = vncUrl;
  frame.onload = () => { if(status) status.textContent = `✅ :${vncPort}`; };
  frame.onerror = () => { if(status) status.textContent = `❌ :${vncPort} 无响应`; };
}
