// ThinkChip.js — 思考强度芯片 (off/low/medium/high).

import { effect } from '../../store/signal.js';
import { thinkingEffort } from '../../store/prefs.js';
import { chipCss, MENU_CSS, MENU_ITEM_CSS, attachDropdown } from './_style.js';

const LABEL = { off: '思考: 关', low: '思考: 低', medium: '思考: 中', high: '思考: 高' };
const ORDER = ['off', 'low', 'medium', 'high'];

export function createThinkChip() {
  const chip = document.createElement('div');
  chip.style.position = 'relative';

  const label = document.createElement('span');
  chip.appendChild(label);

  const arrow = document.createElement('span');
  arrow.textContent = ' ▾';
  arrow.style.opacity = '0.6';
  chip.appendChild(arrow);

  const menu = document.createElement('div');
  menu.style.cssText = MENU_CSS + ';min-width:140px';
  chip.appendChild(menu);

  for (const eff of ORDER) {
    const it = document.createElement('div');
    it.textContent = LABEL[eff];
    it.style.cssText = MENU_ITEM_CSS;
    it.addEventListener('mouseenter', () => it.style.background = '#f0f0f0');
    it.addEventListener('mouseleave', () => it.style.background = 'transparent');
    it.addEventListener('click', (e) => {
      e.stopPropagation();
      thinkingEffort.value = eff;
      menu.style.display = 'none';
    });
    menu.appendChild(it);
  }

  attachDropdown(chip, menu);

  effect(() => {
    const eff = thinkingEffort.value;
    chip.style.cssText = chipCss(eff !== 'off') + ';position:relative';
    label.textContent = LABEL[eff] || `思考: ${eff}`;
  });
  return chip;
}
