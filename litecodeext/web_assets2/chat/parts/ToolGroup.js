// ToolGroup.js — 工具调用聚合折叠块 (Kimi 风格 "调用了 N 个工具" summary).

import { createToolResult } from '../ToolResult.js';
import { createCollapsible } from '../../ui/Collapsible.js';

/**
 * createToolGroup(tools, toolResults) — 工具调用组
 *
 * tools: string[]         — 每项形如 "read_file: path/to/x.py"
 * toolResults: object[]   — 与 tools 一一对应, {result?, content?}
 *
 * 用户折叠 / 展开状态与 Reasoning 一致 (默认收起, 眼不见为净).
 */
export function createToolGroup(tools, toolResults = []) {
  const body = document.createElement('div');
  body.className = 'tool-group-body';
  tools.forEach((t, i) => {
    body.appendChild(_makeToolBlock(t, toolResults[i]));
  });

  const summary = _buildToolSummary(tools);
  return createCollapsible({
    summary,
    content: body,
    open: false,
    cls: 'tool-group',
  });
}

function _makeToolBlock(t, resObj) {
  const colon = t.indexOf(': ');
  const toolName = colon > 0 ? t.slice(0, colon) : t;
  const args = colon > 0 ? t.slice(colon + 2) : '';
  const result = resObj ? (resObj.result || resObj.content || null) : null;
  return createToolResult({ type: 'tool_use', name: toolName, input: args, result });
}

function _buildToolSummary(tools) {
  const span = document.createElement('span');
  span.className = 'tool-group-summary';
  const first = tools[0] ? tools[0].split(': ')[0] : '';
  span.innerHTML = `<span class="tg-icon">&#x1F527;</span>`
    + `<span class="tg-label">调用了 ${tools.length} 个工具</span>`
    + (first ? `<span class="tg-hint">${first}${tools.length > 1 ? '…' : ''}</span>` : '');
  return span;
}
