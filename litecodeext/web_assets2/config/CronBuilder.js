const PRESETS = [
  { label: '每分钟', value: '* * * * *' },
  { label: '每小时', value: '0 * * * *' },
  { label: '每天',   value: '0 0 * * *' },
  { label: '每周',   value: '0 0 * * 0' },
  { label: '每月',   value: '0 0 1 * *' },
  { label: '自定义', value: '__custom__' },
];

function matchPreset(expr) {
  const p = PRESETS.find(p => p.value !== '__custom__' && p.value === expr);
  return p ? p.value : '__custom__';
}

export function createCronBuilder({ initial = '0 0 * * *', onChange } = {}) {
  let currentExpr = initial;

  const el = document.createElement('div');
  el.style.cssText = 'display:flex;flex-direction:column;gap:12px';

  const preview = document.createElement('div');
  preview.style.cssText = 'font-family:monospace;font-size:13px;background:#f5f5f5;border:1px solid #e0e0e0;border-radius:4px;padding:6px 10px;color:#333';

  const row = document.createElement('div');
  row.style.cssText = 'display:flex;align-items:center;gap:10px';

  const label = document.createElement('label');
  label.style.cssText = 'font-size:13px;color:#444;white-space:nowrap';
  label.textContent = '执行频率';

  const select = document.createElement('select');
  select.style.cssText = 'padding:6px 10px;border:1px solid #ccc;border-radius:4px;font-size:13px;background:#fff;cursor:pointer';

  PRESETS.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.value;
    opt.textContent = p.label;
    select.appendChild(opt);
  });

  const customWrap = document.createElement('div');
  customWrap.style.cssText = 'display:none;flex-direction:column;gap:6px';

  const customLabel = document.createElement('label');
  customLabel.style.cssText = 'font-size:12px;color:#666';
  customLabel.textContent = 'Cron 表达式 (分 时 日 月 周)';

  const customInput = document.createElement('input');
  customInput.type = 'text';
  customInput.style.cssText = 'font-family:monospace;padding:6px 10px;border:1px solid #ccc;border-radius:4px;font-size:13px;width:200px;box-sizing:border-box';
  customInput.placeholder = '* * * * *';

  customWrap.appendChild(customLabel);
  customWrap.appendChild(customInput);

  row.appendChild(label);
  row.appendChild(select);

  el.appendChild(preview);
  el.appendChild(row);
  el.appendChild(customWrap);

  function updatePreview(expr) {
    preview.textContent = 'Cron: ' + expr;
  }

  function setExpression(expr) {
    currentExpr = expr;
    const matched = matchPreset(expr);
    select.value = matched;
    if (matched === '__custom__') {
      customWrap.style.display = 'flex';
      customInput.value = expr;
    } else {
      customWrap.style.display = 'none';
    }
    updatePreview(expr);
  }

  function getExpression() {
    return currentExpr;
  }

  select.addEventListener('change', () => {
    const val = select.value;
    if (val === '__custom__') {
      customWrap.style.display = 'flex';
      customInput.value = currentExpr;
      customInput.focus();
    } else {
      customWrap.style.display = 'none';
      currentExpr = val;
      updatePreview(currentExpr);
      if (onChange) onChange(currentExpr);
    }
  });

  customInput.addEventListener('input', () => {
    currentExpr = customInput.value.trim() || '* * * * *';
    updatePreview(currentExpr);
    if (onChange) onChange(currentExpr);
  });

  setExpression(initial);

  return { el, getExpression, setExpression };
}
