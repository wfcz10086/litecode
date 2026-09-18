import { typeOf } from '../utils/fileTypes.js';
import { imgLazyObserver } from '../utils/perf.js';

const BLANK_SRC = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';

function fileUrl(path) {
  return '/api/files/download?path=' + encodeURIComponent(path);
}

function previewUrl(path) {
  return '/api/files/preview?path=' + encodeURIComponent(path);
}

function fileName(path) {
  return (path || '').split('/').pop().split('?')[0] || path;
}

async function headCheck(url) {
  try { const r = await fetch(url, { method: 'HEAD' }); return r.ok; }
  catch { return false; }
}

function makeDownloadBtn(path, label) {
  const btn = document.createElement('a');
  btn.href = fileUrl(path);
  btn.download = fileName(path);
  btn.className = 'pcard__dl-btn';
  btn.textContent = label || '下载';
  return btn;
}

function makeHeader(icon, name) {
  const hdr = document.createElement('div');
  hdr.className = 'pcard__header';
  const ic = document.createElement('span');
  ic.className = 'pcard__icon';
  ic.textContent = icon;
  const nm = document.createElement('span');
  nm.className = 'pcard__name';
  nm.textContent = name;
  hdr.appendChild(ic);
  hdr.appendChild(nm);
  return hdr;
}

export function createPreviewCard(path) {
  const { icon, inline } = typeOf(path);
  const name = fileName(path);
  const card = document.createElement('div');
  card.className = 'preview-card';

  if (inline === 'img') {
    const img = document.createElement('img');
    img.dataset.src = fileUrl(path);
    img.src = BLANK_SRC;
    img.alt = name;
    img.className = 'pcard__img';
    img.addEventListener('click', () => {
      document.dispatchEvent(new CustomEvent('lightbox-open', { detail: { src: img.dataset.src, name } }));
    });
    imgLazyObserver.observe(img);
    card.appendChild(img);

  } else if (inline === 'video') {
    const vid = document.createElement('video');
    vid.src = fileUrl(path);
    vid.controls = true;
    vid.className = 'pcard__video';
    card.appendChild(vid);

  } else if (inline === 'audio') {
    card.appendChild(makeHeader(icon, name));
    const aud = document.createElement('audio');
    aud.src = fileUrl(path);
    aud.controls = true;
    aud.className = 'pcard__audio';
    card.appendChild(aud);

  } else if (inline === 'iframe') {
    card.appendChild(makeHeader(icon, name));
    const fr = document.createElement('iframe');
    fr.src = previewUrl(path);
    fr.className = 'pcard__iframe';
    fr.setAttribute('sandbox', 'allow-scripts allow-same-origin');
    card.appendChild(fr);

  } else if (inline === 'text') {
    card.appendChild(makeHeader(icon, name));
    const actions = document.createElement('div');
    actions.className = 'pcard__actions';
    const viewBtn = document.createElement('button');
    viewBtn.className = 'pcard__view-btn';
    viewBtn.textContent = '查看';
    viewBtn.addEventListener('click', () => {
      document.dispatchEvent(new CustomEvent('file-view-request', { detail: { path } }));
    });
    actions.appendChild(viewBtn);
    actions.appendChild(makeDownloadBtn(path, '下载'));
    card.appendChild(actions);

  } else if (inline === 'office') {
    card.appendChild(makeHeader(icon, name));
    const actions = document.createElement('div');
    actions.className = 'pcard__actions';
    const openBtn = document.createElement('a');
    openBtn.className = 'pcard__open-btn';
    openBtn.textContent = '在新窗口打开';
    openBtn.target = '_blank';
    openBtn.rel = 'noopener';
    const url = fileUrl(path);
    openBtn.href = url;
    openBtn.setAttribute('aria-disabled', 'true');
    openBtn.style.opacity = '0.5';
    headCheck(url).then(ok => {
      if (ok) {
        openBtn.removeAttribute('aria-disabled');
        openBtn.style.opacity = '';
      } else {
        openBtn.textContent = '文件不可访问';
      }
    });
    actions.appendChild(openBtn);
    card.appendChild(actions);

  } else {
    card.appendChild(makeHeader(icon, name));
    const actions = document.createElement('div');
    actions.className = 'pcard__actions';
    actions.appendChild(makeDownloadBtn(path));
    card.appendChild(actions);
  }

  return card;
}
