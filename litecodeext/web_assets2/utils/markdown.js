export function renderMarkdown(text) {
  if (!text) return '';
  if (typeof window === 'undefined' || !window.marked) {
    return `<pre>${_esc(text)}</pre>`;
  }
  try {
    const html = window.marked.parse(text);
    return _addCopyButtons(html);
  } catch (e) {
    return `<pre>${_esc(text)}</pre>`;
  }
}

export function highlightCode(el) {
  if (typeof window !== 'undefined' && window.hljs) {
    el.querySelectorAll('pre code').forEach(block => {
      try { window.hljs.highlightElement(block); } catch (_) {}
    });
  }
}

function _esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function _addCopyButtons(html) {
  // copy buttons injected after DOM insert via highlightCode path
  return html;
}
