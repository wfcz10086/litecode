import { cur, setCur, loadSession, createSession } from './store/session.js';
import { streaming } from './store/stream.js';
import { onDelta } from './net/sse.js';
import { streamChat } from './net/sse.js';
import { interrupt } from './net/interrupt.js';
import { uploadFiles } from './store/upload.js';
import { loadModels } from './store/prefs.js';
import { createMessageList, appendMessage, patchLastMessage } from './chat/MessageList.js';
import { createMessageActions } from './chat/MessageActions.js';
import { createChatInput } from './chat/ChatInput.js';
import { createChatHeader } from './ui/ChatHeader.js';
import { createUploadZone } from './files/UploadZone.js';
import { createPendingCards } from './files/PendingCards.js';
import { createConfigPanel } from './config/ConfigPanel.js';
import { createFileViewer } from './workspace/FileViewer.js';

async function mount() {
  const app = document.getElementById('app');
  if (!app) return;

  app.style.cssText = 'display:flex;flex-direction:column;height:100vh;overflow:hidden;background:#fff';

  const configPanel = createConfigPanel();

  // 后台加载模型列表 (Kimi 风格 header 需要)
  loadModels();

  const header = createChatHeader({
    onOpenConfig: () => configPanel.open(),
    onNewSession: async () => {
      const sid = await createSession();
      if (!sid) return;
      setCur(sid);
      const u = new URL(location.href);
      u.searchParams.set('sid', sid);
      history.replaceState({}, '', u);
      msgList.innerHTML = '';
      appendMessage(msgList, { role: 'assistant', content: '新会话已创建, 请发消息。' });
    },
  });
  app.appendChild(header.el);

  const scrollArea = document.createElement('div');
  scrollArea.style.cssText = 'flex:1;overflow-y:auto;padding:16px 12px;background:#fbfbfb';
  const msgList = createMessageList([]);
  scrollArea.appendChild(msgList);
  app.appendChild(scrollArea);

  app.appendChild(createPendingCards());

  const urlSid = new URLSearchParams(location.search).get('sid');
  if (urlSid) {
    setCur(urlSid);
    loadSession(urlSid).then(msgs => {
      if (msgs && msgs.length) {
        msgs.forEach(m => appendMessage(msgList, m));
      } else {
        appendMessage(msgList, { role: 'assistant', content: '开始对话吧。' });
      }
    });
  } else {
    appendMessage(msgList, { role: 'assistant', content: '你好, 我是 LiteCode。选好模型/思考强度后发消息。' });
  }

  const chatInput = createChatInput({
    getSessionId: () => cur.value,
    onSend: async (text) => {
      let sid = cur.value;
      if (!sid) {
        sid = await createSession();
        if (!sid) {
          appendMessage(msgList, { role: 'assistant', content: '[错误] 创建会话失败' });
          return;
        }
        setCur(sid);
        const u = new URL(location.href);
        u.searchParams.set('sid', sid);
        history.replaceState({}, '', u);
      }
      appendMessage(msgList, { role: 'user', content: text });
      appendMessage(msgList, { role: 'assistant', content: '' });
      await streamChat(sid, text);
    },
    onInterrupt: () => interrupt(cur.value),
    onFilesSelected: (files) => uploadFiles(files),
  });
  app.appendChild(chatInput.el);

  onDelta('content',   v => patchLastMessage(msgList, { content: v }));
  onDelta('reasoning', v => patchLastMessage(msgList, { reasoning: v }));
  onDelta('task_exec', v => patchLastMessage(msgList, { task_exec: v }));
  onDelta('diff_view', v => patchLastMessage(msgList, { diff_view: v }));
  onDelta('usage',     v => patchLastMessage(msgList, { usage: v }));

  scrollArea.addEventListener('dblclick', (e) => {
    const userBubble = e.target.closest('.msg.user .mb');
    if (!userBubble) return;
    const msgEl = userBubble.closest('.msg.user');
    if (!msgEl) return;
    const actions = createMessageActions(msgEl._msg || {}, cur.value);
    if (!msgEl.querySelector('.msg-actions')) msgEl.appendChild(actions);
  });

  document.body.appendChild(createUploadZone());

  const viewer = createFileViewer();
  document.body.appendChild(viewer.el);

  chatInput.focus();
}

document.addEventListener('DOMContentLoaded', mount);
