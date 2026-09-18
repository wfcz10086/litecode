import { signal } from './signal.js';
import { streamChat } from '../net/sse.js';

export async function createSession() {
  const r = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  if (!r.ok) return null;
  const data = await r.json();
  return data.session_id || data.sid || data.id || null;
}

export const cur = signal(null);
export const sessions = signal([]);

export function setCur(id) { cur.value = id; }

export async function loadSession(sid) {
  const r = await fetch(`/api/sessions/${sid}`);
  if (!r.ok) return null;
  const data = await r.json();
  return data.messages || data.msgs || [];
}

let _resending = false;

export async function truncateAndResend(sessionId, msgIdx, newContent) {
  if (_resending) return;
  _resending = true;
  try {
    const resp = await fetch(`/api/sessions/${sessionId}/truncate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep: msgIdx }),
    });
    if (!resp.ok) return;

    let textToSend = newContent;
    if (textToSend === null) {
      const sessResp = await fetch(`/api/sessions/${sessionId}`);
      if (!sessResp.ok) return;
      const sessData = await sessResp.json();
      const msgs = sessData.messages || sessData.msgs || [];
      const userMsgs = msgs.filter(m => m.role === 'user');
      textToSend = userMsgs.length ? userMsgs[userMsgs.length - 1].content : '';
    }

    if (textToSend) {
      await streamChat(sessionId, textToSend);
    }
  } finally {
    _resending = false;
  }
}
