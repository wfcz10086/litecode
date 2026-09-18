// ChipRow.js — 芯片行容器 (模型 / 思考 / DeepSearch).

import { createModelChip } from './ModelChip.js';
import { createThinkChip } from './ThinkChip.js';
import { createDeepChip } from './DeepChip.js';

export function createChipRow() {
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap;';
  row.appendChild(createModelChip());
  row.appendChild(createThinkChip());
  row.appendChild(createDeepChip());
  return row;
}
