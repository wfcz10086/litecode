/* timeline-driven anime renderer — seek(t) must be a pure function of t */
const TL = window.TL;               // {scenes:[{id,start,dur,text,...}], total}
const $ = id => document.getElementById(id);
const clamp = (v,a,b)=>Math.min(b,Math.max(a,v));
const ez = x => { x=clamp(x,0,1); return x*x*(3-2*x); };
const oback = x => { x=clamp(x,0,1); const c=1.70158; return 1+(c+1)*Math.pow(x-1,3)+c*Math.pow(x-1,2); };
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}}
const R = mulberry32(1929);
const fmt = v => "$"+Math.round(v).toLocaleString("en-US");

/* ---------- 初始化粒子/元素 (一次性) ---------- */
const candles=[],coins=[],conf=[],heads=[];
(function init(){
  const C=$("candles");
  for(let i=0;i<16;i++){const d=document.createElement("div");d.className="candle";
    const w=14+R()*22,h=70+R()*220,x=R()*1040,sp=260+R()*380,off=R()*2200;
    d.style.cssText=`width:${w}px;height:${h}px;left:${x}px;background:${i%3?"#e63946":"#8f1f2a"};box-shadow:0 0 18px rgba(230,57,70,.35)`;
    C.appendChild(d);candles.push({d,sp,off,h});}
  const P=$("coinpile");
  for(let i=0;i<26;i++){const d=document.createElement("div");d.className="coin";
    const r=58+R()*70,x=150+R()*720;
    d.style.cssText+=`width:${r}px;height:${r}px;left:${x}px;font-size:${r*.5}px;top:-140px`;d.textContent="$";
    P.appendChild(d);coins.push({d,r,x,t0:.4+i*.11});}
  const F=$("confetti");
  for(let i=0;i<34;i++){const d=document.createElement("div");
    const x=R()*1080,s=10+R()*16;
    d.style.cssText=`position:absolute;width:${s}px;height:${s*2.2}px;left:${x}px;background:${R()>.35?"#f5c451":"#fff"};bottom:-40px;opacity:0`;
    F.appendChild(d);conf.push({d,x,sp:200+R()*320,off:R()*3});}
  const Cr=$("crowd");
  for(let i=0;i<22;i++){const d=document.createElement("div");d.className="head";
    const x=20+R()*1000,sc=.6+R()*.8;
    d.style.cssText+=`left:${x}px;transform:scale(${sc})`;
    Cr.appendChild(d);heads.push({d,x,sc,ph:R()*6,fall:R()>.55});}
  // S1 行情牌
  const tick=["UR","NYC","BOS","PHL","BAL","SRY","MET","WAB","DEL","LVW","CAN","ERI"];
  const B=$("board");
  for(let i=0;i<12;i++){const d=document.createElement("div");d.className="qrow";
    const a=(90+R()*40)|0,b=(1/4|0)+R()*3;
    d.textContent=` ${tick[i].padEnd(4)} ${a} ${b.toFixed(2).padStart(5)}  ${R()>.5?"▲":"▼"}`;
    B.appendChild(d);}
  // S3 破产章
  [[430,760,"破产·1908",3.4],[610,740,"破产·1911",4.9],[790,730,"再破产",6.4]]
   .forEach(([x,y,txt,at])=>{const d=document.createElement("div");
    d.className="crashstamp";d.textContent=txt;d.style.left=x+"px";d.style.top=y+"px";
    d.dataset.at=at;$("sc3").appendChild(d);});
  // S0 大标题字
  "天才交易员的陨落".split("").forEach(ch=>{const s=document.createElement("span");s.textContent=ch;$("s0title").appendChild(s);});
  // S5 水底暗层
  const dk=document.createElement("div");dk.id="dark5";
  dk.style.cssText="position:absolute;inset:0;background:#02040a;opacity:0";$("sc5").prepend(dk);
  // S5 涟漪
  [[900,120],[950,200],[990,160]].forEach(([y])=>{const d=document.createElement("div");
    d.className="ripple";d.style.top=y+"px";$("sc5").appendChild(d);
    coins.push({d,ripple:true,y});});
  $("sub").parentElement;
})();

/* ---------- 字幕 ---------- */
function phrases(text){return text.split(/(?<=[，。！？；：])/).filter(s=>s.trim());}
const SUBS = TL.scenes.map(s=>{const ph=phrases(s.text);
  const tot=ph.reduce((a,x)=>a+x.length,0);let c=0;
  return ph.map(p=>{const t0=c/tot;c+=p.length;return{t0,t1:c,p};});});
let lastSub="";
function setSub(subIdx,u,dur){
  const el=$("sub");const list=SUBS[subIdx];
  const frac=clamp((u-0.1)/((dur-0.4)||1),0,1);
  const cur=list.find(x=>frac>=x.t0&&frac<x.t1)||list[list.length-1];
  const txt=cur.p.replace(/[。！？]/g,"");
  if(txt!==lastSub){lastSub=txt;el.innerHTML=txt.replace(/(\$[\d,]+M?|一亿美元|三百万美元|五百美元|\d+岁)/g,"<b>$1</b>").replace(/(破产|枪响|失败)/g,'<span class="hi">$1</span>');}
  const inScene=(frac-cur.t0)/((cur.t1-cur.t0)||1);
  el.style.opacity=clamp(inScene*8,0,1);
  el.style.transform=`scale(${1+0.06*(1-clamp(inScene*6,0,1))})`;
}

/* ---------- 各场景 ---------- */
const ACTS=["序章 · 1929","第一幕 · 抄牌少年","第二幕 · 1907","第三幕 · 破产轮回","第四幕 · 黑色星期四","第五幕 · 陨落","终章 · 3202"];

function upS0(u,d){
  candles.forEach(c=>{const y=((u*c.sp+c.off)%(d+400))-260;c.d.style.top=y+"px";});
  $("s0num").textContent=fmt(1e8*ez(u/4));
  $("s0num").style.opacity=u<4.4?1:0;
  $("s0title").querySelectorAll("span").forEach((s,i)=>{
    const p=oback((u-4.8-i*.16)/.5);s.style.opacity=clamp(p,0,1);
    s.style.transform=`translateY(${60*(1-clamp(p,0,1))}px) scale(${1.3-.3*clamp(p,0,1)})`;});
  $("s0tape").style.left=(-(u*230)%1080)+"px";
}
function upS1(u,d){
  $("board").querySelectorAll(".qrow").forEach((r,i)=>{
    const p=ez((u-.4-i*.28)/.5);r.style.opacity=p;
    r.style.transform=`translateX(${30*(1-p)}px)`;});
  $("chalkdust").setAttribute("opacity",(Math.sin(u*7)>.55&&u<5)?1:0);
  const st=$("s1stamp");
  let txt=null,tt=0;
  if(u<6.2){txt="14岁";tt=(u-1)/.6;}
  else if(u<8.6){txt="20岁";tt=(u-6.4)/.6;}
  if(txt){st.textContent=txt;const p=oback(tt);st.style.opacity=clamp(tt*4,0,1);
    st.style.transform=`scale(${3-2*clamp(p,0,1)}) rotate(-6deg)`;}
  else st.style.opacity=0;
  const ban=$("ban"),bp=oback((u-8.9)/.55);
  ban.style.opacity=clamp((u-8.9)*3,0,1);
  ban.style.transform=`rotate(-18deg) scale(${3-2*clamp(bp,0,1)})`;
}
function upS2(u,d){
  $("fallpath").style.strokeDashoffset=2200*(1-ez(u/4));
  $("s2num").textContent=fmt(3e6*ez(u/4.2));
  coins.filter(c=>!c.ripple).forEach(c=>{
    const p=clamp((u-c.t0)/.7,0,1),e=ez(p);
    const yTop=-140, yBot=1920-360-c.r/2-(c.r%50);
    c.d.style.top=(yTop+(yBot-yTop)*e)+"px";
    c.d.style.transform=`rotate(${(1-e)*360}deg)`;c.d.style.opacity=p>0?1:0;});
  const mo=ez((u-5)/.7);$("morgan").style.opacity=mo;
  $("morgan").style.transform=`translateY(${70*(1-mo)}px)`;
  const bb=$("bubble"),b=oback((u-6.2)/.5);
  bb.style.opacity=clamp((u-6.2)*4,0,1);bb.style.transform=`scale(${clamp(b,0,1)})`;
}
function upS3(u,d){
  $("curvepath").style.strokeDashoffset=3000*(1-ez(u/8));
  document.querySelectorAll(".crashstamp").forEach(s=>{
    const p=oback((u-(+s.dataset.at))/.45);
    s.style.opacity=clamp((u-(+s.dataset.at))*5,0,1);
    s.style.transform=`scale(${2.4-1.4*clamp(p,0,1)}) rotate(-12deg)`;});
  const f=$("five00"),fp=oback((u-9.6)/.6);
  f.style.opacity=clamp((u-9.6)*3,0,1);
  f.style.transform=`scale(${clamp(fp,0,1)})`;
  f.innerHTML='$500 <span style="font-size:70px;color:#fff">→ 几天赢回 →</span>';
}
function upS4(u,d){
  $("crashline").style.strokeDashoffset=2000*(1-ez(u/5));
  $("hisline").style.strokeDashoffset=1200*(1-ez((u-4.6)/1.8));
  $("hislabel").setAttribute("opacity",ez((u-6)/.5));
  heads.forEach(h=>{let y=0,r=0;
    if(h.fall&&u>1.5){const p=ez((u-1.5)/1.2);y=p*70;r=p*50;}
    const bob=Math.sin(u*6+h.ph)*6;
    h.d.style.transform=`scale(${h.sc}) translate(${bob}px,${y}px) rotate(${r}deg)`;});
  conf.forEach(c=>{const p=clamp((u-4.6-c.off*.2)/4,0,1);
    c.d.style.bottom=(p*1500)+"px";c.d.style.opacity=p>0&&p<1?(.9*(1-p*.6)):0;
    c.d.style.transform=`translateY(0) rotate(${u*160+c.off*40}deg)`;});
  const b=$("s4big"),bp=oback((u-7)/.6);
  b.style.opacity=clamp((u-7)*3,0,1);b.style.transform=`scale(${clamp(bp,0,1)})`;
}
function upS5(u,d){
  const p=clamp(u/(d-1),0,1);
  $("dark5").style.opacity=p*.5;
  $("bar1").style.transform=`translateY(${p*520}px) rotate(${p*7}deg)`;
  $("bar1").style.opacity=1-ez((u-6.9)/1.3);          // 入水线附近沉没淡出
  $("watersurf").style.opacity=.9*ez((u-2)/1)*(1-ez((u-(d-2))/1.5));
  coins.filter(c=>c.ripple).forEach((c,i)=>{          // 入水后才起涟漪
    const act=u>6.6,rp=(u-6.6)*.5+i*.3;
    c.d.style.opacity=act?(1-rp%1)*.55:0;
    if(!act)return;
    c.d.style.width=(60+(rp%1)*420)+"px";c.d.style.height=(16+(rp%1)*90)+"px";});
  const dp=oback((u-3)/.7);$("s5debt").style.opacity=clamp((u-3)*3,0,1);
  $("s5debt").style.transform=`translateY(${-140*(1-clamp(dp,0,1))}px)`;
  $("oldman").style.opacity=ez((u-5)/1.2);
}
function upS6(u,d){
  let li=1;
  if(u>3.1) li=u<4?(R()<.5?.2:1):0;           // 门缝灯光闪烁熄灭
  $("doorlight").style.opacity=li;
  $("doornum").style.opacity=1-ez((u-8)/1);
  $("door").style.opacity=1-ez((u-8.5)/1);
  const n=$("note");
  if(u>4.2){n.style.opacity=clamp((u-4.2)/.4,0,1);
    const full="我的人生，是一次失败。";
    const k=Math.floor((u-4.4)/.24);
    n.textContent=full.slice(0,clamp(k,0,full.length))+(k<full.length&&k>0&&Math.sin(u*9)>0?"｜":"");}
  else n.style.opacity=0;
  $("quote").style.opacity=ez((u-9.5)/1)*(1-ez((u-13.2)/.8));
  $("endcard").style.opacity=ez((u-13.4)/1);
}
const UP=[upS0,upS1,upS2,upS3,upS4,upS5,upS6];

/* ---------- 主入口 ---------- */
window.seek=function(t){
  const G=.7;
  TL.scenes.forEach((s,i)=>{
    const sc=$("sc"+i);
    const a=ez((t-(s.start-.1))/.45)*(1-ez((t-(s.start+s.dur-.2))/.5));
    sc.style.opacity=a;
    if(a>0&&t<s.start+s.dur+G) UP[i](clamp(t-s.start,0,s.dur),s.dur);
  });
  let ci=0;TL.scenes.forEach((s,i)=>{if(t>=s.start)ci=i;});
  const cur=TL.scenes[ci];
  if($("actband").dataset.i!=ci){$("actband").dataset.i=ci;$("actband").textContent=ACTS[ci];}
  const u=t-cur.start;
  if(u>=0&&u<=cur.dur+.2)setSub(ci,clamp(u,0,cur.dur),cur.dur);
  else if(u>cur.dur+.55&&ci<TL.scenes.length-1&&TL.scenes[ci+1].start-t>0)$("sub").style.opacity=0;
};
window.seek(0);
