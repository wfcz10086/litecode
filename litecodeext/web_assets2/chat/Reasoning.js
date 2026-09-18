import { createCollapsible } from '../ui/Collapsible.js';
import { renderMarkdown, highlightCode } from '../utils/markdown.js';

/**
 * createReasoning(text) — 推理折叠块 (Kimi 风格)
 *
 * summary 状态机:
 *   流式中:   "思考中… X 字"      (默认展开, 让用户看见过程)
 *   流式结束: "思考了 N 秒 · X 字" (自动折叠, 眼不见为净)
 */
export function createReasoning(text) {
  const isStreaming = !text;  // 无初始文本 → 流式起点
  const bodyEl = document.createElement('div');
  bodyEl.className = 'reasoning-content';

  if (text) {
    bodyEl.innerHTML = renderMarkdown(text);
    highlightCode(bodyEl);
  }

  const summary = _buildSummary(text ? text.length : 0, /*streaming=*/isStreaming, /*seconds=*/null);

  const details = createCollapsible({
    summary,
    content: bodyEl,
    open: isStreaming,   // 流式过程默认展开; 历史消息默认收起
    cls: 'reasoning-block',
  });

  details._bodyEl = bodyEl;
  details._text = text || '';
  details._t0 = Date.now();
  details._finished = !isStreaming;

  return details;
}

/**
 * appendReasoningText(el, delta) — 流式追加推理文本片段
 */
export function appendReasoningText(el, delta) {
  if (!el._bodyEl) return;
  el._text = (el._text || '') + delta;
  el._bodyEl.innerHTML = renderMarkdown(el._text);
  highlightCode(el._bodyEl);
  _updateSummary(el, el._text.length, /*streaming=*/true, /*seconds=*/null);
}

/**
 * finalizeReasoning(el) — 流式结束时调用. 显示"思考了 N 秒", 自动收起.
 */
export function finalizeReasoning(el) {
  if (!el || el._finished) return;
  el._finished = true;
  const seconds = Math.max(1, Math.round((Date.now() - (el._t0 || Date.now())) / 1000));
  _updateSummary(el, (el._text || '').length, /*streaming=*/false, seconds);
  if (el.open) el.open = false;  // Kimi: 思考完自动折叠
}

function _buildSummary(charCount, streaming, seconds) {
  const span = document.createElement('span');
  span.className = 'reasoning-summary';
  span.innerHTML = _renderSummaryHTML(charCount, streaming, seconds);
  return span;
}

function _updateSummary(el, charCount, streaming, seconds) {
  const s = el.querySelector('.reasoning-summary');
  if (s) s.innerHTML = _renderSummaryHTML(charCount, streaming, seconds);
}

function _renderSummaryHTML(charCount, streaming, seconds) {
  if (streaming) {
    return `<span class="reasoning-icon">&#x1F4AD;</span>`
      + `<span class="reasoning-label">思考中…</span>`
      + `<span class="reasoning-chars">${charCount} 字</span>`;
  }
  const secStr = seconds ? `${seconds} 秒 · ` : '';
  return `<span class="reasoning-icon">&#x1F4AD;</span>`
    + `<span class="reasoning-label">思考了 ${secStr}${charCount} 字</span>`;
}
