// text.js — 从 msg.content (string / array / block) 提取纯文本.

export function extractText(content) {
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
