/* _pptx_extract.js — 在浏览器里把每个 section.slide 拆成可还原的元素清单 (html2pptx.py 用)。
 * 调用: page.evaluate(<本文件>, {selector})  →  [{w,h,items,notes,text,metrics}]
 * 坐标一律相对所在 slide 左上角 (CSS px); items 按绘制顺序 (z 再稳定排序)。
 * 元素类型: shape (纯色块/边框) · image (img / url 背景) · cap (需要截图的元素: 渐变/滤镜/svg/canvas/旋转)
 *           · text (文字块, 含段落与 run 样式) · table (原生表格)。
 */
(args) => {
  const SEL = (args && args.selector) || 'section.slide';
  const MAX_ITEMS = (args && args.maxItems) || 400;
  const INLINE = new Set(['inline', 'contents']);
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK']);
  const CAP_TAGS = new Set(['SVG', 'CANVAS', 'VIDEO', 'IFRAME', 'OBJECT', 'EMBED', 'MATH']);
  let capSeq = 0;

  const cs = (e) => getComputedStyle(e);
  const px = (v) => parseFloat(v) || 0;
  const parseColor = (s) => {
    if (!s || s === 'transparent') return null;
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat);
    const a = p.length > 3 ? p[3] : 1;
    if (a <= 0.01) return null;
    return { r: Math.round(p[0]), g: Math.round(p[1]), b: Math.round(p[2]), a: a };
  };
  const visible = (e, st) => st.display !== 'none' && st.visibility !== 'hidden' && st.visibility !== 'collapse';
  const isInlineEl = (e) => {
    if (e.nodeType === 3) return true;
    if (e.nodeType !== 1) return false;
    if (CAP_TAGS.has(e.tagName.toUpperCase()) || e.tagName === 'IMG' || e.tagName === 'TABLE') return false;
    const st = cs(e);
    if (!INLINE.has(st.display)) return false;
    if (parseColor(st.backgroundColor) || px(st.borderTopWidth) > 0 || px(st.borderLeftWidth) > 0) return false; // 带底色的 span 当块处理
    for (const c of e.children) if (!isInlineEl(c)) return false;
    return true;
  };
  const opacityOf = (e, root) => {
    let o = 1;
    for (let n = e; n && n !== root.parentElement; n = n.parentElement) o *= parseFloat(cs(n).opacity);
    return o;
  };
  const zOf = (e, root) => {
    for (let n = e; n && n !== root; n = n.parentElement) {
      const st = cs(n);
      if (st.position !== 'static' && st.zIndex !== 'auto') return parseInt(st.zIndex) || 0;
    }
    return 0;
  };
  const rotated = (st) => {
    if (!st.transform || st.transform === 'none') return false;
    const m = st.transform.match(/matrix\(([^)]+)\)/);
    if (!m) return true;                               // matrix3d 等一律当复杂
    const v = m[1].split(',').map(parseFloat);
    return Math.abs(v[1]) > 0.01 || Math.abs(v[2]) > 0.01;
  };
  const radiusOf = (st, w, h) => {
    const r = st.borderTopLeftRadius || '0';
    if (r.endsWith('%')) return Math.min(w, h) * px(r) / 100;
    return Math.min(px(r), Math.min(w, h) / 2);
  };

  function textStyle(el, st) {
    const fam = (st.fontFamily || '').split(',').map(s => s.trim().replace(/^["']|["']$/g, ''));
    const deco = st.textDecorationLine || st.textDecoration || '';
    return {
      size: px(st.fontSize), bold: (parseInt(st.fontWeight) || 400) >= 600, italic: st.fontStyle === 'italic',
      underline: deco.includes('underline'), strike: deco.includes('line-through'),
      color: parseColor(st.color), fonts: fam, spacing: st.letterSpacing === 'normal' ? 0 : px(st.letterSpacing),
      transform: st.textTransform, sup: st.verticalAlign === 'super', sub: st.verticalAlign === 'sub',
    };
  }

  /* 把一组相邻的 inline 节点收集成 runs (br → 换行标记) */
  function collectRuns(nodes, runs) {
    for (const n of nodes) {
      if (n.nodeType === 3) {
        const pe = n.parentElement, st = cs(pe);
        let t = n.nodeValue;
        if (!/^pre/.test(st.whiteSpace)) t = t.replace(/\s+/g, ' ');
        if (!t) continue;
        const s = textStyle(pe, st);
        if (s.transform === 'uppercase') t = t.toUpperCase();
        else if (s.transform === 'lowercase') t = t.toLowerCase();
        runs.push(Object.assign({ text: t }, s, { href: pe.closest('a') ? pe.closest('a').href : '' }));
      } else if (n.nodeType === 1) {
        if (n.tagName === 'BR') { runs.push({ br: true }); continue; }
        const st = cs(n);
        if (!visible(n, st)) continue;
        collectRuns(Array.from(n.childNodes), runs);
      }
    }
  }
  function trimRuns(runs) {
    while (runs.length && !runs[0].br && !runs[0].text.trim()) runs.shift();
    while (runs.length && !runs[runs.length - 1].br && !runs[runs.length - 1].text.trim()) runs.pop();
    if (runs.length && runs[0].text) runs[0].text = runs[0].text.replace(/^\s+/, '');
    const last = runs[runs.length - 1];
    if (last && last.text) last.text = last.text.replace(/\s+$/, '');
    return runs;
  }
  function lineCount(range) {
    const tops = new Set();
    for (const r of range.getClientRects()) if (r.width > 0.5 && r.height > 0.5) tops.add(Math.round(r.top / 4));
    return Math.max(1, tops.size);
  }

  function slideOf(sec) {
    const sr = sec.getBoundingClientRect();
    const items = [];
    const metrics = { items: 0, caps: 0, capFullArea: 0, rotatedText: 0, textBlocks: 0 };
    const rel = (r) => ({ x: r.left - sr.left, y: r.top - sr.top, w: r.width, h: r.height });
    const push = (it, el) => { it.z = zOf(el, sec); it.seq = items.length; items.push(it); };

    function capture(el, mode, r) {
      const id = ++capSeq;
      el.setAttribute('data-ds-cap', String(id));
      push(Object.assign({ t: 'cap', id: id, mode: mode }, rel(r)), el);
      metrics.caps++;
      if (mode === 'full') metrics.capFullArea += r.width * r.height;
    }

    function imageItem(el, st, r, src, fit, pos, natW, natH) {
      // 被 overflow:hidden 的祖先裁切 (常见: .photo{overflow:hidden;border-radius} > img{object-fit:cover})
      let clip = { l: r.left, t: r.top, r: r.right, b: r.bottom }, radius = radiusOf(st, r.width, r.height);
      for (let a = el.parentElement; a && a !== sec.parentElement; a = a.parentElement) {
        const as = cs(a);
        if (as.overflow === 'visible' && as.overflowX === 'visible') continue;
        const ar = a.getBoundingClientRect();
        clip = { l: Math.max(clip.l, ar.left), t: Math.max(clip.t, ar.top), r: Math.min(clip.r, ar.right), b: Math.min(clip.b, ar.bottom) };
        if (!radius && Math.abs(ar.width - r.width) < 3 && Math.abs(ar.height - r.height) < 3) radius = radiusOf(as, ar.width, ar.height);
        if (a === sec) break;
      }
      if (clip.r - clip.l < 2 || clip.b - clip.t < 2) return;
      push(Object.assign({ t: 'image', src: src, fit: fit, pos: pos, natW: natW, natH: natH, radius: radius,
        opacity: opacityOf(el, sec),
        clip: { x: clip.l - sr.left, y: clip.t - sr.top, w: clip.r - clip.l, h: clip.b - clip.t } }, rel(r)), el);
    }

    function boxOf(el, st, r, isRoot) {
      // 自身的底色 / 边框 / 背景图 (不含子元素)
      const bgImg = st.backgroundImage && st.backgroundImage !== 'none' ? st.backgroundImage : '';
      const complex = /gradient/.test(bgImg) || (st.filter && st.filter !== 'none' && el.tagName !== 'IMG') ||
        (st.clipPath && st.clipPath !== 'none') || (st.maskImage && st.maskImage !== 'none') ||
        (st.webkitMaskImage && st.webkitMaskImage !== 'none');
      if (complex) { capture(el, 'self', r); return; }
      const fill = parseColor(st.backgroundColor);
      const sides = ['Top', 'Right', 'Bottom', 'Left'].map(s => ({
        w: st['border' + s + 'Style'] === 'none' ? 0 : px(st['border' + s + 'Width']),
        c: parseColor(st['border' + s + 'Color']) }));
      const uni = sides.every(s => s.w === sides[0].w && JSON.stringify(s.c) === JSON.stringify(sides[0].c));
      const op = opacityOf(el, sec);
      if (fill || (uni && sides[0].w > 0 && sides[0].c)) {
        push(Object.assign({ t: 'shape', fill: fill, radius: radiusOf(st, r.width, r.height), opacity: op,
          line: uni && sides[0].w > 0 && sides[0].c ? { w: sides[0].w, c: sides[0].c } : null, root: !!isRoot }, rel(r)), el);
      }
      if (!uni) {                                     // 单边框 (常见: 左侧强调竖条) → 细长矩形
        const R = rel(r);
        const geo = [[R.x, R.y, R.w, sides[0].w], [R.x + R.w - sides[1].w, R.y, sides[1].w, R.h],
                     [R.x, R.y + R.h - sides[2].w, R.w, sides[2].w], [R.x, R.y, sides[3].w, R.h]];
        sides.forEach((s, i) => {
          if (s.w > 0 && s.c) push({ t: 'shape', fill: s.c, radius: 0, opacity: op, line: null,
            x: geo[i][0], y: geo[i][1], w: geo[i][2], h: geo[i][3] }, el);
        });
      }
      const url = bgImg.match(/url\(["']?([^"')]+)["']?\)/);
      if (url) {
        const size = st.backgroundSize;
        const fit = size === 'contain' ? 'contain' : (size === '100% 100%' ? 'fill' : 'cover');
        const pos = (st.backgroundPosition || '50% 50%').split(' ').map(v => v.endsWith('%') ? px(v) / 100 : 0.5);
        const im = new Image(); im.src = url[1];
        imageItem(el, st, r, im.src, fit, pos, im.naturalWidth, im.naturalHeight);
      }
    }

    function textItem(el, nodes, st, pure, listPrefix) {
      const runs = trimRuns((() => { const a = []; collectRuns(nodes, a); return a; })());
      if (!runs.some(x => x.text && x.text.trim())) return;
      const range = document.createRange();
      range.setStartBefore(nodes[0]); range.setEndAfter(nodes[nodes.length - 1]);
      const tr = range.getBoundingClientRect();
      if (tr.width < 1 || tr.height < 1) return;
      let box = rel(tr);
      if (pure) {                                    // 纯文字容器: 横向用内容框 (对齐方式才对), 纵向用实际文字行
        const er = el.getBoundingClientRect();
        const l = er.left + px(st.borderLeftWidth) + px(st.paddingLeft);
        const rgt = er.right - px(st.borderRightWidth) - px(st.paddingRight);
        if (rgt - l > tr.width - 1) box = Object.assign(box, { x: l - sr.left, w: rgt - l });
      }
      if (rotated(st)) metrics.rotatedText++;
      const lh = st.lineHeight === 'normal' ? px(st.fontSize) * 1.25 : px(st.lineHeight);
      if (listPrefix) {
        const ind = px(st.fontSize) * 1.2;
        runs.unshift(Object.assign({}, runs.find(x => x.text) || {}, { text: listPrefix, br: false }));
        box.x -= ind; box.w += ind;
      }
      metrics.textBlocks++;
      push(Object.assign({ t: 'text', runs: runs, align: st.textAlign, lineHeight: lh,
        lines: lineCount(range), opacity: opacityOf(el, sec), dir: st.direction }, box), el);
    }

    function tableItem(el, r) {
      const rows = [];
      for (const tr of el.rows) {
        const rr = tr.getBoundingClientRect();
        const cells = [];
        for (const td of tr.cells) {
          const c = td.getBoundingClientRect(), st = cs(td);
          const runs = [];
          collectRuns(Array.from(td.childNodes), runs);
          cells.push(Object.assign({ fill: parseColor(st.backgroundColor) || parseColor(cs(tr).backgroundColor),
            runs: trimRuns(runs), align: st.textAlign, valign: st.verticalAlign,
            pad: [px(st.paddingTop), px(st.paddingRight), px(st.paddingBottom), px(st.paddingLeft)],
            border: st.borderBottomStyle !== 'none' && px(st.borderBottomWidth) > 0 ? { w: px(st.borderBottomWidth), c: parseColor(st.borderBottomColor) } : null,
            rowSpan: td.rowSpan, colSpan: td.colSpan }, rel(c)));
        }
        rows.push(Object.assign({ cells: cells }, rel(rr)));
      }
      push(Object.assign({ t: 'table', rows: rows }, rel(r)), el);
    }

    function walk(el, isRoot) {
      if (items.length > MAX_ITEMS) return;
      if (SKIP_TAGS.has(el.tagName)) return;
      if (el.matches('aside.notes, .notes[data-notes], [data-pptx="skip"]')) return;
      const st = cs(el);
      if (st.display === 'contents') { for (const c of el.children) walk(c); return; }
      if (!visible(el, st) || parseFloat(st.opacity) === 0) return;
      const r = el.getBoundingClientRect();
      const tag = el.tagName.toUpperCase();
      if (r.width < 0.5 && r.height < 0.5 && st.overflow === 'visible') { for (const c of el.children) walk(c); return; }
      if (CAP_TAGS.has(tag)) { capture(el, 'full', r); return; }
      if (st.webkitBackgroundClip === 'text' || st.backgroundClip === 'text' || (rotated(st) && el.textContent.trim())) {
        capture(el, 'full', r); return;                // 渐变字 / 旋转文字: 整块截图
      }
      if (tag === 'IMG') {
        if (!el.currentSrc && !el.src) return;
        if (st.filter && st.filter !== 'none') { capture(el, 'full', r); return; }
        const pos = (st.objectPosition || '50% 50%').split(' ').map(v => v.endsWith('%') ? px(v) / 100 : 0.5);
        imageItem(el, st, r, el.currentSrc || el.src, st.objectFit || 'fill', pos, el.naturalWidth, el.naturalHeight);
        return;
      }
      boxOf(el, st, r, isRoot);
      if (tag === 'TABLE') { tableItem(el, r); return; }
      const kids = Array.from(el.childNodes).filter(n => n.nodeType === 1 || (n.nodeType === 3 && n.nodeValue.length));
      const pure = kids.length > 0 && kids.every(isInlineEl);
      const isLi = tag === 'LI' && st.listStyleType !== 'none' && st.display === 'list-item';
      let prefix = '';
      if (isLi) {
        const ordered = el.parentElement && el.parentElement.tagName === 'OL';
        prefix = ordered ? (Array.from(el.parentElement.children).indexOf(el) + 1) + '. ' : '• ';
      }
      let group = [];
      const flush = () => {
        if (group.length) { textItem(el, group, st, pure, prefix); prefix = ''; }
        group = [];
      };
      for (const n of kids) {
        if (isInlineEl(n)) group.push(n);
        else { flush(); walk(n); }
      }
      flush();
    }

    walk(sec, true);
    metrics.items = items.length;
    items.sort((a, b) => (a.z - b.z) || (a.seq - b.seq));
    const notesEl = sec.querySelector('aside.notes, .notes[data-notes]');
    return { w: sr.width, h: sr.height, items: items, metrics: metrics,
      notes: notesEl ? notesEl.innerText.trim() : '',
      text: sec.innerText.replace(/\n{3,}/g, '\n\n').trim() };
  }

  return Array.from(document.querySelectorAll(SEL)).map(sec => {
    try { return slideOf(sec); }
    catch (e) {
      const r = sec.getBoundingClientRect();
      return { w: r.width, h: r.height, items: [], error: String(e), metrics: {}, notes: '', text: sec.innerText };
    }
  });
}
