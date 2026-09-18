export function confirmCard({ title, message, confirmText = '确认', cancelText = '取消' }) {
  return new Promise(resolve => {
    if (document.querySelector('.confirm-overlay,.form-overlay')) { resolve(false); return; }
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:9000';

    const card = document.createElement('div');
    card.className = 'confirm-card';
    card.style.cssText = 'background:#fff;border-radius:8px;padding:24px;min-width:320px;max-width:480px;box-shadow:0 8px 32px rgba(0,0,0,.18)';

    const h = document.createElement('h3');
    h.style.cssText = 'margin:0 0 12px;font-size:16px';
    h.textContent = title;

    const p = document.createElement('p');
    p.style.cssText = 'margin:0 0 24px;color:#555;font-size:14px;line-height:1.5';
    p.textContent = message;

    const btns = document.createElement('div');
    btns.style.cssText = 'display:flex;gap:8px;justify-content:flex-end';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.textContent = cancelText;

    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn btn-primary';
    confirmBtn.textContent = confirmText;

    btns.appendChild(cancelBtn);
    btns.appendChild(confirmBtn);
    card.appendChild(h);
    card.appendChild(p);
    card.appendChild(btns);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    function finish(result) {
      document.body.removeChild(overlay);
      document.removeEventListener('keydown', onKey);
      resolve(result);
    }

    function onKey(e) {
      if (e.key === 'Escape') finish(false);
      if (e.key === 'Enter') finish(true);
    }

    overlay.addEventListener('click', e => { if (e.target === overlay) finish(false); });
    cancelBtn.addEventListener('click', () => finish(false));
    confirmBtn.addEventListener('click', () => finish(true));
    document.addEventListener('keydown', onKey);

    confirmBtn.focus();
  });
}
