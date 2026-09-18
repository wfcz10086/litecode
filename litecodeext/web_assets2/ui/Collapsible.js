/**
 * createCollapsible — 通用折叠组件
 * @param {object} opts
 * @param {string|HTMLElement} opts.summary  — 标题文本或元素
 * @param {string|HTMLElement} opts.content  — 内容文本或元素
 * @param {boolean} [opts.open=false]        — 默认展开状态
 * @param {string}  [opts.cls='']            — 额外 class
 * @returns {HTMLDetailsElement}
 */
export function createCollapsible({ summary, content, open = false, cls = '' }) {
  const details = document.createElement('details');
  if (open) details.open = true;
  if (cls) details.className = cls;

  const summaryEl = document.createElement('summary');
  if (typeof summary === 'string') {
    summaryEl.textContent = summary;
  } else {
    summaryEl.appendChild(summary);
  }
  details.appendChild(summaryEl);

  const bodyEl = document.createElement('div');
  bodyEl.className = 'collapsible-body';
  if (typeof content === 'string') {
    bodyEl.textContent = content;
  } else if (content instanceof HTMLElement) {
    bodyEl.appendChild(content);
  }
  details.appendChild(bodyEl);

  return details;
}
