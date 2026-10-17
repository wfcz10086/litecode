// ── 旧 DAG 面板残留 (P34-d) ─────────────────────────────
// 2026-10-17 退役: #pnl-dag/#dag-canvas 已在 P0-6 删除, 画布操作函数 (loadDAG/newDAG/addDAGNode*/dagUndo/
// dagClearStatus/aiGenDAG/saveDAG/showDAGNodeOutput) 已删, closeP 迁至 app.js。
// 仅保留仍有调用方的: loadDAGList (panel_refresh) / _showDAGCard+_hideDAGCard / runDAG / _pollDAGJob 及其辅助。
let _dagEditor=null,_dagCurrent=null,_dagNodeOutputBuf={};
async function loadDAGList(){
  // 旧 #pnl-dag / #dag-select / #dag-canvas 与 dagEditorInit 已在 P0-6 删除, 原实现每次被调用都抛
  // "dagEditorInit is not defined" (对话里建/改 DAG 触发 panel_refresh 时)。现改为: 新版全屏 DAG
  // 界面开着且停在列表页时重绘列表, 其余情况无事可做。
  const ov=document.getElementById('dag-ui');
  if(ov&&ov.classList.contains('on')&&window.DAG&&window.DAG._state.screen==='list')window.DAG.showList();
}
/* [v1.12] 卡片式 DAG 编辑表单 — 替代所有 prompt() 弹窗
 * [2026-07-24 P0-1] 扩展: 加 task rows=8 + 高级选项 (timeout/max_retries/critical/when) +
 *                        tool 类 (tool_name/tool_args) + control 类 (IF/switch 条件)
 *                        新增 mode: 'inspect-x6' 供 X6 画布双击节点用, 'edge-label' 供双击边用. */
const DAG_AGENT_TYPES=['coder','explorer','researcher','analyst','tester','shell','writer','critic'];
const DAG_TOOL_TEMPLATES=['pptx','search','shell_cmd','ocr','wechat_send','html_render','cad_2d','cad_3d','pptx_render','web_search','file_read','file_write'];
const DAG_CONTROL_TYPES=['if','switch','merge'];
function _hideDAGCard(){const c=document.getElementById('dag-card');if(c){c.style.display='none';c.innerHTML='';/* 还原 embedded 定位 */c.style.position='';c.style.top='';c.style.right='';c.style.bottom='';c.style.left='';c.style.zIndex='';c.style.maxWidth='';c.style.maxHeight='';c.style.overflowY='';c.style.boxShadow='';}}
function _dcInput(id,ph,val,extra=''){return `<input id="${id}" placeholder="${esc(ph)}" value="${esc(val==null?'':val)}" style="width:100%;padding:5px 8px;background:var(--bg);border:1px solid var(--bd);border-radius:4px;color:var(--tx);font-size:12px;margin-bottom:6px" ${extra}>`;}
function _dcNumber(id,ph,val,min,max){return `<input id="${id}" type="number" placeholder="${esc(ph)}" value="${val==null?'':val}" min="${min}" max="${max}" style="flex:1;padding:5px 8px;background:var(--bg);border:1px solid var(--bd);border-radius:4px;color:var(--tx);font-size:12px">`;}
function _dcTextarea(id,ph,val,rows=8){return `<textarea id="${id}" placeholder="${esc(ph)}" rows="${rows}" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--bd);border-radius:4px;color:var(--tx);font-size:12px;resize:vertical;font-family:var(--mono);margin-bottom:6px">${esc(val||'')}</textarea>`;}
function _dcSel(id,list,val){const opts=list.map(t=>`<option value="${t}"${t===val?' selected':''}>${t}</option>`).join('');return `<select id="${id}" style="width:100%;padding:5px 8px;background:var(--bg);border:1px solid var(--bd);border-radius:4px;color:var(--tx);font-family:var(--mono);font-size:12px;margin-bottom:6px">${opts}</select>`;}
function _dcLabel(txt){return `<div style="font-size:10px;color:var(--tx3);margin:6px 0 2px;font-family:var(--mono);text-transform:uppercase;letter-spacing:.4px">${esc(txt)}</div>`;}
function _dcAdvanced(ctx){
  return `<details style="margin-top:6px;border-top:1px dashed var(--bd);padding-top:6px">
    <summary style="cursor:pointer;font-size:11px;color:var(--ac2);user-select:none;padding:2px 0">⚙ 高级选项 (timeout · max_retries · when · critical)</summary>
    <div style="padding-top:8px">
      ${_dcLabel('执行超时 · timeout(秒)')}
      <div style="display:flex;gap:6px;margin-bottom:6px">
        ${_dcNumber('dc-timeout','秒',ctx.timeout||120,10,7200)}
        ${_dcNumber('dc-retries','最大重试',ctx.max_retries==null?1:ctx.max_retries,0,10)}
      </div>
      ${_dcLabel('条件表达式 · when (JSON, 支持 ${step:id:output})')}
      ${_dcTextarea('dc-when','例: {"if":"${step:qa:output}", "eq":"pass"}',ctx.when&&typeof ctx.when==='object'?JSON.stringify(ctx.when,null,2):(ctx.when||''),4)}
      <label style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--tx2);margin-top:4px">
        <input type="checkbox" id="dc-critical"${ctx.critical?' checked':''}>
        关键节点 · 失败终止全局 (critical)
      </label>
      <label style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--tx2);margin-top:4px">
        <input type="checkbox" id="dc-bp"${ctx.breakpoint?' checked':''}>
        在此暂停 · 断点 (breakpoint)
      </label>
    </div>
  </details>`;
}
function _showDAGCard(mode, ctx, onSave){
  ctx=ctx||{};
  const c=document.getElementById('dag-card');
  if(!c){toast('卡片容器未找到');return;}
  let title='', body='';
  if(mode==='new-dag'){
    title='新建 DAG';
    const def='dag-'+Date.now().toString(36).slice(-4);
    body=_dcInput('dc-name','DAG 名称 (a-z0-9_-)',ctx.name||def)
      +_dcLabel('描述 (可选)')
      +_dcTextarea('dc-desc','一句话说明这个 DAG 干什么',ctx.desc||'',3)
      +`<label style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--tx2);margin-top:4px">
        <input type="checkbox" id="dc-critic"${ctx.auto_critic?' checked':''}>
        运行完自动 critic 复审
      </label>`;
  }else if(mode==='new-node'||mode==='edit-node'||mode==='inspect-x6'){
    title=mode==='new-node'?'新建节点':'编辑节点';
    const kind=ctx.node_kind||'agent'; /* agent | tool | control */
    const tabSel=(v,l)=>`<button type="button" data-nk="${v}" class="dc-tab" style="flex:1;padding:5px 8px;font-size:11px;background:${kind===v?'var(--ac)':'var(--sf2)'};color:${kind===v?'#000':'var(--tx2)'};border:1px solid var(--bd);border-radius:5px;cursor:pointer">${l}</button>`;
    /* 分类 tab (只在 X6 inspect 模式显示 3 选) */
    const showTabs=mode==='inspect-x6';
    const tabBar=showTabs?`<div style="display:flex;gap:4px;margin-bottom:8px">${tabSel('agent','🧠 Agent')}${tabSel('tool','🔧 Tool')}${tabSel('control','⚙ 控制')}</div>`:'';
    body=tabBar
      +_dcInput('dc-label','节点显示标题',ctx.label||('Step'+((_dagEditor?._state.nodes.size||0)+1)))
      +_dcSel('dc-agent',DAG_AGENT_TYPES,ctx.agent_type||'coder');
    /* tool 节点专属 */
    if(kind==='tool'){
      body+=_dcLabel('工具名 · tool_name')
        +_dcSel('dc-tool',DAG_TOOL_TEMPLATES,ctx.tool_name||ctx.kind||'shell_cmd')
        +_dcLabel('工具参数 · tool_args (JSON)')
        +_dcTextarea('dc-toolargs','{\n  "topic": "示例",\n  "slides": "${step:outline:output}"\n}',ctx.tool_args?JSON.stringify(ctx.tool_args,null,2):'{}',6);
    }
    /* control 节点专属 */
    if(kind==='control'){
      body+=_dcLabel('控制类型')
        +_dcSel('dc-ctrl',DAG_CONTROL_TYPES,ctx.ctrl_type||ctx.kind||'if')
        +_dcLabel('分支条件表达式')
        +_dcTextarea('dc-branch','例: ${step:qa:output}.startsWith("pass")',ctx.branch||'',3);
    }
    /* 主任务描述 (所有节点通用, rows=8 满足用户"缺关键输入"诉求) */
    body+=_dcLabel('任务描述 · task (支持 ${step:id:output} 占位符)')
      +_dcTextarea('dc-task','写清楚这个节点该做什么、输出格式、约束条件 —\n不要吝惜文字, 越具体越好',ctx.task||'',8);
    /* 高级选项折叠 */
    body+=_dcAdvanced(ctx);
  }else if(mode==='edge-label'){
    title='边标签 · 分支/条件';
    body=_dcLabel('例: yes / no / caseA / retry')
      +_dcInput('dc-elabel','分支名',ctx.label||'');
  }else if(mode==='ai-gen'){
    title='🤖 AI 一键生成 DAG';
    body=_dcTextarea('dc-desc','一句话描述工作流, 例: 检查代码 → 跑测试 → 写报告',ctx.desc||'',4);
  }
  const okLabel=mode==='ai-gen'?'生成':((mode==='edit-node'||mode==='inspect-x6')?'保存':(mode==='edge-label'?'确定':'创建'));
  c.innerHTML=`<div style="font-size:12px;font-weight:600;color:var(--ac2);margin-bottom:6px">${title}</div>${body}<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px"><button class="abtn" id="dc-cancel" style="font-size:11px;padding:3px 12px">取消</button><button class="abtn" id="dc-ok" style="font-size:11px;padding:3px 12px;border-color:var(--ac3);color:var(--ac3)">${okLabel}</button></div>`;
  c.style.display='block';
  /* [P0-1] X6 / edge 模式: 把 dag-card 提升为顶层浮层, 覆盖在 X6 画布之上 */
  if(mode==='inspect-x6'||mode==='edge-label'){
    c.style.position='fixed';
    c.style.top='60px';
    c.style.right='24px';
    c.style.zIndex='10000';
    c.style.maxWidth='420px';
    c.style.maxHeight='calc(100vh - 120px)';
    c.style.overflowY='auto';
    c.style.boxShadow='0 10px 40px rgba(0,0,0,.6)';
  }
  /* inspect-x6 tab 切换 — 重新调用 _showDAGCard 换 kind */
  c.querySelectorAll('.dc-tab').forEach(btn=>{
    btn.onclick=()=>{
      /* 收集当前已填字段, 换 kind 后回填 */
      const partial={
        label:document.getElementById('dc-label')?.value,
        agent_type:document.getElementById('dc-agent')?.value,
        task:document.getElementById('dc-task')?.value,
        timeout:parseInt(document.getElementById('dc-timeout')?.value)||undefined,
        max_retries:parseInt(document.getElementById('dc-retries')?.value),
        when:document.getElementById('dc-when')?.value,
        critical:!!document.getElementById('dc-critical')?.checked,
        breakpoint:!!document.getElementById('dc-bp')?.checked,
        node_kind:btn.dataset.nk,
      };
      _showDAGCard('inspect-x6',partial,onSave);
    };
  });
  document.getElementById('dc-cancel').onclick=_hideDAGCard;
  document.getElementById('dc-ok').onclick=()=>{
    let data={};
    const $v=id=>(document.getElementById(id)?.value||'').trim();
    const $c=id=>!!document.getElementById(id)?.checked;
    if(mode==='new-dag'){
      data.name=$v('dc-name');
      data.description=$v('dc-desc');
      data.auto_critic=$c('dc-critic');
      if(!data.name){toast('需要 DAG 名称');return;}
    }else if(mode==='new-node'||mode==='edit-node'||mode==='inspect-x6'){
      data.label=$v('dc-label');
      data.agent_type=$v('dc-agent')||'coder';
      data.task=document.getElementById('dc-task')?.value||'';
      data.breakpoint=$c('dc-bp');
      data.critical=$c('dc-critical');
      const to=parseInt($v('dc-timeout'));if(!isNaN(to))data.timeout=to;
      const mr=parseInt($v('dc-retries'));if(!isNaN(mr))data.max_retries=mr;
      const whenRaw=document.getElementById('dc-when')?.value||'';
      if(whenRaw.trim()){
        try{data.when=JSON.parse(whenRaw);}catch{data.when=whenRaw;/* 存字符串, 后端可选择解析 */}
      }
      /* tool 类专属 */
      if(document.getElementById('dc-tool')){
        data.tool_name=$v('dc-tool');
        const argsRaw=document.getElementById('dc-toolargs')?.value||'';
        if(argsRaw.trim()){
          try{data.tool_args=JSON.parse(argsRaw);}catch(e){toast('tool_args JSON 格式错: '+e.message);return;}
        }
        data.node_kind='tool';
      }
      /* control 类专属 */
      if(document.getElementById('dc-ctrl')){
        data.ctrl_type=$v('dc-ctrl');
        data.branch=$v('dc-branch');
        data.node_kind='control';
      }
      if(!data.label){toast('需要 Label');return;}
    }else if(mode==='edge-label'){
      data.label=$v('dc-elabel');
    }else if(mode==='ai-gen'){
      data.desc=$v('dc-desc');
      if(!data.desc){toast('需要描述');return;}
    }
    _hideDAGCard();
    if(typeof onSave==='function')onSave(data);
  };
  /* 焦点到第一个 input */
  setTimeout(()=>{const f=c.querySelector('input,textarea');if(f){f.focus();if(f.select&&f.tagName==='INPUT')f.select();}},10);
  /* [v1.13] Enter 提交 / Esc 取消 — 在卡片内的 input/textarea 上挂 keydown */
  c.querySelectorAll('input,textarea,select').forEach(el=>{
    el.addEventListener('keydown',ke=>{
      if(ke.key==='Enter'){
        if(el.tagName==='TEXTAREA' && !ke.ctrlKey && !ke.metaKey)return; /* textarea 默认换行, Ctrl+Enter 提交 */
        ke.preventDefault();
        document.getElementById('dc-ok')?.click();
      }else if(ke.key==='Escape'){
        ke.preventDefault();
        _hideDAGCard();
      }
    });
  });
}
/* [v1.10] 运行当前 DAG: POST /api/dags/{name}/run → 拿 job_id → 轮询 → 节点染色 */
/* [v1.14] 加运行状态栏 + Run 按钮在跑时置灰，跑完恢复 + 节点点击查看输出 */
let _dagPollTimer=null;
let _dagRunStartTs=0;
let _dagLatestJob=null;  /* 缓存最新 job detail，供节点点击查看 */
/* [M1] Jenkins Blue Ocean 风 Build 详情面板. 由 dag_build_view.js 创建, runDAG/_pollDAGJob 更新. */
let _dagBuildView=null;
function _setDAGRunBtn(running){
  const btn=document.getElementById('dag-run-btn');
  if(!btn)return;
  if(running){
    btn.disabled=true;
    btn.dataset._origText=btn.textContent;
    btn.textContent='⏳ 运行中…';
    btn.style.opacity='0.55';
    btn.style.cursor='not-allowed';
  }else{
    btn.disabled=false;
    if(btn.dataset._origText)btn.textContent=btn.dataset._origText;
    btn.style.opacity='';
    btn.style.cursor='';
  }
}
function _setDAGStatusBar(visible, opts){
  opts=opts||{};
  /* 1. 面板内状态条 */
  const bar=document.getElementById('dag-status-bar');
  if(bar){
    bar.style.display = visible ? 'block' : 'none';
    if(visible){
      const icon=document.getElementById('dag-status-icon');
      const text=document.getElementById('dag-status-text');
      const prog=document.getElementById('dag-status-progress');
      if(icon)icon.textContent=opts.icon||'⏳';
      if(text)text.textContent=opts.text||'';
      if(prog)prog.textContent=opts.progress||'';
    }
  }
  /* 2. 全局浮动指示器（任意面板下都可见） */
  const fi=document.getElementById('dag-floating-indicator');
  if(fi){
    _initDAGIndicatorDrag(fi);
    fi.style.display = visible ? 'flex' : 'none';
    if(visible){
      const fIcon=document.getElementById('dag-fi-icon');
      const fText=document.getElementById('dag-fi-text');
      const fProg=document.getElementById('dag-fi-prog');
      if(fIcon)fIcon.textContent=opts.icon||'⏳';
      if(fText)fText.textContent=(opts.text||'DAG 运行中').slice(0,28);
      if(fProg)fProg.textContent=opts.progress||'';
      /* pulsing border for running state */
      const running = (opts.icon==='▶' || opts.icon==='⏳');
      fi.style.borderColor = running ? 'var(--ac3, #fbbf24)' : 'var(--ac2, #3b82f6)';
    }
  }
}
/* [2026-05] DAG 浮动指示器拖拽: 一次初始化, 拖完位置存 localStorage, 刷新读回.
 * 防误拖: drag 阈值 4px 内仍当 click → 走原 onclick (openP('dag')). */
function _initDAGIndicatorDrag(fi){
  if(fi._dragInit)return; fi._dragInit=true;
  try{
    const saved=JSON.parse(localStorage.getItem('dag-fi-pos')||'null');
    if(saved&&typeof saved.left==='number'&&typeof saved.top==='number'){
      fi.style.left=saved.left+'px'; fi.style.top=saved.top+'px';
      fi.style.right=''; fi.style.bottom='';
    }
  }catch(e){}
  let down=null;
  fi.addEventListener('mousedown',e=>{
    if(e.button!==0)return;
    const r=fi.getBoundingClientRect();
    down={x:e.clientX,y:e.clientY,ox:r.left,oy:r.top,moved:false};
    e.preventDefault();
  });
  window.addEventListener('mousemove',e=>{
    if(!down)return;
    const dx=e.clientX-down.x, dy=e.clientY-down.y;
    if(!down.moved && Math.hypot(dx,dy)<4)return;
    down.moved=true;
    fi.style.cursor='grabbing';
    let nx=down.ox+dx, ny=down.oy+dy;
    const W=window.innerWidth-fi.offsetWidth-4, H=window.innerHeight-fi.offsetHeight-4;
    nx=Math.max(4,Math.min(W,nx)); ny=Math.max(4,Math.min(H,ny));
    fi.style.left=nx+'px'; fi.style.top=ny+'px';
    fi.style.right=''; fi.style.bottom='';
  });
  window.addEventListener('mouseup',()=>{
    if(!down)return;
    if(down.moved){
      try{
        const r=fi.getBoundingClientRect();
        localStorage.setItem('dag-fi-pos',JSON.stringify({left:Math.round(r.left),top:Math.round(r.top)}));
      }catch(e){}
      /* 抑制本次 click (拖完不要触发 openP) */
      const stop=ev=>{ev.stopPropagation();ev.preventDefault();fi.removeEventListener('click',stop,true);};
      fi.addEventListener('click',stop,true);
    }
    fi.style.cursor='';
    down=null;
  });
}
async function runDAG(){
  if(!_dagEditor||_dagEditor._state.nodes.size===0){toast('画布为空');return;}
  if(!_dagCurrent){toast('请先保存 DAG');return;}
  /* 防重复点击 */
  const btn=document.getElementById('dag-run-btn');
  if(btn&&btn.disabled){toast('已在运行中');return;}
  /* 全部节点先标 idle */
  if(_dagEditor.clearAllStatus)_dagEditor.clearAllStatus();
  /* 起点 (无 depends_on) 先标 running */
  const allSteps=_dagEditor.toJSON().steps;
  const starts=allSteps.filter(s=>!s.depends_on||s.depends_on.length===0).map(s=>s.id);
  starts.forEach(id=>_dagEditor.setNodeStatus&&_dagEditor.setNodeStatus(id,'running'));
  /* 状态栏 + 按钮置灰 */
  _setDAGRunBtn(true);
  _dagRunStartTs=Date.now();
  _setDAGStatusBar(true,{icon:'⏳',text:'派发中… '+_dagCurrent,progress:''});
  toast('▶ 运行中: '+_dagCurrent);
  try{
    const r=await fetch(`/api/dags/${encodeURIComponent(_dagCurrent)}/run`,{method:'POST'});
    if(!r.ok){
      toast('运行端点未实现 (HTTP '+r.status+')');
      _setDAGRunBtn(false);
      _setDAGStatusBar(false);
      return;
    }
    const d=await r.json();
    const total=d.step_count||allSteps.length;
    _setDAGStatusBar(true,{
      icon:'▶',
      text:`运行中 · job=${(d.job_id||'').slice(0,6)}`,
      progress:`0/${total} · 0s`
    });
    toast(`✓ 已派 ${total} 步 (job=${(d.job_id||'').slice(0,6)})`);
    /* [M1] Jenkins Blue Ocean 风 Build 详情面板 */
    if(_dagBuildView){_dagBuildView.close();_dagBuildView=null;}
    if(typeof window.createDAGBuildView==='function'){
      _dagBuildView=window.createDAGBuildView({jobId:d.job_id||'',dagName:_dagCurrent,stepCount:total});
      document.body.appendChild(_dagBuildView.el);
    }
    /* 启动轮询（1.5s 间隔，更跟手） */
    if(_dagPollTimer)clearInterval(_dagPollTimer);
    _pollDAGJob(d.job_id, total);  /* 立即拉一次，不等 1.5s */
    _dagPollTimer=setInterval(()=>_pollDAGJob(d.job_id, total),1500);
    /* 30 分钟硬超时 */
    setTimeout(()=>{
      if(_dagPollTimer){
        clearInterval(_dagPollTimer);_dagPollTimer=null;
        _setDAGRunBtn(false);
        _setDAGStatusBar(true,{icon:'⌛',text:'30 分钟超时，已停止轮询',progress:''});
        setTimeout(()=>_setDAGStatusBar(false), 8000);
      }
    },30*60*1000);
  }catch(e){
    toast('运行失败: '+e);
    _setDAGRunBtn(false);
    _setDAGStatusBar(true,{icon:'❌',text:'运行失败: '+String(e).slice(0,80),progress:''});
    setTimeout(()=>_setDAGStatusBar(false), 5000);
  }
}
/* [P46] 断点面板渲染 */
function _renderBreakpointPanel(job_id, j){
  let panel = document.getElementById('dag-breakpoint-panel');
  if(!panel){
    panel = document.createElement('div');
    panel.id = 'dag-breakpoint-panel';
    panel.style.cssText='margin:6px 0;padding:6px 10px;background:var(--bg1);border:1px solid var(--bd);border-radius:6px;font-size:12px;';
    const bar = document.getElementById('dag-status-bar');
    if(bar && bar.parentNode) bar.parentNode.insertBefore(panel, bar.nextSibling);
    else document.getElementById('dag-canvas-wrap')?.appendChild(panel);
  }
  const bps = new Set(j.breakpoints||[]);
  const paused = j.paused_at||null;
  const agents = j.agents||[];
  const allSteps = (j.step_count||agents.length) > 0;
  if(!allSteps && bps.size===0 && !paused){panel.style.display='none';return;}
  panel.style.display='block';
  /* 收集所有步骤 id/label */
  const steps = agents.length ? agents.map(a=>({id:a.label||a.agent_type,label:a.label||a.agent_type}))
    : Array.from(bps).map(id=>({id,label:id}));
  if(!steps.length && !paused){panel.style.display='none';return;}
  let html='<div style="font-weight:600;margin-bottom:4px;color:var(--tx2)">节点断点</div>';
  steps.forEach(s=>{
    const hasBp=bps.has(s.id);
    const isPaused=paused===s.id;
    html+=`<div style="display:flex;align-items:center;gap:8px;padding:2px 0;">`;
    html+=`<button onclick="_toggleBreakpoint('${job_id}','${s.id}',${!hasBp})" title="${hasBp?'取消断点':'设置断点'}"
      style="background:none;border:none;cursor:pointer;font-size:14px;line-height:1;color:${hasBp?'#ef4444':'var(--tx3)'}">${hasBp?'●':'○'}</button>`;
    html+=`<span style="color:var(--tx2);min-width:80px">${esc(s.label)}</span>`;
    if(isPaused){
      html+=`<span style="color:#f59e0b;font-weight:600">⏸ 已暂停</span>`;
      html+=`<button onclick="_resumeNode('${job_id}','${s.id}')" class="abtn" style="padding:1px 8px;font-size:11px">▶ 继续</button>`;
    }
    html+='</div>';
  });
  panel.innerHTML=html;
}
window._toggleBreakpoint=async function(job_id,node_id,setVal){
  try{
    await fetch(`/api/dags/jobs/${encodeURIComponent(job_id)}/breakpoints/${encodeURIComponent(node_id)}`,
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({set:setVal})});
    if(_dagLatestJob && _dagLatestJob.job_id===job_id) _pollDAGJob(job_id,null);
  }catch(e){console.warn('breakpoint err',e);}
};
window._resumeNode=async function(job_id,node_id){
  try{
    await fetch(`/api/dags/jobs/${encodeURIComponent(job_id)}/resume/${encodeURIComponent(node_id)}`,
      {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(_dagLatestJob && _dagLatestJob.job_id===job_id) _pollDAGJob(job_id,null);
  }catch(e){console.warn('resume err',e);}
};
async function _pollDAGJob(job_id, total){
  try{
    const r=await fetch(`/api/dags/jobs/${encodeURIComponent(job_id)}`);
    if(!r.ok)return;
    const j=await r.json();
    /* 缓存最新 job detail 供节点点击查看 */
    j.job_id = job_id;
    _dagLatestJob = j;
    /* agents 里的每条对应一个 step, label/agent_type 用来匹配节点 */
    (j.agents||[]).forEach(a=>{
      if(_dagEditor&&_dagEditor.setNodeStatus){  /* _dagEditor 恒为 null (旧画布已删), 不加守卫会让整轮 poll 抛错, build view 收不到更新 */
        const status=a.success?'done':(a.error?'failed':'running');
        _dagEditor.setNodeStatus(a.label||a.agent_type, status, a.elapsed);
      }
      /* [P54+] 把 agent.output_preview 注入 _dagNodeOutputBuf, 让 modal 点开就有日志 */
      const key = a.step_id || a.label || a.agent_type;
      if (key){
        const out = a.output_preview || a.error || '';
        if (out){
          const tag = a.success ? '✅ 完成' : (a.error ? '❌ 失败' : '⏳ 运行中');
          _dagNodeOutputBuf[key] = `[${tag} · ${(a.elapsed||0).toFixed(1)}s]\n\n${out}`;
          /* 若 modal 正显示该 step, 实时刷新内容 */
          const modal = document.getElementById('dag-node-output-modal');
          if (modal && modal.style.display === 'flex'){
            const pre = document.getElementById('dag-node-output-body');
            if (pre && (pre.dataset.stepId === key || pre.dataset.stepLabel === (a.label||a.agent_type))){
              pre.textContent = _dagNodeOutputBuf[key];
              /* 同时刷 meta 上的状态 */
              const meta = document.getElementById('dag-node-output-meta');
              if (meta){
                const col = a.success?'var(--ac3,#10b981)':(a.error?'var(--dg,#ef4444)':'var(--wn,#f59e0b)');
                meta.innerHTML = `<span style="color:${col};font-weight:600">${a.success?'成功':(a.error?'失败':'运行中')}</span>  ·  ⏱ ${(a.elapsed||0).toFixed(1)}s  ·  🔖 job=${job_id.slice(0,8)}`;
              }
            }
          }
        }
      }
    });
    /* [M1] Build 详情面板同步 */
    if(_dagBuildView){try{_dagBuildView.update(j);}catch(_ex){}}
    /* [P46] 断点面板 */
    _renderBreakpointPanel(job_id, j);
    /* 实时状态栏 */
    const done=(j.agents||[]).filter(a=>a.success||a.error).length;
    const tot=total||(j.agents||[]).length||1;
    const elapsed=Math.round((Date.now()-_dagRunStartTs)/1000);
    _setDAGStatusBar(true,{
      icon: j.status==='running'?'▶':(j.status==='done'?'✅':(j.status==='failed'?'❌':'⏳')),
      text: j.status==='running'?`运行中 · job=${(job_id||'').slice(0,6)}`:`完成 · job=${(job_id||'').slice(0,6)}`,
      progress: `${done}/${tot} · ${elapsed}s`
    });
    if(j.status==='done'||j.status==='failed'){
      if(_dagPollTimer){clearInterval(_dagPollTimer);_dagPollTimer=null;}
      const ok=(j.agents||[]).filter(a=>a.success).length;
      _setDAGRunBtn(false);
      _setDAGStatusBar(true,{
        icon: j.status==='done'?'🎉':'✗',
        text: j.status==='done'?`DAG 完成 ${ok}/${tot}`:`DAG 失败 ${ok}/${tot}`,
        progress: `${(j.elapsed||elapsed).toFixed(0)}s`
      });
      toast(j.status==='done'?`🎉 DAG 完成 ${ok}/${tot} (${(j.elapsed||0).toFixed(0)}s)`:'✗ DAG 失败');
      /* 8s 后状态栏淡出，让用户有时间看到结果 */
      setTimeout(()=>_setDAGStatusBar(false), 8000);
      /* [M1] 完成后拉 Artifacts 挂底部 */
      if(_dagBuildView){
        fetch(`/api/dags/jobs/${encodeURIComponent(job_id)}/artifacts`)
          .then(r=>r.ok?r.json():null)
          .then(d=>{if(d&&_dagBuildView)_dagBuildView.mountArtifacts(d.artifacts||[]);})
          .catch(()=>{});
      }
    }
  }catch(e){console.warn('poll err',e);}
}
