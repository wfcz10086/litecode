// prefs.js — 会话内偏好: 模型选择 / 思考强度 / DeepSearch
// 落地策略: localStorage, 立刻回放, 后端 stateless.

import { signal } from './signal.js';

const LS_KEY = 'litecode.prefs.v1';

function _load() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return {};
    return JSON.parse(raw) || {};
  } catch { return {}; }
}
function _save(o) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(o)); } catch {}
}

const _initial = _load();

// 默认模型: 兜底 Qwen3.6-35B (config 里同款). 之后会被 /api/config 里的 default_model_id 覆盖.
export const currentModel = signal(_initial.model || 'Qwen3.6-35B');
export const thinkingEffort = signal(_initial.thinking || 'medium');  // off | low | medium | high
export const deepSearch = signal(!!_initial.deepSearch);
export const availableModels = signal([]);  // [{id, label, provider, deployment, supports_vision}]

function _persist() {
  _save({
    model: currentModel.value,
    thinking: thinkingEffort.value,
    deepSearch: deepSearch.value,
  });
}
currentModel.subscribe(_persist);
thinkingEffort.subscribe(_persist);
deepSearch.subscribe(_persist);

export async function loadModels() {
  try {
    const r = await fetch('/api/config');
    if (!r.ok) return;
    const j = await r.json();
    const cfg = j.config || j;
    const models = (cfg.models || []).map(m => ({
      id: m.id,
      label: m.label || m.id,
      provider: m.provider || '',
      deployment: m.deployment || '',
      supports_vision: !!m.supports_vision,
      thinking: !!m.enable_thinking,
    }));
    availableModels.value = models;
    const def = cfg.default_model_id;
    if (def && !_initial.model) currentModel.value = def;
    if (models.length && !models.some(m => m.id === currentModel.value)) {
      currentModel.value = def || models[0].id;
    }
  } catch (e) { /* silent — UI 仍可用默认 */ }
}

export function buildChatExtras() {
  return {
    model: currentModel.value,
    thinking_effort: thinkingEffort.value,
    deep_search: deepSearch.value,
  };
}
