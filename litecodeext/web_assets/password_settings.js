// password_settings.js — 设置面板 "修改密码" 卡片 (POST /api/auth/password)
// 挂载点: chat_panels.js loadSet() 里的 #pwd-card-host; 不用 prompt(), 内嵌表单 + 行内提示.
(function(){
  const IN='width:100%;box-sizing:border-box;padding:4px 6px;background:var(--bg);border:1px solid var(--bd2);border-radius:4px;color:var(--tx);font-size:11px;font-family:var(--mono)';
  function renderPasswordCard(){
    const host=document.getElementById('pwd-card-host');if(!host)return;
    host.innerHTML=`<div style="margin:14px 0 6px;font-size:11px;color:var(--tx3);font-family:var(--mono)">修改密码</div>
    <form id="pwd-form" autocomplete="off" onsubmit="return false" style="display:flex;flex-direction:column;gap:5px;padding:8px 10px;background:var(--sf);border:1px solid var(--bd);border-radius:var(--rs)">
      <input type="password" id="pwd-old" placeholder="当前密码" autocomplete="current-password" style="${IN}">
      <input type="password" id="pwd-new" placeholder="新密码 (至少 8 位)" autocomplete="new-password" style="${IN}">
      <input type="password" id="pwd-new2" placeholder="再次输入新密码" autocomplete="new-password" style="${IN}">
      <div style="display:flex;gap:6px;align-items:center">
        <span id="pwd-msg" role="status" style="flex:1;font-size:10px;font-family:var(--mono);color:var(--tx3)"></span>
        <button type="button" class="abtn" id="pwd-submit" onclick="pwdChange()" style="font-size:10px;padding:3px 10px">修改密码</button>
      </div>
    </form>`;
  }
  function msg(text,kind){
    const el=document.getElementById('pwd-msg');if(!el)return;
    el.textContent=text;
    el.style.color=kind==='ok'?'var(--ac3)':kind==='err'?'var(--dg)':'var(--wn)';
  }
  async function pwdChange(){
    const o=document.getElementById('pwd-old'),n=document.getElementById('pwd-new'),n2=document.getElementById('pwd-new2');
    if(!o||!n||!n2)return;
    if(!o.value||!n.value||!n2.value){msg('请填写全部三项','err');return;}
    if(n.value.length<8){msg('新密码至少 8 个字符','err');return;}
    if(n.value!==n2.value){msg('两次输入的新密码不一致','err');return;}
    if(n.value===o.value){msg('新密码不能与当前密码相同','err');return;}
    const btn=document.getElementById('pwd-submit');if(btn)btn.disabled=true;
    msg('提交中…','wait');
    try{
      const r=await fetch('/api/auth/password',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({old_password:o.value,new_password:n.value})});
      let d={};try{d=await r.json();}catch(_){}
      if(r.ok&&d.ok){
        o.value=n.value=n2.value='';
        msg(`密码已修改。其它设备上的登录已退出${d.revoked?` (${d.revoked} 个会话)`:''}, 当前页面保持登录。`,'ok');
        if(typeof toast==='function')toast('密码已修改');
      }else if(r.status===429){
        msg('失败次数过多, 请稍后再试 ('+(d.detail||'')+')','err');
      }else if(r.status===401){
        msg('登录已失效, 请刷新页面重新登录','err');
      }else{
        msg(d.detail||('修改失败 HTTP '+r.status),'err');
      }
    }catch(ex){msg('网络错误: '+ex,'err');}
    finally{if(btn)btn.disabled=false;}
  }
  window.renderPasswordCard=renderPasswordCard;
  window.pwdChange=pwdChange;
})();
