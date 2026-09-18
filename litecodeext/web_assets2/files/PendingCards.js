import { pendFiles, removeFile, uploadFile } from '../store/upload.js';
import { effect } from '../store/signal.js';

const STATUS_LABEL = { queued: '等待', uploading: '上传中…', done: '完成', error: '失败' };
const STATUS_CLS   = { queued: 'pcard--queued', uploading: 'pcard--uploading', done: 'pcard--done', error: 'pcard--error' };

function shortPath(p) {
  if (!p || p.length <= 36) return p || '';
  return '…' + p.slice(-33);
}

function buildCard(entry, container) {
  const card = document.createElement('div');
  card.className = 'pcard ' + (STATUS_CLS[entry.status] || '');
  card.dataset.fileId = entry.id;

  const nameEl = document.createElement('span');
  nameEl.className = 'pcard__name';
  nameEl.textContent = entry.name;

  const statusEl = document.createElement('span');
  statusEl.className = 'pcard__status';
  statusEl.textContent = STATUS_LABEL[entry.status] || entry.status;

  card.appendChild(nameEl);
  card.appendChild(statusEl);

  if (entry.status === 'done' && entry.path) {
    const pathEl = document.createElement('span');
    pathEl.className = 'pcard__path';
    pathEl.textContent = shortPath(entry.path);
    card.appendChild(pathEl);
  }

  if (entry.status === 'error') {
    if (entry.error) {
      const errEl = document.createElement('span');
      errEl.className = 'pcard__err';
      errEl.textContent = entry.error;
      card.appendChild(errEl);
    }

    const retryBtn = document.createElement('button');
    retryBtn.className = 'pcard__retry';
    retryBtn.textContent = '重试';
    retryBtn.addEventListener('click', () => {
      const current = pendFiles.value.find(f => f.id === entry.id);
      if (!current || !current.file) return;
      removeFile(entry.id);
      uploadFile(current.file);
    });
    card.appendChild(retryBtn);
  }

  const closeBtn = document.createElement('button');
  closeBtn.className = 'pcard__close';
  closeBtn.textContent = '×';
  closeBtn.addEventListener('click', () => removeFile(entry.id));
  card.appendChild(closeBtn);

  return card;
}

export function createPendingCards() {
  const container = document.createElement('div');
  container.className = 'pending-cards';

  effect(() => {
    const files = pendFiles.value;
    container.innerHTML = '';
    for (const entry of files) {
      container.appendChild(buildCard(entry, container));
    }
    container.style.display = files.length ? '' : 'none';
  });

  return container;
}
