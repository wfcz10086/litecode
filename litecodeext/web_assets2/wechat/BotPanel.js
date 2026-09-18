import { fetchWechatQR } from '../store/config.js';

const QR_TTL = 60;

export function createBotPanel() {
  let timer = null;
  let remaining = QR_TTL;

  const el = document.createElement('div');
  el.style.cssText = 'display:flex;flex-direction:column;align-items:center;gap:16px;padding:20px 0';

  const qrWrap = document.createElement('div');
  qrWrap.style.cssText = 'display:flex;flex-direction:column;align-items:center;gap:12px';

  const img = document.createElement('img');
  img.style.cssText = 'width:200px;height:200px;border:1px solid #e0e0e0;border-radius:4px;display:none';
  img.alt = '微信登录二维码';

  const errorEl = document.createElement('p');
  errorEl.style.cssText = 'color:#d32f2f;font-size:13px;text-align:center;display:none;margin:0';

  const expiredEl = document.createElement('div');
  expiredEl.style.cssText = 'display:none;flex-direction:column;align-items:center;gap:10px';

  const expiredMsg = document.createElement('p');
  expiredMsg.style.cssText = 'color:#888;font-size:13px;margin:0';
  expiredMsg.textContent = '二维码已过期';

  const refreshBtn = document.createElement('button');
  refreshBtn.type = 'button';
  refreshBtn.className = 'btn btn-primary';
  refreshBtn.textContent = '重新生成';
  refreshBtn.style.cssText = 'padding:6px 18px;border:none;border-radius:4px;background:#1976d2;color:#fff;font-size:13px;cursor:pointer';

  expiredEl.appendChild(expiredMsg);
  expiredEl.appendChild(refreshBtn);

  const countdownEl = document.createElement('div');
  countdownEl.style.cssText = 'font-size:12px;color:#888';

  qrWrap.appendChild(img);
  qrWrap.appendChild(expiredEl);
  qrWrap.appendChild(errorEl);
  qrWrap.appendChild(countdownEl);
  el.appendChild(qrWrap);

  function stopTimer() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  function showExpired() {
    stopTimer();
    img.style.display = 'none';
    countdownEl.style.display = 'none';
    expiredEl.style.display = 'flex';
  }

  function startCountdown() {
    remaining = QR_TTL;
    countdownEl.textContent = `二维码有效期: ${remaining}s`;
    countdownEl.style.display = 'block';
    expiredEl.style.display = 'none';
    stopTimer();
    timer = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        showExpired();
      } else {
        countdownEl.textContent = `二维码有效期: ${remaining}s`;
      }
    }, 1000);
  }

  async function loadQR() {
    errorEl.style.display = 'none';
    expiredEl.style.display = 'none';
    img.style.display = 'none';
    countdownEl.textContent = '加载中…';
    countdownEl.style.display = 'block';
    stopTimer();

    try {
      const data = await fetchWechatQR();
      if (data && data.qr) {
        img.src = data.qr.startsWith('data:') ? data.qr : `data:image/png;base64,${data.qr}`;
        img.style.display = 'block';
        startCountdown();
      } else {
        throw new Error('返回数据缺少 qr 字段');
      }
    } catch (err) {
      countdownEl.style.display = 'none';
      errorEl.textContent = `获取二维码失败: ${err.message}`;
      errorEl.style.display = 'block';
    }
  }

  refreshBtn.addEventListener('click', loadQR);

  const observer = new MutationObserver(mutations => {
    for (const m of mutations) {
      for (const node of m.removedNodes) {
        if (node === el || node.contains(el)) { destroy(); }
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });

  function destroy() {
    stopTimer();
    observer.disconnect();
  }

  function refresh() {
    loadQR();
  }

  loadQR();

  el._destroy = destroy;

  return { el, refresh, destroy };
}
