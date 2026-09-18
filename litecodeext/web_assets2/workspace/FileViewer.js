import { typeOf } from '../utils/fileTypes.js';

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

export function createFileViewer() {
  const el = document.createElement('div');
  el.className = 'file-viewer';
  el.style.display = 'none';

  const header = document.createElement('div');
  header.className = 'file-viewer__header';

  const titleEl = document.createElement('span');
  titleEl.className = 'file-viewer__title';

  const closeBtn = document.createElement('button');
  closeBtn.className = 'file-viewer__close';
  closeBtn.textContent = '×';
  closeBtn.addEventListener('click', close);

  header.appendChild(titleEl);
  header.appendChild(closeBtn);

  const body = document.createElement('div');
  body.className = 'file-viewer__body';

  el.appendChild(header);
  el.appendChild(body);

  function clear() {
    body.innerHTML = '';
  }

  function open(path) {
    const name = fileName(path);
    const { icon, inline } = typeOf(path);
    titleEl.textContent = icon + ' ' + name;
    clear();

    if (inline === 'img') {
      const img = document.createElement('img');
      img.src = fileUrl(path);
      img.alt = name;
      img.className = 'file-viewer__img';
      body.appendChild(img);

    } else if (inline === 'video') {
      const vid = document.createElement('video');
      vid.src = fileUrl(path);
      vid.controls = true;
      vid.className = 'file-viewer__video';
      body.appendChild(vid);

    } else if (inline === 'audio') {
      const aud = document.createElement('audio');
      aud.src = fileUrl(path);
      aud.controls = true;
      aud.className = 'file-viewer__audio';
      body.appendChild(aud);

    } else if (inline === 'iframe' || inline === 'text') {
      const fr = document.createElement('iframe');
      fr.src = previewUrl(path);
      fr.className = 'file-viewer__iframe';
      fr.setAttribute('sandbox', 'allow-scripts allow-same-origin');
      body.appendChild(fr);

    } else if (inline === 'office') {
      const loading = document.createElement('div');
      loading.className = 'file-viewer__loading';
      loading.textContent = '检查文件…';
      body.appendChild(loading);

      const url = fileUrl(path);
      headCheck(url).then(ok => {
        loading.remove();
        if (ok) {
          const link = document.createElement('a');
          link.href = url;
          link.target = '_blank';
          link.rel = 'noopener';
          link.textContent = '在新窗口打开';
          link.className = 'file-viewer__open-link';
          body.appendChild(link);
        } else {
          const msg = document.createElement('div');
          msg.className = 'file-viewer__unavail';
          msg.textContent = '文件不可访问';
          body.appendChild(msg);
        }
      });

    } else {
      const dl = document.createElement('a');
      dl.href = fileUrl(path);
      dl.download = name;
      dl.className = 'file-viewer__dl-link';
      dl.textContent = '下载 ' + name;
      body.appendChild(dl);
    }

    el.style.display = '';
  }

  function close() {
    el.style.display = 'none';
    clear();
  }

  document.addEventListener('file-view-request', (e) => {
    if (e.detail && e.detail.path) open(e.detail.path);
  });

  return { el, open, close };
}
