import { signal } from './signal.js';

// 每个 entry: { id, file, name, size, path, status: 'queued'|'uploading'|'done'|'error', error }
export const pendFiles = signal([]);

export function addFile(entry) { pendFiles.value = [...pendFiles.value, entry]; }
export function updateFile(id, patch) {
  pendFiles.value = pendFiles.value.map(f => f.id === id ? {...f, ...patch} : f);
}
export function removeFile(id) { pendFiles.value = pendFiles.value.filter(f => f.id !== id); }
export function clearFiles() { pendFiles.value = []; }

export async function uploadFiles(fileList) {
  return Promise.allSettled(fileList.map(file => uploadFile(file)));
}

export async function uploadFile(file) {
  const id = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
  addFile({ id, name: file.name, status: 'uploading', path: null, file });
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    updateFile(id, { status: 'done', path: data.path });
    return { id, path: data.path };
  } catch (e) {
    updateFile(id, { status: 'error', error: e.message });
    throw e;
  }
}
