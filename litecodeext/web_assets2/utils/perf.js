export const imgLazyObserver = new IntersectionObserver(entries => {
  for (const e of entries) {
    if (e.isIntersecting) {
      const img = e.target;
      img.src = img.dataset.src;
      imgLazyObserver.unobserve(img);
    }
  }
}, { rootMargin: '200px' });

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}
