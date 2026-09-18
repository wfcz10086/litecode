import { uploadFile } from '../store/upload.js';

function uploadBatch(files) {
  return Promise.allSettled(files.map(f => uploadFile(f)));
}

export function createUploadZone(onFiles) {
  const zone = document.createElement('div');
  zone.className = 'upload-zone';
  zone.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:900;';

  const overlay = document.createElement('div');
  overlay.className = 'upload-zone__overlay';
  overlay.style.cssText = [
    'position:absolute;inset:0;display:none;align-items:center;justify-content:center;',
    'background:rgba(0,120,255,0.12);border:3px dashed #0078ff;border-radius:8px;',
    'font-size:1.4rem;color:#0078ff;pointer-events:none;',
  ].join('');
  overlay.textContent = '松开以上传文件';
  zone.appendChild(overlay);

  let dragCounter = 0;

  function showOverlay() {
    overlay.style.display = 'flex';
    zone.style.pointerEvents = 'all';
  }
  function hideOverlay() {
    overlay.style.display = 'none';
    zone.style.pointerEvents = 'none';
  }

  function handleFiles(files) {
    if (!files || !files.length) return;
    const arr = Array.from(files);
    uploadBatch(arr);
    if (onFiles) onFiles(arr);
  }

  document.addEventListener('dragenter', (e) => {
    if (!e.dataTransfer || !e.dataTransfer.types.includes('Files')) return;
    dragCounter++;
    showOverlay();
  });

  document.addEventListener('dragleave', (e) => {
    dragCounter--;
    if (dragCounter <= 0) {
      dragCounter = 0;
      hideOverlay();
    }
  });

  document.addEventListener('dragover', (e) => {
    if (!e.dataTransfer || !e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });

  document.addEventListener('drop', (e) => {
    dragCounter = 0;
    hideOverlay();
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    handleFiles(e.dataTransfer.files);
  });

  document.addEventListener('paste', (e) => {
    if (!e.clipboardData || !e.clipboardData.items) return;
    const imageFiles = [];
    for (const item of e.clipboardData.items) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        const f = item.getAsFile();
        if (f) imageFiles.push(f);
      }
    }
    if (imageFiles.length) handleFiles(imageFiles);
  });

  return zone;
}
