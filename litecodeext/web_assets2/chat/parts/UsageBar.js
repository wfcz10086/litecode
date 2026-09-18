// UsageBar.js — 底部 Token / 轮次 / 耗时统计条.

export function createUsageBar(usage) {
  const ub = document.createElement('div');
  ub.className = 'ub';
  const f = n => (n || 0).toLocaleString();
  ub.innerHTML =
    `<span>P:<b>${f(usage.prompt_tokens)}</b></span>` +
    `<span>C:<b>${f(usage.completion_tokens)}</b></span>` +
    `<span>T:<b>${f(usage.total_tokens)}</b></span>` +
    (usage.iterations > 1 ? `<span>${usage.iterations}轮</span>` : '') +
    (usage.elapsed_seconds ? `<span>${usage.elapsed_seconds}s</span>` : '');
  return ub;
}
