import { createCollapsible } from '../ui/Collapsible.js';

const ARGS_PREVIEW = 120;
const RESULT_PREVIEW = 200;

/**
 * createToolResult(block) — 工具卡折叠组件
 *
 * block 形态 1 (SSE task_exec):
 *   { status: 'executing'|'done', detail: 'tool_name: args...', agent_label?: string }
 *
 * block 形态 2 (历史会话 tool_use/tool_result 对):
 *   { type: 'tool_use'|'tool_result', name?, input?, content?, result? }
 *
 * 返回 .tool-card details 元素
 */
export function createToolResult(block) {
  if (!block) return document.createDocumentFragment();

  if (block.type === 'tool_use' || block.type === 'tool_result') {
    return _fromHistoryBlock(block);
  }
  return _fromTaskExec(block);
}

function _fromTaskExec(exec) {
  const { status, detail = '', agent_label } = exec;
  const colon = detail.indexOf(': ');
  const toolName = colon > 0 ? detail.slice(0, colon) : detail;
  const args = colon > 0 ? detail.slice(colon + 2) : '';

  const summary = _buildExecSummary(toolName, status, agent_label);
  const bodyEl = _buildExecBody(args, null);

  const details = createCollapsible({
    summary,
    content: bodyEl,
    open: false,
    cls: 'tool-card tool-exec',
  });
  details._toolName = toolName;
  details._status = status;
  return details;
}

function _fromHistoryBlock(block) {
  const toolName = block.name || block.tool_name || '(unknown)';
  const input = block.input || block.args || null;
  const result = block.content || block.result || null;

  const summary = _buildExecSummary(toolName, result ? 'done' : 'executing', null);
  const argsStr = input ? (typeof input === 'string' ? input : JSON.stringify(input, null, 2)) : '';
  const resultStr = result ? (typeof result === 'string' ? result : JSON.stringify(result, null, 2)) : '';

  const bodyEl = _buildExecBody(argsStr, resultStr);
  return createCollapsible({
    summary,
    content: bodyEl,
    open: false,
    cls: 'tool-card tool-history',
  });
}

function _buildExecSummary(toolName, status, agentLabel) {
  const span = document.createElement('span');
  span.className = 'tool-summary';
  const icon = status === 'done' ? '&#x2705;' : '&#x23F3;';
  const labelHtml = agentLabel ? `<span class="tool-agent">[${_esc(agentLabel)}]</span> ` : '';
  span.innerHTML = `${icon} ${labelHtml}<span class="tool-name">${_esc(toolName)}</span>`;
  return span;
}

function _buildExecBody(argsStr, resultStr) {
  const div = document.createElement('div');
  div.className = 'tool-body';

  if (argsStr) {
    const preview = argsStr.length > ARGS_PREVIEW ? argsStr.slice(0, ARGS_PREVIEW) + '…' : argsStr;
    const argsDiv = document.createElement('div');
    argsDiv.className = 'tool-args';
    argsDiv.textContent = preview;
    if (argsStr.length > ARGS_PREVIEW) {
      const full = createCollapsible({
        summary: '展开参数',
        content: argsStr,
        open: false,
        cls: 'tool-args-full',
      });
      div.appendChild(argsDiv);
      div.appendChild(full);
    } else {
      div.appendChild(argsDiv);
    }
  }

  if (resultStr) {
    const resultDiv = document.createElement('div');
    resultDiv.className = 'tool-result';
    if (resultStr.length > RESULT_PREVIEW) {
      const preview = resultStr.slice(0, RESULT_PREVIEW) + '…';
      resultDiv.textContent = '↳ ' + preview;
      const full = createCollapsible({
        summary: '展开结果',
        content: resultStr,
        open: false,
        cls: 'tool-result-full',
      });
      div.appendChild(resultDiv);
      div.appendChild(full);
    } else {
      resultDiv.textContent = '↳ ' + resultStr;
      div.appendChild(resultDiv);
    }
  }

  return div;
}

function _esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
