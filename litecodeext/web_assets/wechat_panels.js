// ── WeChat 管理 ──────────────────────────────────────────────
let _wxPoll=null;
let _wxPrevHash='';        // 上次渲染的状态哈希，跳过无变化的 DOM 重建
let _wxRenderedQr={};      // {bot_id: qr_url} 已渲染的二维码缓存
let _wxForceRender=true;   // 首次 / 操作后强制全量渲染

function _wxStateHash(d){
  // 生成轻量哈希：只关心会变化的字段
  const bots=(d.bots||[]).map(b=>b.bot_id+':'+b.status+':'+(b.qr_url||'')+':'+b.msg_count+':'+(b.last_error||'')+':'+(b.users||[]).map(u=>u.user_id+u.token_expired+u.hours_remaining).join(',')).join('|');
  const cfg=d.config||{};
  return bots+'##'+cfg.enabled+'##'+cfg.keepalive_hours+'##'+(cfg.keepalive_message||'')+'##'+(cfg.vision_model||'')+'##'+cfg.send_interval;
}

async function loadWx(){
  const el=document.getElementById('wx-bd');
  if(_wxForceRender && !el.innerHTML.includes('wx-ka-h')){
    el.innerHTML='<div style="color:var(--tx3)">Loading...</div>';
  }
  try{
    const [wxRes,mdlRes]=await Promise.all([fetch('/api/wechat/status'),fetch('/api/models')]);
    const d=await wxRes.json(), mdlData=await mdlRes.json();
    if(!d.available){el.innerHTML='<div style="color:var(--tx3);padding:12px">微信桥接模块不可用。请确认 wechat_bridge.py 存在且已安装依赖：<br><code style="color:var(--ac2)">pip install wechatbot-sdk aiohttp</code></div>';return;}

    // ── 状态对比：无变化则跳过 DOM 重建 ──
    const newHash=_wxStateHash(d);
    if(!_wxForceRender && newHash===_wxPrevHash){
      // 状态无变化，只续约轮询
      _wxSchedulePoll(d.bots||[]);
      return;
    }
    _wxPrevHash=newHash;
    _wxForceRender=false;

    const cfg=d.config||{};
    const bots=d.bots||[];
    const allModels=mdlData.models||[];
    const visionModels=allModels.filter(m=>m.supports_vision);
    const stColors={online:'var(--ac3)',wait_scan:'var(--wn)',scanned:'var(--ac2)',expired:'var(--dg)',offline:'var(--tx3)'};
    const stLabels={online:'● 在线',wait_scan:'◎ 等待扫码',scanned:'◉ 已扫码',expired:'✕ 已过期',offline:'○ 离线'};
    let h='';
    // SDK 状态提示
    if(cfg.sdk_available===false){
      h+=`<div style="padding:8px 10px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:var(--rs);margin-bottom:10px;font-size:12px;color:var(--dg)">
        ⚠️ wechatbot-sdk 未安装，微信功能不可用<br>
        <code style="color:var(--ac2);font-size:11px">pip install wechatbot-sdk</code>
      </div>`;
    }
    // 配置区
    h+=`<div style="margin-bottom:16px;padding:10px 12px;background:var(--sf2);border-radius:var(--rs);border:1px solid var(--bd)">
      <div style="font-size:12px;font-weight:600;color:var(--tx);margin-bottom:8px;font-family:var(--mono)">⚙️ 保活 & 发送配置</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px">
        <label style="font-size:11px;color:var(--tx2);min-width:80px">保活间隔(h)</label>
        <input type="number" id="wx-ka-h" value="${cfg.keepalive_hours||22}" min="1" max="23" step="0.5" style="width:70px;padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:12px;font-family:var(--mono)">
        <label style="font-size:11px;color:var(--tx2);min-width:80px">发送间隔(s)</label>
        <input type="number" id="wx-si" value="${cfg.send_interval||1.5}" min="0.5" max="10" step="0.5" style="width:70px;padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:12px;font-family:var(--mono)">
      </div>
      <div style="display:flex;gap:8px;align-items:flex-start;margin-bottom:6px">
        <label style="font-size:11px;color:var(--tx2);min-width:80px;padding-top:4px">保活消息</label>
        <textarea id="wx-ka-msg" rows="3" placeholder="多行心跳消息, 支持 emoji + 时间戳占位 &#123;time&#125; &#123;date&#125;" style="flex:1;padding:6px 8px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:12px;resize:vertical;font-family:var(--mono);min-height:60px">${esc(cfg.keepalive_message||'')}</textarea>
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <label style="font-size:11px;color:var(--tx2);min-width:80px">👁️ 视觉模型</label>
        <select id="wx-vision-mdl" style="flex:1;padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:11px;font-family:var(--mono)">
          <option value="">不使用（图片仅保存路径）</option>
          ${visionModels.map(m=>`<option value="${esc(m.id)}" ${cfg.vision_model===m.id?'selected':''}>${esc(m.label||m.id)} 👁️</option>`).join('')}
        </select>
      </div>
      <div style="font-size:10px;color:var(--tx3);margin-bottom:6px;padding-left:88px">当主模型不支持图片时，自动用此模型生成图片描述再转给主模型</div>
      <div style="display:flex;gap:8px;align-items:center">
        <label style="font-size:11px;color:var(--tx2);min-width:80px">启用</label>
        <input type="checkbox" id="wx-enabled" ${cfg.enabled?'checked':''} style="accent-color:var(--ac3)">
        <div style="flex:1"></div>
        <button class="abtn" onclick="wxSaveCfg()" style="font-size:11px;padding:4px 12px">保存配置</button>
      </div>
    </div>`;
    // Bot 列表
    h+=`<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <span style="font-size:12px;font-weight:600;color:var(--tx);font-family:var(--mono)">📱 微信实例 (${bots.length})</span>
      <button class="abtn" onclick="wxAddBot()" style="font-size:11px;padding:4px 12px">+ 添加微信</button>
    </div>`;
    if(!bots.length){
      h+='<div style="color:var(--tx3);font-size:12px;padding:16px 0;text-align:center">暂无微信实例，点击上方按钮添加</div>';
    } else {
      bots.forEach(b=>{
        const sc=stColors[b.status]||'var(--tx3)';
        const sl=stLabels[b.status]||b.status;
        h+=`<div style="padding:10px 12px;background:var(--sf);border:1px solid var(--bd);border-radius:var(--rs);margin-bottom:8px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            <span style="font-family:var(--mono);font-size:13px;font-weight:600;color:var(--tx)">${esc(b.bot_id)}</span>
            <span style="font-size:11px;color:${sc};font-weight:600">${sl}</span>
            <span style="font-size:10px;color:var(--tx3);margin-left:auto">📨 ${b.msg_count||0}</span>
          </div>`;
        // 错误提示
        if(b.last_error){
          h+=`<div style="font-size:11px;color:var(--dg);background:rgba(239,68,68,.08);padding:6px 8px;border-radius:4px;margin-bottom:6px;border:1px solid rgba(239,68,68,.2)">
            ⚠️ ${esc(b.last_error.slice(0,200))}
            ${b.hint?`<div style="color:var(--wn);margin-top:3px;font-size:10px">${esc(b.hint)}</div>`:''}
            <div style="color:var(--tx3);margin-top:3px;font-size:10px">💡 请确认已安装 wechatbot-sdk：<code style="color:var(--ac2)">pip install wechatbot-sdk</code></div>
          </div>`;
        }
        // QR 码 — 使用 canvas 本地生成，避免外部 API 重复请求
        if(b.status==='wait_scan'&&b.qr_url){
          const qrId='wx-qr-'+b.bot_id.replace(/[^a-zA-Z0-9]/g,'_');
          h+=`<div style="text-align:center;padding:12px;background:var(--bg);border-radius:var(--rs);margin-bottom:6px">
            <div style="font-size:12px;color:var(--wn);margin-bottom:8px;font-weight:600">📱 请用微信扫描下方二维码登录</div>
            <canvas id="${qrId}" style="width:200px;height:200px;border-radius:6px;image-rendering:pixelated;border:2px solid var(--bd2)"></canvas>
            <div style="font-size:10px;color:var(--tx3);margin-top:6px">二维码有效期约 2 分钟，过期请点击"重新登录"</div>
          </div>`;
          // 延迟渲染 QR（DOM 插入后执行）
          setTimeout(()=>_wxRenderQr(qrId, b.qr_url), 0);
        } else if(b.status==='wait_scan'&&!b.qr_url){
          h+=`<div style="text-align:center;padding:12px;background:var(--bg);border-radius:var(--rs);margin-bottom:6px">
            <div class="dots" style="justify-content:center"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>
            <div style="font-size:11px;color:var(--tx3);margin-top:6px">正在获取二维码…</div>
          </div>`;
        } else if(b.status==='scanned'){
          h+=`<div style="text-align:center;padding:10px;background:rgba(59,130,246,.06);border:1px solid rgba(59,130,246,.2);border-radius:var(--rs);margin-bottom:6px">
            <div style="font-size:12px;color:var(--ac2);font-weight:600">✅ 已扫码，请在微信上确认登录</div>
          </div>`;
        }
        // 离线/过期 → 显眼的重连提示
        if(b.status==='offline'||b.status==='expired'){
          h+=`<div style="text-align:center;padding:10px;background:rgba(245,158,11,.06);border:1px dashed var(--wn);border-radius:var(--rs);margin-bottom:6px">
            <div style="font-size:12px;color:var(--wn);margin-bottom:4px">${b.status==='expired'?'⏰ 登录已过期，需重新扫码':'📱 点击下方"重新登录"获取二维码'}</div>
          </div>`;
        }
        // 用户列表
        if(b.users&&b.users.length){
          h+=`<div style="font-size:11px;color:var(--tx3);margin-bottom:4px;font-family:var(--mono)">在线用户 (${b.users.length})</div>`;
          b.users.forEach(u=>{
            const tc=u.token_expired?'var(--dg)':'var(--ac3)';
            const ts_=u.token_expired?'⚠️ 已过期 — 需用户重新发消息':u.hours_remaining+'h';
            h+=`<div style="font-size:11px;padding:2px 0;display:flex;gap:6px;align-items:center">
              <span style="color:var(--tx)">${esc(u.name)}</span>
              <span style="color:var(--tx3)">${esc(u.user_id)}</span>
              <span style="color:${tc};margin-left:auto;font-family:var(--mono)">${ts_}</span>
            </div>`;
          });
        }
        // 操作按钮
        h+=`<div style="display:flex;gap:6px;margin-top:8px">`;
        if(b.status==='expired'||b.status==='offline'){
          h+=`<button class="abtn" onclick="wxRelogin('${b.bot_id}')" style="font-size:10px;padding:3px 10px">重新登录</button>`;
        }
        h+=`<button class="abtn dng" onclick="wxRemove('${b.bot_id}')" style="font-size:10px;padding:3px 10px">删除</button>`;
        h+=`</div></div>`;
      });
    }
    el.innerHTML=h;
    // 自动刷新
    _wxSchedulePoll(bots);
  }catch(ex){el.innerHTML=`<div style="color:var(--dg)">Error: ${ex}</div>`;}
}

function _wxSchedulePoll(bots){
  if(_wxPoll)clearTimeout(_wxPoll);
  if(!document.getElementById('pnl-wechat').classList.contains('open'))return;
  const hasPending=bots.some(b=>b.status==='wait_scan'||b.status==='scanned');
  const hasOnline=bots.some(b=>b.status==='online');
  if(hasPending){
    // 扫码中：5秒轮询（足够快又不会造成闪烁）
    _wxPoll=setTimeout(loadWx,5000);
  } else if(hasOnline){
    // 已在线：30秒轮询（检查 token 过期状态）
    _wxPoll=setTimeout(loadWx,30000);
  }
}

// ── 本地 QR 码渲染（纯 JS，无外部依赖）──
// 极简 QR 码生成器 — 基于 qr-creator 算法的内联实现
function _wxRenderQr(canvasId, text){
  const cvs=document.getElementById(canvasId);
  if(!cvs)return;
  if(_wxRenderedQr[canvasId]===text)return; // 同一 URL 不重复渲染
  _wxRenderedQr[canvasId]=text;

  // [v1.2] 优先用内嵌的纯 JS 库在浏览器本地生成 (Kazuhiko Arase QRCode),
  // 完全无网络依赖 → 海外节点/离线/内网都能扫.
  // 失败再退到 server 端 python qrcode, 再退到外部服务.
  if(window.QRGen&&window.QRGen.render){
    try{
      window.QRGen.render(cvs, text, 220);
      return;
    }catch(ex){
      console.warn('QRGen local render failed, falling back:', ex);
    }
  }

  const urls=[
    '/api/wechat/qr?text='+encodeURIComponent(text)+'&size=220',
    'https://api.qrserver.com/v1/create-qr-code/?size=220x220&data='+encodeURIComponent(text),
    'https://quickchart.io/qr?text='+encodeURIComponent(text)+'&size=220',
  ];

  function drawError(){
    cvs.width=220; cvs.height=220;
    const ctx=cvs.getContext('2d');
    ctx.fillStyle='#ffffff'; ctx.fillRect(0,0,220,220);
    ctx.fillStyle='#333'; ctx.font='11px monospace'; ctx.textAlign='center';
    ctx.fillText('QR 加载失败',110,80);
    ctx.fillText('请点击重新登录',110,100);
    const short=text.length>70?text.slice(0,70)+'…':text;
    ctx.font='9px monospace';ctx.fillStyle='#888';
    for(let i=0;i<short.length;i+=35){
      ctx.fillText(short.slice(i,i+35),110,130+(i/35)*12);
    }
  }

  function tryLoad(idx){
    if(idx>=urls.length){drawError();return;}
    const img=new Image();
    img.crossOrigin='anonymous';
    img.onload=function(){
      cvs.width=220; cvs.height=220;
      const ctx=cvs.getContext('2d');
      ctx.fillStyle='#ffffff';
      ctx.fillRect(0,0,220,220);
      ctx.drawImage(img,0,0,220,220);
    };
    img.onerror=function(){tryLoad(idx+1);};
    img.src=urls[idx];
  }
  tryLoad(0);
}

async function wxSaveCfg(){
  const h=parseFloat(document.getElementById('wx-ka-h').value)||22;
  const si=parseFloat(document.getElementById('wx-si').value)||1.5;
  const msg=document.getElementById('wx-ka-msg').value;
  const en=document.getElementById('wx-enabled').checked;
  const vm=document.getElementById('wx-vision-mdl').value;
  await fetch('/api/wechat/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keepalive_hours:h,send_interval:si,keepalive_message:msg,enabled:en,vision_model:vm})});
  toast('✅ 微信配置已保存');_wxForceRender=true;loadWx();
}
async function wxAddBot(){
  await fetch('/api/wechat/bots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
  _wxForceRender=true;loadWx();
}
async function wxRemove(id){
  if(!await confirmCard('删除微信实例','将此微信实例移除, 后续消息将无法收发','删除'))return;
  await fetch(`/api/wechat/bots/${id}`,{method:'DELETE'});
  _wxForceRender=true;toast('已删除');loadWx();
}
async function wxRelogin(id){
  _wxRenderedQr={};  // 清除 QR 缓存，确保新二维码能渲染
  await fetch(`/api/wechat/bots/${id}/relogin`,{method:'POST'});
  _wxForceRender=true;loadWx();
}
// ── WeCom (企业微信智能机器人) 管理 ──────────────────────────
let _wcPoll=null, _wcSelected='', _wcTab='contacts';
async function loadWc(){
  const el=document.getElementById('wc-bd');
  try{
    const d=await(await fetch('/api/wecom/status')).json();
    if(!d.available){
      el.innerHTML='<div style="color:var(--tx3);padding:12px">企微桥接不可用. 确认 wecom_bridge.py + websocket-client 已装.</div>';
      return;
    }
    const cfg=d.config||{}, bots=d.bots||[];
    const sc={online:'var(--ac3)',connecting:'var(--wn)',offline:'var(--tx3)',error:'var(--dg)',init:'var(--tx3)'};
    const sl={online:'● 在线',connecting:'◎ 连接中',offline:'○ 离线',error:'✕ 错误',init:'· 初始化'};
    let h='';
    if(cfg.sdk_available===false){
      h+='<div style="padding:8px 10px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:var(--rs);margin-bottom:10px;font-size:12px;color:var(--dg)">⚠️ websocket-client 未安装, 企微桥不可用<br><code style="color:var(--ac2);font-size:11px">pip install websocket-client</code></div>';
    }
    // ── Bot 列表 + 添加 ──
    h+=`<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <span style="font-size:12px;font-weight:600;color:var(--tx);font-family:var(--mono)">🏢 企微 Bot (${bots.length})</span>
      <button class="abtn" onclick="wcAddBot()" style="font-size:11px;padding:4px 12px">+ 添加 Bot</button>
    </div>`;
    if(!bots.length){
      h+='<div style="color:var(--tx3);font-size:12px;padding:16px 0;text-align:center">暂无企微 Bot. 点击上方按钮, 填入 bot_id + secret 添加.</div>';
    }else{
      bots.forEach(b=>{
        const st=sc[b.status]||'var(--tx3)', lbl=sl[b.status]||b.status;
        const last=b.last_active?_fmtRel(b.last_active):'—';
        const sel=b.bot_id===_wcSelected?'2px solid var(--ac2)':'1px solid var(--bd)';
        h+=`<div style="padding:10px 12px;background:var(--sf);border:${sel};border-radius:var(--rs);margin-bottom:8px;cursor:pointer" onclick="wcSelect('${b.bot_id}')">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            <span style="font-family:var(--mono);font-size:12px;font-weight:600;color:var(--tx);word-break:break-all;flex:1">${esc(b.bot_id)}</span>
            <span style="font-size:11px;color:${st};font-weight:600;white-space:nowrap">${lbl}</span>
          </div>
          <div style="display:flex;gap:12px;font-size:10px;color:var(--tx3);font-family:var(--mono)">
            <span>👥 ${b.contacts||0}</span><span>📨 ${b.messages||0}</span><span>📎 ${b.media_valid||0}</span><span style="margin-left:auto">🕐 ${last}</span>
          </div>`;
        if(b.last_error){
          h+=`<div style="font-size:11px;color:var(--dg);background:rgba(239,68,68,.06);padding:4px 6px;border-radius:4px;margin-top:6px">⚠️ ${esc(b.last_error.slice(0,140))}</div>`;
        }
        h+=`<div style="display:flex;gap:6px;margin-top:8px" onclick="event.stopPropagation()">
          <button class="abtn" onclick="wcRelogin('${b.bot_id}')" style="font-size:10px;padding:3px 10px">🔁 重连</button>
          <button class="abtn dng" onclick="wcRemove('${b.bot_id}')" style="font-size:10px;padding:3px 10px">删除</button>
        </div></div>`;
      });
    }
    // ── 选中 Bot 的详情区 ──
    if(_wcSelected && bots.some(b=>b.bot_id===_wcSelected)){
      h+=`<div style="margin-top:12px;border-top:1px solid var(--bd);padding-top:10px">
        <div style="display:flex;gap:4px;margin-bottom:8px">
          <button class="abtn ${_wcTab==='contacts'?'':'sec'}" onclick="wcTab('contacts')" style="font-size:11px;padding:4px 12px">👥 联系人</button>
          <button class="abtn ${_wcTab==='messages'?'':'sec'}" onclick="wcTab('messages')" style="font-size:11px;padding:4px 12px">📨 消息</button>
          <button class="abtn ${_wcTab==='send'?'':'sec'}" onclick="wcTab('send')" style="font-size:11px;padding:4px 12px">✉️ 发送</button>
        </div>
        <div id="wc-tab-bd" style="min-height:120px">加载中...</div>
      </div>`;
    }
    el.innerHTML=h;
    if(_wcSelected)_wcLoadTab();
    _wcSchedulePoll(bots);
  }catch(ex){el.innerHTML=`<div style="color:var(--dg)">Error: ${ex}</div>`;}
}
function _fmtRel(ts){
  const d=Date.now()/1000-ts;
  if(d<60)return Math.floor(d)+'s 前';
  if(d<3600)return Math.floor(d/60)+'m 前';
  if(d<86400)return Math.floor(d/3600)+'h 前';
  return Math.floor(d/86400)+'d 前';
}
function _wcSchedulePoll(bots){
  if(_wcPoll)clearTimeout(_wcPoll);
  if(!document.getElementById('pnl-wecom').classList.contains('open'))return;
  const hasConnecting=bots.some(b=>b.status==='connecting'||b.status==='init');
  const delay=hasConnecting?4000:20000;
  _wcPoll=setTimeout(function(){
    // 护栏: 若用户正在"发送"tab 且 textarea 有内容, 跳过本轮重建
    // (loadWc → el.innerHTML=h 会销毁 textarea, 丢输入). 待清空/切 tab 后自然恢复.
    try{
      if(_wcTab==='send'){
        const ta=document.getElementById('wc-md');
        if(ta && ta.value && ta.value.trim().length>0){
          _wcSchedulePoll(bots); // 重排一轮, 继续观察
          return;
        }
      }
    }catch(_e){}
    loadWc();
  }, delay);
}
function wcSelect(id){ _wcSelected=id; _wcTab='contacts'; loadWc(); }
function wcTab(t){ _wcTab=t; _wcLoadTab(); }
async function _wcLoadTab(){
  const el=document.getElementById('wc-tab-bd'); if(!el||!_wcSelected)return;
  el.innerHTML='<div style="color:var(--tx3);font-size:11px;padding:10px 0;text-align:center">加载中...</div>';
  try{
    if(_wcTab==='contacts'){
      const d=await(await fetch(`/api/wecom/bots/${_wcSelected}/contacts?limit=50`)).json();
      const cs=d.contacts||[];
      if(!cs.length){el.innerHTML='<div style="color:var(--tx3);font-size:11px;padding:10px 0;text-align:center">暂无联系人. 让用户先给 Bot 发一条消息.</div>';return;}
      let h='';
      cs.forEach(c=>{
        h+=`<div style="padding:6px 8px;background:var(--sf2);border-radius:4px;margin-bottom:4px;display:flex;gap:8px;align-items:center;font-size:11px">
          <span style="font-family:var(--mono);color:var(--tx);flex:1;word-break:break-all">${esc(c.userid)}</span>
          <span style="color:var(--tx3);font-family:var(--mono)">📨 ↓${c.msg_in_count||0} ↑${c.msg_out_count||0}</span>
          <span style="color:var(--tx3);font-family:var(--mono);white-space:nowrap">${_fmtRel(c.last_seen)}</span>
        </div>`;
      });
      el.innerHTML=h;
    }else if(_wcTab==='messages'){
      const d=await(await fetch(`/api/wecom/bots/${_wcSelected}/messages?limit=30`)).json();
      const ms=d.messages||[];
      // 标题栏 (含清空按钮), 无论有没有消息都渲染, 方便用户清空空表也 OK
      let hHead=`<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <span style="font-size:11px;color:var(--tx3);font-family:var(--mono)">共 ${ms.length} 条 (最新 30)</span>
        <button class="abtn dng" onclick="wcClearMessages()" style="font-size:10px;padding:3px 10px">🗑 清空历史</button>
      </div>`;
      if(!ms.length){el.innerHTML=hHead+'<div style="color:var(--tx3);font-size:11px;padding:10px 0;text-align:center">暂无消息</div>';return;}
      let h=hHead;
      ms.forEach(m=>{
        const arrow=m.direction==='in'?'← 收':'→ 发';
        const clr=m.direction==='in'?'var(--ac2)':'var(--ac3)';
        const err=m.errcode?`<span style="color:var(--dg);font-size:10px">✕ ${esc(m.errmsg||'')}</span>`:'';
        h+=`<div style="padding:6px 8px;background:var(--sf2);border-radius:4px;margin-bottom:4px;font-size:11px;border-left:2px solid ${clr}">
          <div style="display:flex;gap:6px;color:var(--tx3);font-family:var(--mono);font-size:10px;margin-bottom:3px">
            <span style="color:${clr}">${arrow}</span><span>${esc(m.userid||'?')}</span><span>[${m.msgtype}]</span>${err}<span style="margin-left:auto">${new Date(m.ts*1000).toLocaleTimeString('zh-CN',{hour12:false})}</span>
          </div>
          <div style="color:var(--tx);word-break:break-word;white-space:pre-wrap;max-height:100px;overflow-y:auto">${esc((m.content||'').slice(0,400))}</div>
        </div>`;
      });
      el.innerHTML=h;
    }else if(_wcTab==='send'){
      const dc=await(await fetch(`/api/wecom/bots/${_wcSelected}/contacts?limit=50`)).json();
      const cs=dc.contacts||[];
      let opts='<option value="">选择联系人…</option>'+cs.map(c=>`<option value="${esc(c.userid)}">${esc(c.userid)} (↓${c.msg_in_count||0})</option>`).join('');
      el.innerHTML=`<div style="padding:6px 0">
        <label style="font-size:11px;color:var(--tx2);display:block;margin-bottom:4px">目标 userid</label>
        <select id="wc-to" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:12px;font-family:var(--mono);margin-bottom:8px">${opts}</select>
        <label style="font-size:11px;color:var(--tx2);display:block;margin-bottom:4px">或手动输入</label>
        <input id="wc-to-manual" placeholder="userid (选择上面下拉框留空即用此)" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:12px;font-family:var(--mono);margin-bottom:8px">
        <label style="font-size:11px;color:var(--tx2);display:block;margin-bottom:4px">Markdown 内容 (企微 API 不吃 msgtype=text, 一律 markdown)</label>
        <textarea id="wc-md" rows="6" placeholder="**支持 markdown** 语法, 换行 \\n" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:12px;font-family:var(--mono);resize:vertical"></textarea>
        <div style="display:flex;gap:6px;margin-top:8px">
          <button class="abtn" onclick="wcSend()" style="font-size:11px;padding:5px 14px">✉️ 发送</button>
          <span id="wc-send-status" style="font-size:11px;color:var(--tx3);align-self:center"></span>
        </div>
      </div>`;
    }
  }catch(ex){el.innerHTML=`<div style="color:var(--dg);font-size:11px">Error: ${ex}</div>`;}
}
async function wcAddBot(){
  const bot_id=await promptCard('添加企微 Bot','企业微信 → 应用管理 → 智能机器人 → bot_id','bot_id (aib开头)');
  if(!bot_id)return;
  const secret=await promptCard('添加企微 Bot','Bot 密钥 secret','secret',bot_id);
  if(!secret)return;
  const r=await(await fetch('/api/wecom/bots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot_id:bot_id.trim(),secret:secret.trim()})})).json();
  if(!r.ok){toast('❌ 添加失败: '+(r.error||r.status||'unknown'));return;}
  toast('✅ Bot 已上线');_wcSelected=bot_id.trim();loadWc();
}
async function wcRemove(id){
  if(!await confirmCard('删除企微 Bot',id+' 将被移除, SQLite 保留.','删除'))return;
  await fetch(`/api/wecom/bots/${id}`,{method:'DELETE'});
  if(_wcSelected===id)_wcSelected='';
  toast('已删除');loadWc();
}
async function wcRelogin(id){
  await fetch(`/api/wecom/bots/${id}/relogin`,{method:'POST'});
  toast('🔁 重连中...');setTimeout(loadWc,1500);
}
async function wcClearMessages(){
  if(!_wcSelected)return;
  if(!await confirmCard('清空企微消息历史',
      `Bot ${_wcSelected} 的全部消息将被删除 (SQLite messages 表), 联系人保留.`,
      '清空'))return;
  try{
    const r=await(await fetch(`/api/wecom/bots/${_wcSelected}/messages`,{method:'DELETE'})).json();
    if(r.ok){toast(`✅ 已清空 ${r.deleted||0} 条消息`);_wcLoadTab();}
    else{toast('❌ 清空失败: '+(r.error||r.errmsg||'unknown'));}
  }catch(ex){toast('❌ '+ex);}
}
async function wcSend(){
  const sel=document.getElementById('wc-to').value;
  const manual=document.getElementById('wc-to-manual').value.trim();
  const to=sel||manual;
  const md=document.getElementById('wc-md').value.trim();
  const st=document.getElementById('wc-send-status');
  if(!to){st.textContent='❌ 请选/填 userid';st.style.color='var(--dg)';return;}
  if(!md){st.textContent='❌ 内容为空';st.style.color='var(--dg)';return;}
  st.textContent='发送中...';st.style.color='var(--tx3)';
  try{
    const r=await(await fetch(`/api/wecom/bots/${_wcSelected}/send`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({to,markdown:md})})).json();
    if(r.ok){st.textContent='✅ 已发送';st.style.color='var(--ac3)';document.getElementById('wc-md').value='';setTimeout(_wcLoadTab,800);}
    else{st.textContent='❌ '+(r.errmsg||r.error||'send failed');st.style.color='var(--dg)';}
  }catch(ex){st.textContent='❌ '+ex;st.style.color='var(--dg)';}
}
// promptCard: 卡片式输入 (代替原生 prompt, 符合"不要用 prompt 弹窗"规范)
function promptCard(title,desc,ph,defval=''){
  return new Promise(res=>{
    const w=document.createElement('div');
    w.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:9999;display:flex;align-items:center;justify-content:center';
    w.innerHTML=`<div style="background:var(--sf);border:1px solid var(--bd);border-radius:var(--rl);padding:16px 18px;max-width:420px;width:88%">
      <div style="font-size:13px;font-weight:600;color:var(--tx);margin-bottom:4px">${esc(title)}</div>
      <div style="font-size:11px;color:var(--tx3);margin-bottom:10px">${esc(desc)}</div>
      <input id="pc-in" placeholder="${esc(ph)}" value="${esc(defval)}" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:12px;font-family:var(--mono);margin-bottom:12px">
      <div style="display:flex;gap:6px;justify-content:flex-end">
        <button class="abtn sec" id="pc-cancel" style="font-size:11px;padding:4px 12px">取消</button>
        <button class="abtn" id="pc-ok" style="font-size:11px;padding:4px 12px">确定</button>
      </div>
    </div>`;
    document.body.appendChild(w);
    const inp=w.querySelector('#pc-in');inp.focus();inp.select();
    const done=(v)=>{document.body.removeChild(w);res(v);};
    w.querySelector('#pc-ok').onclick=()=>done(inp.value);
    w.querySelector('#pc-cancel').onclick=()=>done('');
    inp.onkeydown=(e)=>{if(e.key==='Enter')done(inp.value);if(e.key==='Escape')done('');};
    w.onclick=(e)=>{if(e.target===w)done('');};
  });
}
