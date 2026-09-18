import { streaming, sending, setCurReader } from '../store/stream.js';

// 事件总线 — 订阅者格式: handlers[deltaKey] = (value, delta) => void
const _handlers = {};
export function onDelta(key, fn) {
  (_handlers[key] = _handlers[key] || []).push(fn);
}
export function offDelta(key, fn) {
  if (_handlers[key]) _handlers[key] = _handlers[key].filter(h => h !== fn);
}

function _dispatch(delta) {
  const keys = ['content', 'reasoning', 'task_exec', 'data_collect', 'task_analysis',
                 'diff_view', 'agent_status', 'orchestrator_step', 'panel_refresh', 'usage'];
  for (const k of keys) {
    if (k in delta && _handlers[k]) {
      _handlers[k].forEach(fn => fn(delta[k], delta));
    }
  }
}

export async function streamChat(sessionId, message, extra = {}) {
  if (streaming.value || sending.value) return;
  sending.value = true;
  streaming.value = true;

  try {
    const resp = await fetch(`/api/chat/${sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, ...extra }),
    });

    if (!resp.ok) {
      if (resp.status === 401) { window.location.href = '/login'; return; }
      const err = await resp.text();
      _dispatch({ content: `[错误 HTTP ${resp.status}] ${err.slice(0, 200)}` });
      return;
    }

    const reader = resp.body.getReader();
    setCurReader(reader);
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') break;
        try {
          const chunk = JSON.parse(data);
          const delta = chunk?.choices?.[0]?.delta;
          if (delta) _dispatch(delta);
        } catch { /* 忽略 parse 错误 */ }
      }
    }
  } finally {
    streaming.value = false;
    sending.value = false;
    setCurReader(null);
  }
}
