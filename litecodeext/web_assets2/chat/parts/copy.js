// copy.js — 给 <pre> 附加 "copy" 按钮.

export function addCopyButtons(container) {
  container.querySelectorAll('pre').forEach(pre => {
    if (pre.querySelector('.cpb')) return;
    const btn = document.createElement('button');
    btn.className = 'cpb';
    btn.textContent = 'copy';
    pre.style.position = 'relative';
    pre.appendChild(btn);
    btn.addEventListener('click', () => {
      navigator.clipboard.writeText(pre.querySelector('code')?.textContent || '');
      btn.textContent = '✓';
      setTimeout(() => { btn.textContent = 'copy'; }, 1500);
    });
  });
}
