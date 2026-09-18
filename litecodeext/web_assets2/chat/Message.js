import { createReasoning } from './Reasoning.js';
import { createToolResult } from './ToolResult.js';
import { createDiffView } from './DiffView.js';
import { renderMarkdown, highlightCode } from '../utils/markdown.js';
import { createCollapsible } from '../ui/Collapsible.js';

const LONG_MSG_THRESHOLD = 4000;

/**
 * createMessage(msg) — 渲染一条消息
 *
 * msg 字段 (历史会话格式):
 *   role: 'user' | 'assistant'
 *   content: string | array
 *   reasoning?: string
 *   tools?: string[]            — task_exec detail 列表
 *   tool_results?: object[]     — 与 tools 一一对应的 result 对象
 *   diffs?: {filepath,diff}[]
 *   usage?: object
 *   source?: 'wechat'|''
 *   wx_user?: string
 *
 * 也兼容历史中 role='tool_use'|'tool_result' 的 block:
 *   { role:'tool_use', type:'tool_use', name, input }
 *   { role:'tool_result', type:'tool_result', content, result }
 */
export function createMessage(msg) {
  if (!msg) return document.createDocumentFragment();

  const role = msg.role || 'assistant';

  if (role === 'tool_use' || msg.type === 'tool_use') {
    return _wrapToolBlock(createToolResult({ type: 'tool_use', name: msg.name, input: msg.input }));
  }
  if (role === 'tool_result' || msg.type === 'tool_result') {
    return _wrapToolBlock(createToolResult({ type: 'tool_result', result: msg.content || msg.result }));
  }

  const wrap = document.createElement('div');
  wrap.className = `msg ${role === 'user' ? 'user' : 'ai'}`;

  const roleLabel = document.createElement('div');
  roleLabel.className = 'mr';
  roleLabel.textContent = _roleLabel(msg);
  wrap.appendChild(roleLabel);

  const bubble = document.createElement('div');
  bubble.className = 'mb';

  if (role === 'user') {
    _renderUserContent(bubble, msg);
  } else {
    _renderAssistantContent(bubble, msg);
  }

  wrap.appendChild(bubble);
  return wrap;
}

function _roleLabel(msg) {
  const isWx = msg.source === 'wechat';
  if (msg.role === 'user') return isWx ? (msg.wx_user || '微信用户') : '你';
  return isWx ? 'AI (微信)' : 'AI';
}

function _renderUserContent(bubble, msg) {
  const raw = _extractText(msg.content);
  if (raw) bubble.textContent = raw;
}

function _renderAssistantContent(bubble, msg) {
  if (msg.reasoning) {
    bubble.appendChild(createReasoning(msg.reasoning));
  }

  const tools = msg.tools || [];
  const toolResults = msg.tool_results || msg._tool_results || [];
  if (tools.length) {
    const tg = document.createElement('div');
    tg.className = 'tool-group';
    tools.forEach((t, i) => {
      const colon = t.indexOf(': ');
      const toolName = colon > 0 ? t.slice(0, colon) : t;
      const args = colon > 0 ? t.slice(colon + 2) : '';
      const resObj = toolResults[i] || null;
      const result = resObj ? (resObj.result || resObj.content || null) : null;
      const block = {
        type: 'tool_use',
        name: toolName,
        input: args,
        result,
      };
      tg.appendChild(createToolResult(block));
    });
    bubble.appendChild(tg);
  }

  const diffs = msg.diffs || [];
  diffs.forEach(dv => {
    if (dv.diff) bubble.appendChild(createDiffView(dv));
  });

  const text = _extractText(msg.content);
  if (text) {
    if (text.length > LONG_MSG_THRESHOLD) {
      const summary = `&#x1F4DC; 长文消息 ${text.length} 字符`;
      const contentEl = document.createElement('div');
      contentEl.className = 'md-content';
      const col = createCollapsible({
        summary,
        content: contentEl,
        open: false,
        cls: 'long-msg-block',
      });
      col.addEventListener('toggle', () => {
        if (col.open && !contentEl._rendered) {
          contentEl._rendered = true;
          contentEl.innerHTML = renderMarkdown(text);
          highlightCode(contentEl);
          _addCopyButtons(contentEl);
        }
      });
      bubble.appendChild(col);
    } else {
      const md = document.createElement('div');
      md.className = 'md-content';
      md.innerHTML = renderMarkdown(text);
      highlightCode(md);
      _addCopyButtons(md);
      bubble.appendChild(md);
    }
  }

  if (msg.usage) {
    bubble.appendChild(_buildUsageBar(msg.usage));
  }
}

function _extractText(content) {
  if (!content) return '';
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map(p => {
      if (typeof p === 'string') return p;
      if (p && p.type === 'text') return p.text || '';
      if (p && p.type === 'image_url') return '[图片]';
      return '';
    }).filter(Boolean).join('\n');
  }
  return String(content);
}

function _buildUsageBar(usage) {
  const ub = document.createElement('div');
  ub.className = 'ub';
  const f = n => (n || 0).toLocaleString();
  ub.innerHTML =
    `<span>P:<b>${f(usage.prompt_tokens)}</b></span>` +
    `<span>C:<b>${f(usage.completion_tokens)}</b></span>` +
    `<span>T:<b>${f(usage.total_tokens)}</b></span>` +
    (usage.iterations > 1 ? `<span>${usage.iterations}轮</span>` : '') +
    (usage.elapsed_seconds ? `<span>${usage.elapsed_seconds}s</span>` : '');
  return ub;
}

function _wrapToolBlock(el) {
  const wrap = document.createElement('div');
  wrap.className = 'msg tool-msg';
  wrap.appendChild(el);
  return wrap;
}

function _addCopyButtons(container) {
  container.querySelectorAll('pre').forEach(pre => {
    if (pre.querySelector('.cpb')) return;
    const btn = document.createElement('button');
    btn.className = 'cpb';
    btn.textContent = 'copy';
    pre.style.position = 'relative';
    pre.appendChild(btn);
    btn.addEventListener('click', () => {
      navigator.clipboard.writeText(pre.querySelector('code')?.textContent || '');
      btn.textContent = '✓';
      setTimeout(() => { btn.textContent = 'copy'; }, 1500);
    });
  });
}
