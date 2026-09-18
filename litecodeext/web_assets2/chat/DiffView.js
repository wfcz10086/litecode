import { createCollapsible } from '../ui/Collapsible.js';

/**
 * createDiffView(diffData) — 渲染文件 diff
 * diffData: { filepath: string, diff: string }  (SSE diff_view 契约字段)
 * 或        字符串 (纯 unified diff 文本)
 * 返回 .diff-block details 元素
 */
export function createDiffView(diffData) {
  let filepath = '';
  let diffStr = '';

  if (typeof diffData === 'string') {
    diffStr = diffData;
  } else {
    filepath = diffData.filepath || '';
    diffStr = diffData.diff || '';
  }

  if (!diffStr) return document.createDocumentFragment();

  const fname = filepath ? filepath.split('/').pop() : 'diff';
  const adds = (diffStr.match(/^\+[^+]/mg) || []).length;
  const dels = (diffStr.match(/^-[^-]/mg) || []).length;

  const summary = _buildSummary(fname, adds, dels);
  const pre = _buildColoredPre(diffStr);

  return createCollapsible({
    summary,
    content: pre,
    open: false,
    cls: 'diff-block',
  });
}

function _buildSummary(fname, adds, dels) {
  const span = document.createElement('span');
  span.className = 'diff-summary';
  span.innerHTML =
    `<span class="diff-fname">${_esc(fname)}</span>` +
    `<span class="diff-add">+${adds}</span>` +
    `<span class="diff-del">-${dels}</span>`;
  return span;
}

function _buildColoredPre(diffStr) {
  const pre = document.createElement('pre');
  pre.className = 'diff-pre';
  const lines = diffStr.split('\n');
  lines.forEach(line => {
    const span = document.createElement('span');
    if (line.startsWith('+') && !line.startsWith('+++')) {
      span.className = 'diff-line-add';
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      span.className = 'diff-line-del';
    } else if (line.startsWith('@@')) {
      span.className = 'diff-line-hunk';
    } else {
      span.className = 'diff-line-ctx';
    }
    span.textContent = line + '\n';
    pre.appendChild(span);
  });
  return pre;
}

function _esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
