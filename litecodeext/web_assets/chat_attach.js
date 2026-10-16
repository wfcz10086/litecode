/* chat_attach.js — [chat-runs 2026-10-16] 接入进行中的对话轮次
 * 轮次在 web_ui 后台跑 (routers/chat_router.py + lib/chat_runs.py), 浏览器只是订阅者:
 *   - 刷新 / 重新打开 / 切回: swS 渲染完历史后 resumeIfRunning → 回放本轮已产出的帧 + 实时跟随
 *   - 断网 / 60s 无帧 / 连接被掐: _streamTurn 收尾时 afterTurn 查状态, 还在跑就自动重连
 *   - 别的标签页 / 别人打开同一会话: 同样走 resumeIfRunning
 * 依赖 chat_core.js 的 cur / streaming / renderMsgs / sbot / _newAiBubble / _streamTurn。
 */
(function () {
  const RETRY_MS = 2000, MAX_OFFLINE_TRIES = 90;   // 断网最多等 3 分钟
  let offlineTries = 0;

  async function runStatus(sid) {
    try {
      const r = await fetch(`/api/chat/${sid}/run`);
      return r.ok ? await r.json() : { running: false };
    } catch (e) { return null; }                    // web_ui 不通 (断网 / 重启中)
  }

  function attach(sid) {
    return _streamTurn(_newAiBubble(), () => fetch(`/api/chat/${sid}/stream?start=0`));
  }

  /* 以后台状态为准: 本轮还在跑 → 历史只渲染到触发本轮的那条 user (之后的中间步骤 server 已落盘,
   * 会被导进历史; 不截掉就会和回放重复), 剩下的靠回放补齐 + 实时跟随 */
  async function resumeIfRunning(sid, msgs) {
    const st = await runStatus(sid);
    if (!st || !st.running) return;
    if (cur !== sid || streaming) return;
    let u = msgs.length - 1;
    while (u >= 0 && msgs[u].role !== 'user') u--;
    if (u < msgs.length - 1) renderMsgs(msgs.slice(0, u + 1));
    attach(sid);
  }

  async function resume(sid) {
    if (cur !== sid || streaming) return;
    let msgs;
    try { msgs = (await (await fetch(`/api/sessions/${sid}`)).json()).messages || []; }
    catch (e) { setTimeout(() => afterTurn(sid), RETRY_MS); return; }
    if (cur !== sid || streaming) return;
    renderMsgs(msgs); sbot(true);
    resumeIfRunning(sid, msgs);
  }

  async function afterTurn(sid) {
    const st = await runStatus(sid);
    if (st === null) {
      if (offlineTries++ < MAX_OFFLINE_TRIES) setTimeout(() => afterTurn(sid), RETRY_MS);
      return;
    }
    offlineTries = 0;
    if (!st.running) return;
    setTimeout(() => { if (cur === sid && !streaming) resume(sid); }, RETRY_MS);
  }

  window.LCAttach = { resume, resumeIfRunning, afterTurn };
})();
