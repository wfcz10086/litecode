// chat_panels.js — 侧栏面板: 项目 / 用量统计 / 记忆 / 设置 [原 chat_core.js 拆分 5/5]
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

