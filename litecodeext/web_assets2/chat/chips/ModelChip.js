// ModelChip.js — 模型选择芯片 (下拉列出 availableModels).

import { effect } from '../../store/signal.js';
import { currentModel, availableModels } from '../../store/prefs.js';
import { chipCss, MENU_CSS } from './_style.js';

export function createModelChip() {
  const chip = document.createElement('div');
  chip.style.cssText = chipCss(false) + ';position:relative';

  const label = document.createElement('span');
  label.textContent = '模型';
  chip.appendChild(label);

  const arrow = document.createElement('span');
  arrow.textContent = ' ▾';
  arrow.style.opacity = '0.6';
  chip.appendChild(arrow);

  const menu = document.createElement('div');
  menu.style.cssText = MENU_CSS + ';min-width:220px';
  chip.appendChild(menu);

  chip.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.style.display === 'none' || !menu.style.display) {
      _rebuildMenu(menu);
      menu.style.display = 'block';
    } else {
      menu.style.display = 'none';
    }
  });
  document.addEventListener('click', () => { menu.style.display = 'none'; });

  effect(() => {
    const mid = currentModel.value;
    const m = availableModels.value.find(x => x.id === mid);
    const short = m ? _shortLabel(m.label || m.id) : mid;
    label.textContent = `模型: ${short}`;
  });

  return chip;
}

function _rebuildMenu(menu) {
  menu.innerHTML = '';
  const list = availableModels.value;
  if (!list.length) {
    const empty = document.createElement('div');
    empty.textContent = '(无模型, 请检查 /api/config)';
    empty.style.cssText = 'padding:6px 8px;font-size:12px;color:#999';
    menu.appendChild(empty);
    return;
  }
  for (const m of list) menu.appendChild(_menuItem(m, menu));
}

function _menuItem(m, menu) {
  const it = document.createElement('div');
  const isSel = m.id === currentModel.value;
  it.style.cssText = [
    'padding:6px 10px', 'border-radius:6px', 'cursor:pointer',
    'display:flex', 'flex-direction:column', 'gap:2px',
    'background:' + (isSel ? '#f4f4f4' : 'transparent'),
  ].join(';');

  const line1 = document.createElement('div');
  line1.textContent = m.label || m.id;
  line1.style.cssText = 'font-size:13px;color:#111';

  const line2 = document.createElement('div');
  line2.textContent = _subLine(m);
  line2.style.cssText = 'font-size:11px;color:#888';

  it.appendChild(line1);
  it.appendChild(line2);

  it.addEventListener('mouseenter', () => it.style.background = '#f0f0f0');
  it.addEventListener('mouseleave', () => {
    it.style.background = m.id === currentModel.value ? '#f4f4f4' : 'transparent';
  });
  it.addEventListener('click', (e) => {
    e.stopPropagation();
    currentModel.value = m.id;
    menu.style.display = 'none';
  });
  return it;
}

function _subLine(m) {
  const bits = [m.provider || '?', m.deployment || '?'];
  if (m.supports_vision) bits.push('视觉');
  if (m.thinking) bits.push('思考');
  return bits.join(' · ');
}

function _shortLabel(s) {
  s = s.replace(/^\[#\d+\]\s*/, '');
  return s.length > 22 ? s.slice(0, 20) + '…' : s;
}
