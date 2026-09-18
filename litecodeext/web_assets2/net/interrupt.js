import { streaming, sending, curReader, setCurReader } from '../store/stream.js';

let _inFlight = false;

export async function interrupt(sessionId) {
  if (!sessionId || _inFlight) return;
  _inFlight = true;
  try {
    await fetch(`/api/interrupt/${sessionId}`, { method: 'POST' }).catch(() => {});
    if (curReader) { try { await curReader.cancel(); } catch {} setCurReader(null); }
    streaming.value = false;
    sending.value = false;
  } finally {
    _inFlight = false;
  }
}
