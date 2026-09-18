function maskValue(val) {
  if (!val || val.length <= 6) return val || '';
  return val.slice(0, 3) + '***' + val.slice(-3);
}

function buildField(f) {
  const wrap = document.createElement('div');
  wrap.style.cssText = 'margin-bottom:14px';

  const label = document.createElement('label');
  label.style.cssText = 'display:block;font-size:13px;color:#444;margin-bottom:4px';
  label.textContent = f.label + (f.required ? ' *' : '');

  let input;
  if (f.type === 'textarea') {
    input = document.createElement('textarea');
    input.rows = 3;
    input.style.resize = 'vertical';
  } else {
    input = document.createElement('input');
    input.type = f.type === 'password' ? 'password' : (f.type || 'text');
  }

  input.style.cssText = 'width:100%;box-sizing:border-box;padding:7px 10px;border:1px solid #ccc;border-radius:4px;font-size:14px';
  input.placeholder = f.placeholder || '';
  input.dataset.key = f.key;

  if (f.mask && f.value) {
    input.dataset.realValue = f.value;
    input.value = maskValue(f.value);
    input.addEventListener('focus', () => {
      if (input.dataset.realValue) {
        input.value = input.dataset.realValue;
        delete input.dataset.realValue;
        if (f.type === 'password') input.type = 'text';
      }
    });
  } else {
    input.value = f.value != null ? String(f.value) : '';
  }

  const errEl = document.createElement('span');
  errEl.style.cssText = 'display:none;color:#d32f2f;font-size:12px;margin-top:3px';

  wrap.appendChild(label);

  if (f.type === 'password' && !f.mask) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:6px;align-items:center';
    row.appendChild(input);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.textContent = '显示';
    toggle.style.cssText = 'white-space:nowrap;font-size:12px;padding:4px 8px;border:1px solid #ccc;border-radius:4px;background:#f5f5f5;cursor:pointer';
    toggle.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      toggle.textContent = show ? '隐藏' : '显示';
    });
    row.appendChild(toggle);
    wrap.appendChild(row);
  } else {
    wrap.appendChild(input);
  }

  wrap.appendChild(errEl);
  return { wrap, input, errEl, field: f };
}

export function formCard({ title, fields, confirmText = '保存' }) {
  return new Promise(resolve => {
    if (document.querySelector('.form-overlay,.confirm-overlay')) { resolve(null); return; }
    const overlay = document.createElement('div');
    overlay.className = 'form-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:9000';

    const card = document.createElement('div');
    card.style.cssText = 'background:#fff;border-radius:8px;padding:24px;min-width:360px;max-width:520px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.18)';

    const h = document.createElement('h3');
    h.style.cssText = 'margin:0 0 18px;font-size:16px';
    h.textContent = title;

    const form = document.createElement('form');
    form.addEventListener('submit', e => e.preventDefault());

    const items = fields.map(f => {
      const item = buildField(f);
      form.appendChild(item.wrap);
      return item;
    });

    const btns = document.createElement('div');
    btns.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:18px';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.textContent = '取消';

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'btn btn-primary';
    saveBtn.textContent = confirmText;

    btns.appendChild(cancelBtn);
    btns.appendChild(saveBtn);
    card.appendChild(h);
    card.appendChild(form);
    card.appendChild(btns);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    function finish(result) {
      document.body.removeChild(overlay);
      document.removeEventListener('keydown', onKey);
      resolve(result);
    }

    function collect() {
      let valid = true;
      const out = {};
      for (const { input, errEl, field } of items) {
        const val = input.dataset.realValue || input.value;
        if (field.required && !val.trim()) {
          errEl.textContent = `${field.label} 不能为空`;
          errEl.style.display = 'block';
          valid = false;
        } else {
          errEl.style.display = 'none';
          out[field.key] = field.type === 'number' ? Number(val) : val;
        }
      }
      return valid ? out : null;
    }

    function onKey(e) {
      if (e.key === 'Escape') finish(null);
    }

    cancelBtn.addEventListener('click', () => finish(null));
    saveBtn.addEventListener('click', () => {
      const out = collect();
      if (out) finish(out);
    });
    document.addEventListener('keydown', onKey);

    if (items.length > 0) items[0].input.focus();
  });
}
