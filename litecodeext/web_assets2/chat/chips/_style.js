// _style.js — 芯片共享样式常量.

export function chipCss(active) {
  return [
    'display:inline-flex', 'align-items:center', 'gap:4px',
    'padding:4px 10px', 'font-size:12px', 'cursor:pointer',
    'border:1px solid ' + (active ? '#111' : '#e0e0e0'),
    'background:' + (active ? '#111' : '#fff'),
    'color:' + (active ? '#fff' : '#333'),
    'border-radius:14px', 'user-select:none',
    'transition:all 0.15s',
  ].join(';');
}

export const MENU_CSS = [
  'position:absolute', 'bottom:100%', 'left:0', 'margin-bottom:4px',
  'background:#fff', 'border:1px solid #e0e0e0', 'border-radius:8px',
  'box-shadow:0 4px 16px rgba(0,0,0,0.08)', 'padding:4px',
  'z-index:1000', 'display:none',
].join(';');

export const MENU_ITEM_CSS = 'padding:6px 10px;border-radius:6px;cursor:pointer;font-size:13px;';

export function attachDropdown(chip, menu) {
  chip.addEventListener('click', (e) => {
    e.stopPropagation();
    menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
  });
  document.addEventListener('click', () => { menu.style.display = 'none'; });
}
