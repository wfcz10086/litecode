// ChatInput.js — 主编排. 组合 ChipRow + InputBar. 无业务逻辑, 只做布局.

import { createChipRow } from './chips/ChipRow.js';
import { createInputBar } from './input/InputBar.js';

const WRAP_CSS = [
  'display:flex', 'flex-direction:column', 'gap:6px',
  'padding:10px 14px 14px', 'border-top:1px solid #ececec',
  'background:#fff',
].join(';');

export function createChatInput({ onSend, onInterrupt, onFilesSelected }) {
  const wrap = document.createElement('div');
  wrap.className = 'chat-input-wrap';
  wrap.style.cssText = WRAP_CSS;

  wrap.appendChild(createChipRow());

  const bar = createInputBar({ onSend, onInterrupt, onFilesSelected });
  wrap.appendChild(bar.el);

  return { el: wrap, focus: bar.focus };
}
