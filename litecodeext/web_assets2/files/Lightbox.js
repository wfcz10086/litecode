export function createLightbox(images) {
  let idx = 0;
  let active = false;

  const mask = document.createElement('div');
  mask.className = 'lightbox';
  mask.style.cssText = [
    'display:none;position:fixed;inset:0;z-index:9999;',
    'background:rgba(0,0,0,0.88);align-items:center;justify-content:center;',
  ].join('');

  const img = document.createElement('img');
  img.className = 'lightbox__img';
  img.style.cssText = 'max-width:90vw;max-height:90vh;object-fit:contain;border-radius:4px;';

  const counter = document.createElement('div');
  counter.className = 'lightbox__counter';
  counter.style.cssText = 'position:absolute;top:16px;left:50%;transform:translateX(-50%);color:#fff;font-size:.9rem;opacity:.7;';

  const btnPrev = document.createElement('button');
  btnPrev.className = 'lightbox__prev';
  btnPrev.textContent = '‹';
  btnPrev.style.cssText = 'position:absolute;left:20px;top:50%;transform:translateY(-50%);font-size:2rem;color:#fff;background:none;border:none;cursor:pointer;opacity:.7;';

  const btnNext = document.createElement('button');
  btnNext.className = 'lightbox__next';
  btnNext.textContent = '›';
  btnNext.style.cssText = 'position:absolute;right:20px;top:50%;transform:translateY(-50%);font-size:2rem;color:#fff;background:none;border:none;cursor:pointer;opacity:.7;';

  mask.appendChild(counter);
  mask.appendChild(btnPrev);
  mask.appendChild(img);
  mask.appendChild(btnNext);
  document.body.appendChild(mask);

  function show() {
    if (!images || !images.length) return;
    const item = images[Math.max(0, Math.min(idx, images.length - 1))];
    img.src = item.src;
    img.alt = item.alt || '';
    counter.textContent = images.length > 1 ? `${idx + 1} / ${images.length}` : '';
    btnPrev.style.display = images.length > 1 ? '' : 'none';
    btnNext.style.display = images.length > 1 ? '' : 'none';
  }

  function open(startIdx) {
    idx = startIdx || 0;
    active = true;
    mask.style.display = 'flex';
    show();
  }

  function close() {
    active = false;
    mask.style.display = 'none';
    img.src = '';
  }

  btnPrev.addEventListener('click', (e) => {
    e.stopPropagation();
    idx = (idx - 1 + images.length) % images.length;
    show();
  });

  btnNext.addEventListener('click', (e) => {
    e.stopPropagation();
    idx = (idx + 1) % images.length;
    show();
  });

  mask.addEventListener('click', (e) => {
    if (e.target === mask) close();
  });

  document.addEventListener('keydown', (e) => {
    if (!active) return;
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowLeft') { idx = (idx - 1 + images.length) % images.length; show(); }
    if (e.key === 'ArrowRight') { idx = (idx + 1) % images.length; show(); }
  });

  return { open, close };
}
