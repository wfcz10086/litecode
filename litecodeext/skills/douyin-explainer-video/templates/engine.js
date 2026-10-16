/* engine.js — 时间轴驱动的竖版解说视频渲染器 (1080x1920)
 * 铁律: seek(t) 必须是 t 的纯函数 (逐帧截图要可复现; 禁止 setTimeout / CSS transition / Date.now)
 * 数据: window.TL (render.py 由 timeline.json 生成 tl.js) = script.json + 每场 start/dur + total
 * 场景: TL.scenes[i].type ∈ LAYOUTS (下方 7 种通用版式), 参数在 scenes[i].params
 * 扩展: 题材专属画面写 custom.js (在 engine.js 之前加载):
 *   window.LAYOUTS_EXT = { mytype: { build(el, p) {...}, update(el, p, u, dur) {...} } }
 *   场景 type 填 "mytype"; update 里可直接用本文件的 ez / oback / clamp / win / mk / R / num
 */
const TL = window.TL;
const $ = id => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const ez = x => { x = clamp(x, 0, 1); return x * x * (3 - 2 * x); };            // smoothstep
const oback = x => { x = clamp(x, 0, 1); const c = 1.70158; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
const win = (u, a, b, fin = .4, fout = .4) => ez((u - a) / fin) * (1 - ez((u - b) / fout));  // [a,b] 内显示
function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const R = rng(TL.seed || 2024);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (v, p = {}) => (v < 0 ? "-" : "") + (p.prefix || "") + Math.abs(Math.round(v)).toLocaleString("en-US") + (p.suffix || "");
// 按字数缩字号, 保证单行不溢出 (width = 可用宽度 px)
const fitFont = (text, max, width) => Math.min(max, Math.floor(width / Math.max([...String(text)].length, 1)));
const title = (el, text, css = "") => { const d = mk(el, "h-title", esc(text), css); d.style.fontSize = fitFont(text, 96, 820) + "px"; return d; };
const mk = (parent, cls, html = "", css = "") => { const d = document.createElement("div"); d.className = cls; d.innerHTML = html; if (css) d.style.cssText = css; parent.appendChild(d); return d; };

/* ---------- 7 种通用版式 ---------- */
const LAYOUTS = window.LAYOUTS = Object.assign({
  // 开场钩子: 数字从 0 滚到 number + 大标题逐字砸下 + 底部滚动警示带
  hook: {
    build(el, p) {
      mk(el, "big num hook-num", "", "top:600px");
      const t = mk(el, "hook-title");
      t.style.fontSize = fitFont(p.title || "", 140, 960) + "px";
      [...(p.title || "")].forEach(ch => mk(t, "ch", esc(ch)));
      if (p.tape) mk(el, "tape", esc((p.tape + "　").repeat(8)));
    },
    update(el, p, u) {
      const n = el.querySelector(".hook-num");
      n.textContent = p.number != null ? num(p.number * ez(u / 3.5), p) : "";
      n.style.transform = `scale(${1 + .08 * Math.sin(u * 7) * (1 - ez(u / 3.5))})`;
      el.querySelectorAll(".hook-title .ch").forEach((c, i) => {
        const k = oback((u - 1.2 - i * .12) / .45);
        c.style.opacity = clamp((u - 1.2 - i * .12) / .2, 0, 1);
        c.style.transform = `translateY(${(1 - k) * 80}px) scale(${1.4 - .4 * k})`;
      });
      const tp = el.querySelector(".tape");
      if (tp) tp.style.transform = `translateX(${-(u * 160) % 1080}px)`;
    }
  },
  // 标题卡: 背景巨大描边字 (年份/关键词) + 标题 + 要点逐条出现
  headline: {
    build(el, p) {
      if (p.kicker) mk(el, "kicker", esc(p.kicker));
      title(el, p.title || "");
      const L = mk(el, "h-lines");
      (p.lines || []).forEach(x => mk(L, "h-line", esc(x)));
    },
    update(el, p, u, dur) {
      const k = el.querySelector(".kicker");
      if (k) k.style.transform = `scale(${1.25 - .25 * ez(u / 2)})`;
      const t = el.querySelector(".h-title");
      t.style.opacity = ez(u / .5); t.style.transform = `translateY(${(1 - oback(u / .6)) * 60}px)`;
      const ls = el.querySelectorAll(".h-line"), step = (dur - 1) / Math.max(ls.length, 1);
      ls.forEach((d, i) => { const a = ez((u - .8 - i * step) / .4); d.style.opacity = a; d.style.transform = `translateX(${(1 - a) * -80}px)`; });
    }
  },
  // 印章: 一串印章在本场时间内均匀砸下 (破产/封神/拉黑…)
  stamps: {
    build(el, p) {
      if (p.title) title(el, p.title, "top:300px");
      (p.stamps || []).forEach((s, i) => mk(el, "stamp", esc(s),
        `left:${70 + (i % 2) * 280 + R() * 40}px;top:${620 + i * 230}px;--rot:${-12 + R() * 24}deg`));
    },
    update(el, p, u, dur) {
      const ss = el.querySelectorAll(".stamp"), step = (dur - 1) / Math.max(ss.length, 1);
      ss.forEach((d, i) => { const x = (u - .6 - i * step) / .3; d.style.opacity = clamp(x * 3, 0, 1); d.style.transform = `rotate(var(--rot)) scale(${3 - 2 * oback(x)})`; });
      const t = el.querySelector(".h-title"); if (t) t.style.opacity = ez(u / .5);
    }
  },
  // 数字滚动: from → to (财富/人数/涨跌), 下方进度条; to < from 时变红
  counter: {
    build(el, p) {
      if (p.label) title(el, p.label, "top:420px");
      mk(el, "big num c-num", "", "top:760px");
      const b = mk(el, "c-bar"); mk(b, "c-fill");
      if (p.note) mk(el, "c-note", esc(p.note));
    },
    update(el, p, u, dur) {
      const a = ez((u - .4) / Math.max(dur * .6, 1)), v = (p.from || 0) + ((p.to || 0) - (p.from || 0)) * a;
      const n = el.querySelector(".c-num"); n.textContent = num(v, p);
      n.classList.toggle("down", (p.to || 0) < (p.from || 0));
      el.querySelector(".c-fill").style.width = (a * 100) + "%";
      const t = el.querySelector(".h-title"); if (t) t.style.opacity = ez(u / .5);
      const nt = el.querySelector(".c-note"); if (nt) nt.style.opacity = ez((u - dur * .65) / .5);
    }
  },
  // 对比: 左右两张卡滑入 + VS (人物 vs 人物 / 期望 vs 现实)
  versus: {
    build(el, p) {
      ["left", "right"].forEach(side => { const c = p[side] || {}; mk(el, `vs-card ${side}`, `<div class="vs-name">${esc(c.name || "")}</div><div class="vs-val">${esc(c.value || "")}</div>`); });
      mk(el, "vs-badge", "VS");
    },
    update(el, p, u) {
      el.querySelector(".vs-card.left").style.transform = `translateX(${(1 - oback((u - .2) / .6)) * -700}px)`;
      el.querySelector(".vs-card.right").style.transform = `translateX(${(1 - oback((u - .6) / .6)) * 700}px)`;
      const b = el.querySelector(".vs-badge"); b.style.opacity = ez((u - 1.1) / .3); b.style.transform = `scale(${3 - 2 * oback((u - 1.1) / .4)}) rotate(-8deg)`;
    }
  },
  // 金句收尾: 打字机 + 结尾关注卡
  quote: {
    build(el, p) { mk(el, "q-text"); if (p.endcard) mk(el, "endcard", esc(p.endcard)); },
    update(el, p, u, dur) {
      const full = p.quote || "", k = Math.floor((u - .5) / .16);
      el.querySelector(".q-text").textContent = full.slice(0, clamp(k, 0, full.length)) + (k > 0 && k < full.length && Math.sin(u * 9) > 0 ? "｜" : "");
      const e = el.querySelector(".endcard"); if (e) e.style.opacity = ez((u - (dur - 3)) / .8);
    }
  },
  // 图片推拉 (ken burns): params.src 指向本地图片 (相对 video.html)
  image: {
    build(el, p) { mk(el, "img-wrap", `<img src="${esc(p.src || "")}">`); if (p.caption) title(el, p.caption, "top:auto;bottom:520px"); },
    update(el, p, u, dur) {
      el.querySelector("img").style.transform = `scale(${1.05 + .15 * u / dur}) translateY(${-30 * u / dur}px)`;
      const c = el.querySelector(".h-title"); if (c) c.style.opacity = ez((u - .5) / .5);
    }
  },
}, window.LAYOUTS_EXT || {});

/* ---------- 字幕: 按标点切句, 句子时长按字数比例分配; 关键词上色 ---------- */
const HL = TL.highlight || {};
const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const HL_RE = ["gold", "red"].map(k => (HL[k] || []).length ? new RegExp("(" + HL[k].map(reEsc).join("|") + ")", "g") : null);
function phrases(text) { return text.split(/(?<=[，。！？；：])/).filter(s => s.trim()); }
const SUBS = TL.scenes.map(s => { const ph = phrases(s.text); const tot = ph.reduce((a, x) => a + x.length, 0) || 1; let c = 0; return ph.map(p => { const t0 = c / tot; c += p.length; return { t0, t1: c / tot, p }; }); });
function subHtml(t) {
  let h = esc(t.replace(/[。！？]/g, ""));
  if (HL_RE[0]) h = h.replace(HL_RE[0], "<b>$1</b>");
  if (HL_RE[1]) h = h.replace(HL_RE[1], '<span class="hi">$1</span>');
  return h;
}
let lastSub = null;
function setSub(i, u, dur) {
  const el = $("sub"), list = SUBS[i]; if (!list.length) { el.style.opacity = 0; return; }
  const f = clamp((u - .1) / ((dur - .4) || 1), 0, 1);
  const cur = list.find(x => f >= x.t0 && f < x.t1) || list[list.length - 1];
  if (cur.p !== lastSub) { lastSub = cur.p; el.innerHTML = subHtml(cur.p); }
  const k = (f - cur.t0) / ((cur.t1 - cur.t0) || 1);
  el.style.opacity = clamp(k * 8, 0, 1);
  el.style.transform = `scale(${1 + .06 * (1 - clamp(k * 6, 0, 1))})`;
}

/* ---------- 搭场景 + 背景浮尘 ---------- */
(function init() {
  const st = $("scenes");
  TL.scenes.forEach((s, i) => {
    const L = LAYOUTS[s.type];
    if (!L) throw new Error(`场景 ${s.id} 的 type "${s.type}" 不存在, 可选: ${Object.keys(LAYOUTS).join(", ")}`);
    const el = mk(st, `scene t-${s.type}`); el.id = "sc" + i;
    L.build(el, s.params || {});
  });
  const D = $("dust");
  for (let i = 0; i < 40; i++) mk(D, "mote", "", `left:${R() * 1080}px;width:${3 + R() * 6}px;height:${3 + R() * 6}px;--sp:${40 + R() * 90};--off:${R() * 1920}`);
})();

/* ---------- 主入口 ---------- */
window.seek = function (t) {
  TL.scenes.forEach((s, i) => {
    const el = $("sc" + i), a = ez((t - (s.start - .1)) / .45) * (1 - ez((t - (s.start + s.dur - .2)) / .5));
    el.style.opacity = a;
    if (a > 0) LAYOUTS[s.type].update(el, s.params || {}, clamp(t - s.start, 0, s.dur + 1), s.dur);
  });
  document.querySelectorAll("#dust .mote").forEach(m => { const sp = +m.style.getPropertyValue("--sp"), off = +m.style.getPropertyValue("--off"); m.style.top = (1920 - ((t * sp + off) % 2000)) + "px"; });
  let ci = 0; TL.scenes.forEach((s, i) => { if (t >= s.start) ci = i; });
  const cur = TL.scenes[ci], act = $("actband");
  if (act.dataset.i != ci) { act.dataset.i = ci; act.textContent = cur.act || ""; }
  $("prog").style.width = (100 * clamp(t / TL.total, 0, 1)) + "%";
  const u = t - cur.start;
  if (u >= 0 && u <= cur.dur + .2) setSub(ci, clamp(u, 0, cur.dur), cur.dur); else $("sub").style.opacity = 0;
};
window.addEventListener("load", () => window.seek(0));
