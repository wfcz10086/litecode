// 画布分屏的纯逻辑: 哪些代码块进画布、标题、下载文件名。无 DOM, node 可直接测 (tests/test_canvas_core.py)。
(function (root) {
  const MIN_LINES = 20;        // 普通代码块: 行数达到这个才收进画布
  const MIN_CHARS = 1500;      // 或字符数达到这个 (单行超长的 JSON 之类)
  const MIN_LINES_RENDER = 8;  // html/svg 能预览, 门槛放低

  const EXT = {
    python: 'py', py: 'py', javascript: 'js', js: 'js', typescript: 'ts', ts: 'ts', tsx: 'tsx', jsx: 'jsx',
    html: 'html', xml: 'xml', svg: 'svg', css: 'css', json: 'json', yaml: 'yaml', yml: 'yml', toml: 'toml',
    bash: 'sh', sh: 'sh', shell: 'sh', zsh: 'sh', sql: 'sql', go: 'go', rust: 'rs', java: 'java', kotlin: 'kt',
    c: 'c', cpp: 'cpp', 'c++': 'cpp', csharp: 'cs', cs: 'cs', php: 'php', ruby: 'rb', swift: 'swift',
    markdown: 'md', md: 'md', dockerfile: 'Dockerfile', ini: 'ini', lua: 'lua', r: 'r', vue: 'vue',
  };

  function langOf(className) {
    const m = /(?:^|\s)language-([\w+#-]+)/.exec(className || '');
    return m ? m[1].toLowerCase() : '';
  }

  // 有些模型给 html 块标 xml 或不标语言, 按内容兜一下
  function kindOf(lang, text) {
    const head = (text || '').trimStart().slice(0, 200).toLowerCase();
    if (lang === 'svg' || (head.startsWith('<svg') && (!lang || lang === 'xml'))) return 'svg';
    if (lang === 'html' || ((!lang || lang === 'xml') && (head.startsWith('<!doctype html') || head.startsWith('<html')))) return 'html';
    if (lang === 'markdown' || lang === 'md') return 'markdown';
    return 'code';
  }

  function lineCount(text) {
    if (!text) return 0;
    return text.replace(/\n+$/, '').split('\n').length;
  }

  function shouldCanvas(lang, text) {
    const n = lineCount(text);
    const kind = kindOf(lang, text);
    if (kind === 'html' || kind === 'svg') return n >= MIN_LINES_RENDER;
    return n >= MIN_LINES || (text || '').length >= MIN_CHARS;
  }

  // 取标题: html 的 <title> → 首行注释里的文件名 → 语言名
  const FILE_RE = /([\w.-]+\.[a-z0-9]{1,8})\b/i;
  const COMMENT_RE = /^\s*(?:#|\/\/|--|\/\*+|<!--|;)\s*(.*?)\s*(?:\*\/|-->)?\s*$/;
  function titleOf(lang, text) {
    const t = /<title[^>]*>([^<]{1,80})<\/title>/i.exec(text || '');
    if (t && t[1].trim()) return t[1].trim();
    const lines = (text || '').split('\n').slice(0, 3);
    for (const ln of lines) {
      if (/^#!/.test(ln)) continue;
      const c = COMMENT_RE.exec(ln);
      if (!c || !c[1]) continue;
      const f = FILE_RE.exec(c[1]);
      if (f && !/^\d+(\.\d+)+$/.test(f[1])) return f[1];
    }
    const kind = kindOf(lang, text);
    if (kind === 'html') return 'HTML 页面';
    if (kind === 'svg') return 'SVG 图形';
    return (lang ? lang.toUpperCase() + ' ' : '') + '代码';
  }

  function extOf(lang, text) {
    const kind = kindOf(lang, text);
    if (kind === 'html') return 'html';
    if (kind === 'svg') return 'svg';
    return EXT[lang] || 'txt';
  }

  // 下载名: 标题本身像文件名就用它, 否则 canvas.<ext>
  function fileName(lang, text) {
    const title = titleOf(lang, text);
    if (/^[\w.-]+\.[a-z0-9]{1,8}$/i.test(title)) return title;
    const ext = extOf(lang, text);
    return ext === 'Dockerfile' ? ext : 'canvas.' + ext;
  }

  const api = { MIN_LINES, MIN_CHARS, MIN_LINES_RENDER, langOf, kindOf, lineCount, shouldCanvas, titleOf, extOf, fileName };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CanvasCore = api;
})(typeof window !== 'undefined' ? window : globalThis);
