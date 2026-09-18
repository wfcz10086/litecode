import { createMessage } from './Message.js';
import { appendReasoningText, createReasoning } from './Reasoning.js';
import { createToolResult } from './ToolResult.js';
import { createDiffView } from './DiffView.js';
import { renderMarkdown, highlightCode } from '../utils/markdown.js';

const MAX_DOM_MESSAGES = 100;
const PLACEHOLDER_CLASS = 'msg-history-placeholder';

/**
 * createMessageList(messages) — 渲染完整历史消息列表
 * messages: msg 对象数组
 * 返回容器 div#msgs
 */
export function createMessageList(messages) {
  const container = document.createElement('div');
  container.id = 'msgs';
  container._allMsgs = [];
  (messages || []).forEach(msg => appendMessage(container, msg));
  return container;
}

/**
 * appendMessage(container, msg) — 增量追加一条消息
 */
export function appendMessage(container, msg) {
  if (container._allMsgs) container._allMsgs.push(msg);
  const el = createMessage(msg);
  container.appendChild(el);
  _trimOldMessages(container);
  _scrollBottom(container);
}

function _trimOldMessages(container) {
  const msgNodes = Array.from(container.children).filter(
    c => !c.classList.contains(PLACEHOLDER_CLASS)
  );
  if (msgNodes.length <= MAX_DOM_MESSAGES) return;

  const excess = msgNodes.length - MAX_DOM_MESSAGES;
  const allMsgs = container._allMsgs || [];
  const trimmedTotal = (container._trimmedCount || 0) + excess;
  container._trimmedCount = trimmedTotal;

  for (let i = 0; i < excess; i++) {
    container.removeChild(msgNodes[i]);
  }

  let placeholder = container.querySelector('.' + PLACEHOLDER_CLASS);
  if (!placeholder) {
    placeholder = document.createElement('div');
    placeholder.className = PLACEHOLDER_CLASS;
    container.insertBefore(placeholder, container.firstChild);
    placeholder.addEventListener('click', () => {
      const count = container._trimmedCount || 0;
      if (!count || !container._allMsgs) return;
      const start = Math.max(0, container._allMsgs.length - MAX_DOM_MESSAGES - 20);
      const end = container._allMsgs.length - MAX_DOM_MESSAGES;
      const toLoad = container._allMsgs.slice(start, end);
      const frag = document.createDocumentFragment();
      toLoad.forEach(m => frag.appendChild(createMessage(m)));
      const firstMsg = Array.from(container.children).find(
        c => !c.classList.contains(PLACEHOLDER_CLASS)
      );
      if (firstMsg) container.insertBefore(frag, firstMsg);
      else container.insertBefore(frag, placeholder.nextSibling);
      container._trimmedCount = Math.max(0, count - toLoad.length);
      _updatePlaceholder(placeholder, container._trimmedCount);
    });
  }
  _updatePlaceholder(placeholder, trimmedTotal);
}

function _updatePlaceholder(el, count) {
  if (count <= 0) {
    el.style.display = 'none';
  } else {
    el.style.display = '';
    el.textContent = `…上方还有 ${count} 条历史（点击加载）`;
  }
}

/**
 * patchLastMessage(container, delta) — 流式 patch 最后一条 AI 消息
 *
 * delta 字段对应 SSE choices[0].delta:
 *   content    string — 追加文本
 *   reasoning  string — 追加推理文本
 *   task_exec  object — 追加/更新工具卡
 *   diff_view  object — 追加 diff 块
 *   usage      object — 更新用量条
 */
export function patchLastMessage(container, delta) {
  let aiMsg = _lastAiMsg(container);
  if (!aiMsg) {
    aiMsg = _createEmptyAiMsg();
    container.appendChild(aiMsg);
  }
  const bubble = aiMsg.querySelector('.mb');
  if (!bubble) return;

  if (delta.reasoning) {
    _patchReasoning(bubble, delta.reasoning);
  }
  if (delta.content) {
    _patchContent(bubble, delta.content);
  }
  if (delta.task_exec) {
    _patchTaskExec(bubble, delta.task_exec);
  }
  if (delta.diff_view) {
    bubble.appendChild(createDiffView(delta.diff_view));
  }
  if (delta.usage) {
    _patchUsage(bubble, delta.usage);
  }

  _scrollBottom(container);
}

function _lastAiMsg(container) {
  const msgs = container.querySelectorAll('.msg.ai');
  return msgs.length ? msgs[msgs.length - 1] : null;
}

function _createEmptyAiMsg() {
  const wrap = document.createElement('div');
  wrap.className = 'msg ai';
  const rl = document.createElement('div');
  rl.className = 'mr';
  rl.textContent = 'AI';
  wrap.appendChild(rl);
  const bubble = document.createElement('div');
  bubble.className = 'mb';
  wrap.appendChild(bubble);
  return wrap;
}

function _patchReasoning(bubble, text) {
  let rsEl = bubble.querySelector('.reasoning-block');
  if (!rsEl) {
    rsEl = createReasoning('');
    bubble.insertBefore(rsEl, bubble.firstChild);
  }
  appendReasoningText(rsEl, text);
}

function _patchContent(bubble, text) {
  let mdEl = bubble.querySelector('.stream-content');
  if (!mdEl) {
    mdEl = document.createElement('div');
    mdEl.className = 'md-content stream-content';
    mdEl._raw = '';
    bubble.appendChild(mdEl);
  }
  mdEl._raw = (mdEl._raw || '') + text;
  mdEl.innerHTML = renderMarkdown(mdEl._raw);
  highlightCode(mdEl);
  _addCopyButtons(mdEl);
}

function _patchTaskExec(bubble, exec) {
  let tg = bubble.querySelector('.tool-group');
  if (!tg) {
    tg = document.createElement('div');
    tg.className = 'tool-group';
    const mdEl = bubble.querySelector('.md-content');
    if (mdEl) bubble.insertBefore(tg, mdEl);
    else bubble.appendChild(tg);
  }

  if (exec.status === 'executing') {
    const card = createToolResult(exec);
    card.dataset.toolExec = 'pending';
    tg.appendChild(card);
  } else if (exec.status === 'done') {
    const pending = tg.querySelector('[data-tool-exec="pending"]');
    if (pending) {
      tg.replaceChild(createToolResult(exec), pending);
    }
  }
}

function _patchUsage(bubble, usage) {
  let ub = bubble.querySelector('.ub');
  if (!ub) {
    ub = document.createElement('div');
    ub.className = 'ub';
    bubble.appendChild(ub);
  }
  const f = n => (n || 0).toLocaleString();
  ub.innerHTML =
    `<span>P:<b>${f(usage.prompt_tokens)}</b></span>` +
    `<span>C:<b>${f(usage.completion_tokens)}</b></span>` +
    `<span>T:<b>${f(usage.total_tokens)}</b></span>` +
    (usage.iterations > 1 ? `<span>${usage.iterations}轮</span>` : '') +
    (usage.elapsed_seconds ? `<span>${usage.elapsed_seconds}s</span>` : '');
}

function _scrollBottom(container) {
  const parent = container.parentElement || container;
  parent.scrollTop = parent.scrollHeight;
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
