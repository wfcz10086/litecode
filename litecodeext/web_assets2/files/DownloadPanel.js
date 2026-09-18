import { createPreviewCard } from './PreviewCard.js';
import { typeOf } from '../utils/fileTypes.js';

const PATH_RE = /(?:\/[\w\-\.\/]+\.(?:png|jpg|jpeg|gif|webp|bmp|svg|avif|mp4|webm|mov|ogv|mp3|wav|ogg|flac|m4a|pdf|html|htm|md|txt|log|json|yaml|yml|toml|ini|csv|tsv|xml|py|js|ts|jsx|tsx|vue|go|rs|c|cpp|h|java|sh|bash|zsh|sql|css|scss|doc|docx|xls|xlsx|ppt|pptx|zip|tar|gz|bz2|xz|7z|rar))/gi;

export function scanPaths(text) {
  const matches = [...(text || '').matchAll(PATH_RE)].map(m => m[0]);
  return [...new Set(matches)];
}

function fileName(path) {
  return (path || '').split('/').pop().split('?')[0] || path;
}

function makeMiniCard(path) {
  const { icon } = typeOf(path);
  const card = document.createElement('div');
  card.className = 'dl-panel__mini-card';

  const ic = document.createElement('span');
  ic.className = 'dl-panel__icon';
  ic.textContent = icon;

  const name = document.createElement('span');
  name.className = 'dl-panel__name';
  name.textContent = fileName(path);

  const preview = createPreviewCard(path);
  preview.className += ' dl-panel__preview';

  card.appendChild(ic);
  card.appendChild(name);
  card.appendChild(preview);
  return card;
}

export function createDownloadPanel(paths) {
  if (!paths || paths.length === 0) return null;

  const panel = document.createElement('div');
  panel.className = 'dl-panel';

  const title = document.createElement('div');
  title.className = 'dl-panel__title';
  title.textContent = `文件 (${paths.length})`;
  panel.appendChild(title);

  const grid = document.createElement('div');
  grid.className = 'dl-panel__grid';
  for (const p of paths) {
    grid.appendChild(makeMiniCard(p));
  }
  panel.appendChild(grid);

  return panel;
}
