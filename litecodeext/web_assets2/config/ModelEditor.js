import { signal, effect } from '../store/signal.js';
import { fetchModels, saveModel, testModel } from '../store/config.js';
import { formCard } from '../ui/FormCard.js';

function maskKey(val) {
  if (!val || val.length <= 6) return val || '';
  return val.slice(0, 3) + '***' + val.slice(-3);
}

function createModelRow(model, onSaved) {
  const row = document.createElement('div');
  row.className = 'model-row';
  row.style.cssText = 'border:1px solid #e0e0e0;border-radius:6px;margin-bottom:8px;overflow:hidden';

  const header = document.createElement('div');
  header.style.cssText = 'display:flex;align-items:center;padding:10px 14px;cursor:pointer;background:#fafafa;gap:10px';

  const nameEl = document.createElement('span');
  nameEl.style.cssText = 'font-weight:600;flex:1';
  nameEl.textContent = model.name;

  const arrow = document.createElement('span');
  arrow.textContent = '▶';
  arrow.style.cssText = 'font-size:11px;color:#888;transition:transform .2s';

  header.appendChild(nameEl);
  header.appendChild(arrow);

  const panel = document.createElement('div');
  panel.style.cssText = 'display:none;padding:14px;border-top:1px solid #e0e0e0;background:#fff';

  const loading = signal(false);
  const testing = signal(false);
  const testResult = signal('');

  let draft = {
    backend_url: model.backend_url || '',
    api_key: model.api_key || '',
    max_tokens: model.max_tokens || 4096,
  };

  function buildInput(label, key, type = 'text') {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'margin-bottom:10px';
    const lbl = document.createElement('label');
    lbl.style.cssText = 'display:block;font-size:12px;color:#555;margin-bottom:3px';
    lbl.textContent = label;
    const inp = document.createElement(type === 'textarea' ? 'textarea' : 'input');
    inp.style.cssText = 'width:100%;box-sizing:border-box;padding:6px 9px;border:1px solid #ccc;border-radius:4px;font-size:13px';
    if (key === 'api_key') {
      inp.type = 'password';
      inp.placeholder = maskKey(draft[key]) || '留空不改';
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;gap:6px;align-items:center';
      row.appendChild(inp);
      const tog = document.createElement('button');
      tog.type = 'button';
      tog.textContent = '显示';
      tog.style.cssText = 'white-space:nowrap;font-size:12px;padding:4px 8px;border:1px solid #ccc;border-radius:4px;background:#f5f5f5;cursor:pointer';
      tog.addEventListener('click', () => {
        const show = inp.type === 'password';
        inp.type = show ? 'text' : 'password';
        tog.textContent = show ? '隐藏' : '显示';
      });
      row.appendChild(tog);
      wrap.appendChild(lbl);
      wrap.appendChild(row);
    } else {
      inp.type = type === 'number' ? 'number' : 'text';
      inp.value = draft[key] != null ? String(draft[key]) : '';
      wrap.appendChild(lbl);
      wrap.appendChild(inp);
    }
    inp.addEventListener('input', () => {
      draft[key] = type === 'number' ? Number(inp.value) : inp.value;
    });
    wrap._input = inp;
    return wrap;
  }

  const urlWrap = buildInput('Backend URL', 'backend_url');
  const keyWrap = buildInput('API Key (mask)', 'api_key');
  const tokWrap = buildInput('Max Tokens', 'max_tokens', 'number');

  const testResultEl = document.createElement('div');
  testResultEl.style.cssText = 'font-size:12px;color:#555;margin-top:6px;min-height:16px;word-break:break-all';

  const btnRow = document.createElement('div');
  btnRow.style.cssText = 'display:flex;gap:8px;margin-top:14px;flex-wrap:wrap';

  const testBtn = document.createElement('button');
  testBtn.type = 'button';
  testBtn.textContent = '测试连接';
  testBtn.style.cssText = 'padding:6px 14px;border:1px solid #1976d2;border-radius:4px;background:#e3f2fd;color:#1565c0;cursor:pointer;font-size:13px';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.textContent = '保存';
  saveBtn.style.cssText = 'padding:6px 14px;border:none;border-radius:4px;background:#1976d2;color:#fff;cursor:pointer;font-size:13px';

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.textContent = '取消';
  cancelBtn.style.cssText = 'padding:6px 14px;border:1px solid #ccc;border-radius:4px;background:#f5f5f5;cursor:pointer;font-size:13px';

  effect(() => {
    testBtn.disabled = testing.value;
    testBtn.textContent = testing.value ? '测试中…' : '测试连接';
    saveBtn.disabled = loading.value;
    saveBtn.textContent = loading.value ? '保存中…' : '保存';
    testResultEl.textContent = testResult.value;
  });

  testBtn.addEventListener('click', async () => {
    testing.value = true;
    testResult.value = '';
    try {
      const r = await testModel(model.name);
      const txt = await r.text();
      testResult.value = `${r.status} ${r.statusText}: ${txt.slice(0, 200)}`;
    } catch (e) {
      testResult.value = `错误: ${e.message}`;
    } finally {
      testing.value = false;
    }
  });

  saveBtn.addEventListener('click', async () => {
    loading.value = true;
    try {
      const keyInp = keyWrap._input;
      const payload = {
        backend_url: draft.backend_url,
        max_tokens: draft.max_tokens,
      };
      if (keyInp.value.trim()) payload.api_key = keyInp.value;
      await saveModel(model.name, payload);
      onSaved && onSaved();
    } catch (e) {
      testResult.value = `保存失败: ${e.message}`;
    } finally {
      loading.value = false;
    }
  });

  cancelBtn.addEventListener('click', () => {
    panel.style.display = 'none';
    arrow.style.transform = '';
  });

  btnRow.appendChild(testBtn);
  btnRow.appendChild(saveBtn);
  btnRow.appendChild(cancelBtn);

  panel.appendChild(urlWrap);
  panel.appendChild(keyWrap);
  panel.appendChild(tokWrap);
  panel.appendChild(btnRow);
  panel.appendChild(testResultEl);

  let open = false;
  header.addEventListener('click', () => {
    open = !open;
    panel.style.display = open ? 'block' : 'none';
    arrow.style.transform = open ? 'rotate(90deg)' : '';
  });

  row.appendChild(header);
  row.appendChild(panel);
  return row;
}

export function createModelEditor() {
  const el = document.createElement('div');
  el.className = 'model-editor';

  const toolbar = document.createElement('div');
  toolbar.style.cssText = 'display:flex;justify-content:flex-end;margin-bottom:10px';

  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.textContent = '+ 新增模型';
  addBtn.style.cssText = 'padding:6px 14px;border:none;border-radius:4px;background:#1976d2;color:#fff;cursor:pointer;font-size:13px';
  toolbar.appendChild(addBtn);

  const list = document.createElement('div');

  async function reload() {
    list.innerHTML = '';
    try {
      const models = await fetchModels();
      const arr = Array.isArray(models) ? models : (models.models || []);
      if (arr.length === 0) {
        const empty = document.createElement('p');
        empty.style.cssText = 'color:#999;font-size:13px';
        empty.textContent = '暂无模型配置';
        list.appendChild(empty);
      }
      arr.forEach(m => list.appendChild(createModelRow(m, reload)));
    } catch (e) {
      const errEl = document.createElement('p');
      errEl.style.cssText = 'color:#d32f2f;font-size:13px';
      errEl.textContent = `加载失败: ${e.message}`;
      list.appendChild(errEl);
    }
  }

  addBtn.addEventListener('click', async () => {
    const result = await formCard({
      title: '新增模型',
      fields: [
        { key: 'name', label: '模型名称', type: 'text', required: true, placeholder: 'gpt-4o' },
        { key: 'backend_url', label: 'Backend URL', type: 'text', placeholder: 'https://api.openai.com/v1' },
        { key: 'api_key', label: 'API Key', type: 'password' },
        { key: 'max_tokens', label: 'Max Tokens', type: 'number', value: 4096 },
      ],
    });
    if (!result) return;
    try {
      await saveModel(result.name, {
        backend_url: result.backend_url,
        api_key: result.api_key,
        max_tokens: Number(result.max_tokens) || 4096,
      });
      reload();
    } catch (e) {
      // silently ignore, user can retry
    }
  });

  el.appendChild(toolbar);
  el.appendChild(list);
  reload();
  return el;
}
