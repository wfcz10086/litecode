// ChatHeader.js — 顶栏: LiteCode 标识 + 会话名 + 齿轮.
// 极简、企业级、无花哨.

import { effect } from '../store/signal.js';
import { currentModel, availableModels } from '../store/prefs.js';

export function createChatHeader({ onOpenConfig, onNewSession }) {
  const bar = document.createElement('div');
  bar.className = 'app-header';
  bar.style.cssText = [
    'display:flex', 'align-items:center', 'gap:12px',
    'padding:10px 16px', 'border-bottom:1px solid #ececec',
    'background:#fff', 'flex-shrink:0',
  ].join(';');

  const logo = document.createElement('div');
  logo.textContent = 'LiteCode';
  logo.style.cssText = 'font-weight:600;font-size:15px;color:#111;letter-spacing:0.2px;';
  bar.appendChild(logo);

  const modelChip = document.createElement('div');
  modelChip.style.cssText = [
    'padding:3px 10px', 'border:1px solid #e0e0e0', 'border-radius:12px',
    'font-size:12px', 'color:#555', 'background:#fafafa',
  ].join(';');
  bar.appendChild(modelChip);

  const spacer = document.createElement('div');
  spacer.style.flex = '1';
  bar.appendChild(spacer);

  const newBtn = document.createElement('button');
  newBtn.type = 'button';
  newBtn.textContent = '＋ 新对话';
  newBtn.title = '新建会话';
  newBtn.style.cssText = _btnCss();
  newBtn.addEventListener('click', () => onNewSession && onNewSession());
  bar.appendChild(newBtn);

  const gear = document.createElement('button');
  gear.type = 'button';
  gear.textContent = '⚙';
  gear.title = '配置';
  gear.style.cssText = _btnCss();
  gear.addEventListener('click', () => onOpenConfig && onOpenConfig());
  bar.appendChild(gear);

  effect(() => {
    const mid = currentModel.value;
    const m = availableModels.value.find(x => x.id === mid);
    modelChip.textContent = m ? (m.label || m.id) : mid;
    modelChip.title = m ? `${m.provider || '?'} · ${m.deployment || '?'}` : '';
  });

  return { el: bar };
}

function _btnCss() {
  return [
    'border:1px solid #e0e0e0', 'background:#fff', 'color:#333',
    'padding:5px 10px', 'border-radius:8px', 'cursor:pointer',
    'font-size:13px',
  ].join(';');
}
