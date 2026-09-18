// Answer.js — 正文 markdown 渲染 + 长文折叠 + copy 按钮.

import { renderMarkdown, highlightCode } from '../../utils/markdown.js';
import { createCollapsible } from '../../ui/Collapsible.js';
import { addCopyButtons } from './copy.js';

const LONG_MSG_THRESHOLD = 4000;

export function createAnswer(text) {
  if (!text) return null;
  if (text.length > LONG_MSG_THRESHOLD) return _longAnswer(text);
  return _shortAnswer(text);
}

function _shortAnswer(text) {
  const md = document.createElement('div');
  md.className = 'md-content';
  md.innerHTML = renderMarkdown(text);
  highlightCode(md);
  addCopyButtons(md);
  return md;
}

function _longAnswer(text) {
  const summary = `&#x1F4DC; 长文消息 ${text.length} 字符`;
  const contentEl = document.createElement('div');
  contentEl.className = 'md-content';
  const col = createCollapsible({
    summary,
    content: contentEl,
    open: false,
    cls: 'long-msg-block',
  });
  col.addEventListener('toggle', () => {
    if (col.open && !contentEl._rendered) {
      contentEl._rendered = true;
      contentEl.innerHTML = renderMarkdown(text);
      highlightCode(contentEl);
      addCopyButtons(contentEl);
    }
  });
  return col;
}
