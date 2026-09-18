import { formCard } from '../ui/FormCard.js';
import { confirmCard } from '../ui/ConfirmCard.js';

const NODE_FIELDS = [
  { key: 'name',  label: '节点名',       required: true  },
  { key: 'type',  label: '类型',         type: 'text'    },
  { key: 'deps',  label: '依赖(逗号分隔)', type: 'text'  },
];

function parseDeps(str) {
  return (str || '').split(',').map(s => s.trim()).filter(Boolean);
}

function renderNodeRow(node, onEdit, onDelete) {
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;align-items:center;gap:12px;padding:8px 10px;border:1px solid #e8e8e8;border-radius:6px;background:#fafafa';

  const info = document.createElement('div');
  info.style.cssText = 'flex:1;font-size:13px;color:#333;min-width:0';
  info.innerHTML = `<strong>${node.name}</strong>&nbsp;<span style="color:#888">[${node.type || '-'}]</span>` +
    (node.deps && node.deps.length ? `&nbsp;<span style="color:#aaa;font-size:12px">deps: ${node.deps.join(', ')}</span>` : '');

  const editBtn = document.createElement('button');
  editBtn.type = 'button';
  editBtn.textContent = '编辑';
  editBtn.style.cssText = 'font-size:12px;padding:3px 10px;border:1px solid #ccc;border-radius:4px;background:#fff;cursor:pointer';

  const delBtn = document.createElement('button');
  delBtn.type = 'button';
  delBtn.textContent = '删除';
  delBtn.style.cssText = 'font-size:12px;padding:3px 10px;border:1px solid #fca5a5;border-radius:4px;background:#fff;color:#dc2626;cursor:pointer';

  editBtn.addEventListener('click', onEdit);
  delBtn.addEventListener('click', onDelete);

  row.appendChild(info);
  row.appendChild(editBtn);
  row.appendChild(delBtn);
  return row;
}

export function createDagEditor({ initialDag = null, onSave } = {}) {
  let nodes = (initialDag && Array.isArray(initialDag.nodes)) ? [...initialDag.nodes] : [];

  const el = document.createElement('div');
  el.style.cssText = 'display:flex;flex-direction:column;gap:12px';

  const listEl = document.createElement('div');
  listEl.style.cssText = 'display:flex;flex-direction:column;gap:8px;min-height:40px';

  const actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:8px;margin-top:4px';

  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.textContent = '+ 添加节点';
  addBtn.style.cssText = 'font-size:13px;padding:6px 14px;border:1px dashed #1976d2;border-radius:4px;background:#fff;color:#1976d2;cursor:pointer';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.textContent = '保存 DAG';
  saveBtn.style.cssText = 'font-size:13px;padding:6px 14px;border:none;border-radius:4px;background:#1976d2;color:#fff;cursor:pointer';

  actions.appendChild(addBtn);
  actions.appendChild(saveBtn);

  el.appendChild(listEl);
  el.appendChild(actions);

  function renderList() {
    listEl.innerHTML = '';
    if (nodes.length === 0) {
      const empty = document.createElement('p');
      empty.style.cssText = 'color:#bbb;font-size:13px;margin:0;padding:8px 0';
      empty.textContent = '暂无节点，点击「+ 添加节点」开始';
      listEl.appendChild(empty);
      return;
    }
    nodes.forEach((node, idx) => {
      const row = renderNodeRow(
        node,
        async () => {
          const result = await formCard({
            title: '编辑节点',
            fields: NODE_FIELDS.map(f => ({ ...f, value: f.key === 'deps' ? (node.deps || []).join(', ') : (node[f.key] || '') })),
          });
          if (!result) return;
          nodes[idx] = { ...node, name: result.name, type: result.type, deps: parseDeps(result.deps) };
          renderList();
        },
        async () => {
          const ok = await confirmCard({ title: '删除节点', message: `确认删除节点「${node.name}」？` });
          if (!ok) return;
          nodes.splice(idx, 1);
          renderList();
        },
      );
      listEl.appendChild(row);
    });
  }

  addBtn.addEventListener('click', async () => {
    const result = await formCard({ title: '新增节点', fields: NODE_FIELDS });
    if (!result) return;
    nodes.push({ name: result.name, type: result.type || '', deps: parseDeps(result.deps) });
    renderList();
  });

  saveBtn.addEventListener('click', () => {
    if (onSave) onSave({ nodes: [...nodes] });
  });

  renderList();

  return { el };
}
