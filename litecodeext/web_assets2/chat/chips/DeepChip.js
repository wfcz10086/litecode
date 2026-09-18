// DeepChip.js — DeepSearch 开关 (布尔切换).

import { effect } from '../../store/signal.js';
import { deepSearch } from '../../store/prefs.js';
import { chipCss } from './_style.js';

export function createDeepChip() {
  const chip = document.createElement('div');
  const label = document.createElement('span');
  label.textContent = '🔎 DeepSearch';
  chip.appendChild(label);
  chip.addEventListener('click', () => { deepSearch.value = !deepSearch.value; });
  effect(() => { chip.style.cssText = chipCss(deepSearch.value); });
  return chip;
}
