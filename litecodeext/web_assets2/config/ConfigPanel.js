import { createModelEditor } from './ModelEditor.js';
import { createCronBuilder } from './CronBuilder.js';
import { createBotPanel } from '../wechat/BotPanel.js';

const TABS = ['模型', '向量库', 'MCP', 'Cron', '微信'];
const PLACEHOLDER_TABS = new Set(['向量库', 'MCP']);

function createTabBar(tabs, onSelect) {
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;border-bottom:2px solid #e0e0e0;margin-bottom:16px';

  const btnEls = {};
  tabs.forEach(t => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = t;
    btn.dataset.tab = t;
    btn.style.cssText = 'padding:8px 16px;border:none;background:none;cursor:pointer;font-size:14px;color:#666;border-bottom:2px solid transparent;margin-bottom:-2px';
    bar.appendChild(btn);
    btnEls[t] = btn;
  });

  bar.addEventListener('click', e => {
    const btn = e.target.closest('button[data-tab]');
    if (!btn) return;
    const tab = btn.dataset.tab;
    Object.values(btnEls).forEach(b => {
      b.style.color = '#666';
      b.style.borderBottomColor = 'transparent';
      b.style.fontWeight = '';
    });
    btn.style.color = '#1976d2';
    btn.style.borderBottomColor = '#1976d2';
    btn.style.fontWeight = '600';
    onSelect(tab);
  });

  function setActive(tab) {
    const btn = btnEls[tab];
    if (btn) btn.click();
  }

  return { el: bar, setActive };
}

export function createConfigPanel() {
  const overlay = document.createElement('div');
  overlay.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:8000;align-items:flex-start;justify-content:center;padding-top:60px';

  const panel = document.createElement('div');
  panel.style.cssText = 'background:#fff;border-radius:10px;width:90%;max-width:680px;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,.18)';

  const panelHeader = document.createElement('div');
  panelHeader.style.cssText = 'display:flex;align-items:center;padding:16px 20px 0;gap:12px';

  const title = document.createElement('h2');
  title.style.cssText = 'margin:0;font-size:17px;flex:1';
  title.textContent = '配置';

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.textContent = '✕';
  closeBtn.style.cssText = 'border:none;background:none;font-size:18px;cursor:pointer;color:#888;padding:0 4px';

  panelHeader.appendChild(title);
  panelHeader.appendChild(closeBtn);

  const body = document.createElement('div');
  body.style.cssText = 'padding:16px 20px 20px;overflow-y:auto;flex:1';

  const contentArea = document.createElement('div');

  let modelEditorEl = null;
  let botPanel = null;

  function showTab(tab) {
    if (botPanel) { botPanel.destroy(); botPanel = null; }
    contentArea.innerHTML = '';
    if (PLACEHOLDER_TABS.has(tab)) {
      const ph = document.createElement('p');
      ph.style.cssText = 'color:#999;font-size:14px;padding:20px 0';
      ph.textContent = `${tab} · 未接入`;
      contentArea.appendChild(ph);
    } else if (tab === '模型') {
      if (!modelEditorEl) modelEditorEl = createModelEditor();
      contentArea.appendChild(modelEditorEl);
    } else if (tab === 'Cron') {
      const { el } = createCronBuilder({ initial: '0 0 * * *' });
      contentArea.appendChild(el);
    } else if (tab === '微信') {
      botPanel = createBotPanel();
      contentArea.appendChild(botPanel.el);
    }
  }

  const tabBar = createTabBar(TABS, showTab);

  body.appendChild(tabBar.el);
  body.appendChild(contentArea);
  panel.appendChild(panelHeader);
  panel.appendChild(body);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  function close() {
    overlay.style.display = 'none';
  }

  function open(tab) {
    overlay.style.display = 'flex';
    tabBar.setActive(tab || TABS[0]);
  }

  closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && overlay.style.display !== 'none') close();
  });

  return { el: overlay, open, close };
}
