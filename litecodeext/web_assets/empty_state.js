// 新会话欢迎区的建议卡片: 从 SUGGESTIONS 渲染到 #empty .sugs, 点击填入输入框 (不自动发送)。
// 增删卡片只改数组; 删掉本文件的 <script> 即回退到 web_ui.html 里写死的四个按钮。
(function () {
  const SUGGESTIONS = [
    { icon: '📄', i18n: 'sug.pdf.label', label: '总结 PDF',
      zh: '我会用输入框右边的 📎 上传一份 PDF,读完请给我 5 条要点和一句话结论',
      en: "I'll upload a PDF with the 📎 button. Read it and give me 5 key points plus a one-line takeaway" },
    { icon: '⏰', i18n: 'sug.timer.label', label: '每日简报',
      zh: '帮我建一个定时任务:每天早上 9 点搜索 AI 领域的新闻,整理成 5 条简报',
      en: 'Create a scheduled task: every morning at 9:00, search AI news and summarize it into a 5-item briefing' },
    { icon: '🔍', i18n: 'sug.search.label', label: '深度搜索',
      zh: '深度搜索一下:最近一周 AI Agent 领域有哪些重要进展?每条附上出处',
      en: 'Deep search: what were the important AI agent developments this past week? Cite a source for each' },
    { icon: '🔀', i18n: 'sug.dag.label', label: '搭一条流水线',
      zh: '帮我搭一条流水线:先搜索资料,再写一篇 800 字综述,最后检查错别字',
      en: 'Build me a pipeline: research the topic, write an 800-word overview, then proofread it' },
    { icon: '🖼️', i18n: 'sug.image.label', label: '看懂截图',
      zh: '我发一张截图,帮我看看里面写了什么、有没有问题',
      en: "I'll send a screenshot. Tell me what it says and whether anything looks wrong" },
    { icon: '🗂️', i18n: 'sug.code.label', label: '分析项目',
      zh: '扫描当前工作区的项目,写一份架构说明',
      en: 'Scan the project in the current workspace and write an architecture overview' },
    { icon: '💞', i18n: 'sug.chat.label', label: '陪我聊聊',
      zh: '今天有点累,陪我聊会儿',
      en: "I'm a bit tired today. Keep me company for a while" },
  ];

  function render() {
    const empty = document.getElementById('empty');
    if (!empty || empty.dataset.sugRendered) return;
    empty.dataset.sugRendered = '1';
    let box = empty.querySelector('.sugs');
    if (!box) {
      box = document.createElement('div');
      box.className = 'sugs';
      empty.appendChild(box);
    }
    box.textContent = '';
    SUGGESTIONS.forEach(s => {
      const b = document.createElement('button');
      b.className = 'sg';
      b.type = 'button';
      const icon = document.createElement('span');
      icon.textContent = s.icon + ' ';
      const text = document.createElement('span');
      text.setAttribute('data-i18n', s.i18n);
      text.textContent = s.label;
      b.append(icon, text);
      b.addEventListener('click', () => {
        const lang = (window.I18N && I18N.locale) || 'zh';
        const prompt = s[lang] || s.zh;
        if (typeof window.sug === 'function') window.sug(prompt);
      });
      box.appendChild(b);
    });
    if (window.I18N && I18N.applyDOM) I18N.applyDOM(box);
  }

  // newChat / delS / renderMsgs 会用不含 .sugs 的 HTML 重建 #empty, 盯住 #msgs 补渲染
  function start() {
    render();
    const msgs = document.getElementById('msgs');
    if (msgs) new MutationObserver(render).observe(msgs, { childList: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
