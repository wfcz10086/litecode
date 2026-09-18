import { truncateAndResend } from '../store/session.js';

export function createMessageActions(msgEl, sessionId, msgIdx) {
  const bar = document.createElement('div');
  bar.className = 'msg-actions';

  const copyBtn = _btn('复制');
  const editBtn = _btn('编辑');
  const retryBtn = _btn('重试');

  bar.appendChild(copyBtn);
  bar.appendChild(editBtn);
  bar.appendChild(retryBtn);

  copyBtn.addEventListener('click', () => {
    const text = msgEl.dataset.rawContent || msgEl.textContent || '';
    navigator.clipboard.writeText(text).catch(() => {});
  });

  editBtn.addEventListener('click', () => {
    const originalHTML = msgEl.innerHTML;
    const originalText = msgEl.dataset.rawContent || msgEl.textContent || '';

    const ta = document.createElement('textarea');
    ta.className = 'msg-edit-textarea';
    ta.value = originalText;
    ta.rows = Math.max(3, originalText.split('\n').length);

    const confirmBtn = _btn('确认');
    const cancelBtn = _btn('取消');
    const actRow = document.createElement('div');
    actRow.className = 'msg-edit-actions';
    actRow.appendChild(confirmBtn);
    actRow.appendChild(cancelBtn);

    msgEl.innerHTML = '';
    msgEl.appendChild(ta);
    msgEl.appendChild(actRow);
    ta.focus();

    confirmBtn.addEventListener('click', () => {
      const newText = ta.value.trim();
      if (!newText) return;
      msgEl.innerHTML = originalHTML;
      truncateAndResend(sessionId, msgIdx, newText);
    });

    cancelBtn.addEventListener('click', () => {
      msgEl.innerHTML = originalHTML;
    });
  });

  retryBtn.addEventListener('click', () => {
    truncateAndResend(sessionId, msgIdx, null);
  });

  return bar;
}

function _btn(label) {
  const b = document.createElement('button');
  b.className = 'msg-action-btn';
  b.textContent = label;
  return b;
}
