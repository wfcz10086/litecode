export const FILE_TYPES = [
  { ext: ['png','jpg','jpeg','gif','webp','bmp','svg','avif'], mime: 'image/*', icon: '🖼️', inline: 'img' },
  { ext: ['mp4','webm','mov','ogv'], mime: 'video/*', icon: '🎬', inline: 'video' },
  { ext: ['mp3','wav','ogg','flac','m4a'], mime: 'audio/*', icon: '🎵', inline: 'audio' },
  { ext: ['pdf'], mime: 'application/pdf', icon: '📕', inline: 'iframe' },
  { ext: ['html','htm'], mime: 'text/html', icon: '🌐', inline: 'iframe' },
  { ext: ['md','txt','log','json','yaml','yml','toml','ini','csv','tsv','xml'], mime: 'text/plain', icon: '📄', inline: 'text' },
  { ext: ['py','js','ts','jsx','tsx','vue','go','rs','c','cpp','h','java','sh','bash','zsh','sql','css','scss'], mime: 'text/plain', icon: '📜', inline: 'text' },
  { ext: ['doc','docx','xls','xlsx','ppt','pptx'], mime: 'application/*', icon: '📊', inline: 'office' },
  { ext: ['zip','tar','gz','bz2','xz','7z','rar'], mime: 'application/*', icon: '📦', inline: null },
];

export function extOf(path) {
  const name = (path || '').split('/').pop().split('?')[0];
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : '';
}

export function typeOf(path) {
  const ext = extOf(path);
  for (const t of FILE_TYPES) {
    if (t.ext.includes(ext)) return { ext, icon: t.icon, mime: t.mime, inline: t.inline };
  }
  return { ext: ext || '?', icon: '📎', mime: 'application/octet-stream', inline: null };
}
