// InputBar.js — textarea + 上传按钮 + 发送按钮 (含流式期间切换"停止").

import { effect } from '../../store/signal.js';
import { streaming } from '../../store/stream.js';

const TEXTAREA_CSS = [
  'flex:1', 'resize:none', 'border:1px solid #d9d9d9', 'border-radius:10px',
  'padding:9px 12px', 'font-size:14px', 'line-height:1.55', 'outline:none',
  'font-family:inherit', 'overflow-y:hidden', 'background:#fafafa',
].join(';');

const UPLOAD_BTN_CSS = 'flex-shrink:0;border:1px solid #e0e0e0;background:#fff;font-size:16px;'
  + 'cursor:pointer;padding:6px 10px;border-radius:8px;line-height:1';

const SEND_BTN_CSS = [
  'flex-shrink:0', 'padding:9px 18px', 'border:none', 'border-radius:10px',
  'cursor:pointer', 'font-size:14px', 'background:#111', 'color:#fff',
  'transition:background 0.15s',
].join(';');

export function createInputBar({ onSend, onInterrupt, onFilesSelected }) {
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;align-items:flex-end;gap:8px;';

  const { uploadBtn, fileInput } = _makeUpload(onFilesSelected);
  const textarea = _makeTextarea();
  const sendBtn = _makeSendBtn();

  bar.appendChild(uploadBtn);
  bar.appendChild(fileInput);
  bar.appendChild(textarea);
  bar.appendChild(sendBtn);

  const autoResize = () => _autoResize(textarea);
  const refreshSend = () => _refreshSendBtn(sendBtn, textarea, streaming.value);

  effect(() => { _refreshSendBtn(sendBtn, textarea, streaming.value); });

  textarea.addEventListener('input', () => { autoResize(); refreshSend(); });
  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (streaming.value) return;
      _doSend(textarea, autoResize, refreshSend, onSend);
    }
  });

  sendBtn.addEventListener('click', () => {
    if (streaming.value) onInterrupt();
    else _doSend(textarea, autoResize, refreshSend, onSend);
  });

  return { el: bar, focus: () => textarea.focus() };
}

function _makeUpload(onFilesSelected) {
  const uploadBtn = document.createElement('button');
  uploadBtn.type = 'button';
  uploadBtn.title = '上传文件';
  uploadBtn.textContent = '📎';
  uploadBtn.style.cssText = UPLOAD_BTN_CSS;

  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.multiple = true;
  fileInput.style.display = 'none';

  uploadBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files.length) {
      onFilesSelected(fileInput.files);
      fileInput.value = '';
    }
  });
  return { uploadBtn, fileInput };
}

function _makeTextarea() {
  const t = document.createElement('textarea');
  t.placeholder = '给 LiteCode 一个任务, Enter 发送, Shift+Enter 换行';
  t.rows = 1;
  t.style.cssText = TEXTAREA_CSS;
  return t;
}

function _makeSendBtn() {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = '发送';
  b.style.cssText = SEND_BTN_CSS;
  return b;
}

function _autoResize(t) {
  t.style.height = 'auto';
  const lineH = 22;
  const maxH = lineH * 8 + 18;
  const h = Math.min(t.scrollHeight, maxH);
  t.style.height = h + 'px';
  t.style.overflowY = t.scrollHeight > maxH ? 'auto' : 'hidden';
}

function _refreshSendBtn(btn, textarea, isStreaming) {
  if (isStreaming) {
    btn.textContent = '停止';
    btn.style.background = '#c92a2a';
    btn.disabled = false;
    return;
  }
  btn.textContent = '发送';
  const empty = !textarea.value.trim();
  btn.style.background = empty ? '#c0c0c0' : '#111';
  btn.disabled = empty;
}

function _doSend(textarea, autoResize, refreshSend, onSend) {
  const text = textarea.value.trim();
  if (!text || streaming.value) return;
  textarea.value = '';
  autoResize();
  refreshSend();
  onSend(text);
}
