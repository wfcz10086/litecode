import { signal } from './signal.js';
import { apiGet, apiPost } from '../net/api.js';

export const appConfig = signal({});

export async function loadConfig() {
  const r = await fetch('/api/config');
  if (r.ok) appConfig.value = await r.json();
}

export async function saveConfig(patch) {
  await fetch('/api/config', { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(patch) });
  await loadConfig();
}

export async function fetchModels() {
  const r = await apiGet('/api/models');
  if (r.status === 404) throw new Error('后端未接入模型 API');
  if (!r.ok) throw new Error(`请求失败 ${r.status}`);
  return r.json();
}

export async function saveModel(name, payload) {
  const r = await apiPost(`/api/models/${encodeURIComponent(name)}`, payload);
  return r;
}

export async function testModel(name) {
  const r = await apiPost(`/api/models/${encodeURIComponent(name)}/test`, {});
  return r;
}

export async function fetchWechatQR() {
  const r = await apiGet('/api/wechat/qr');
  if (!r.ok) throw new Error(`请求失败 ${r.status}`);
  return r.json();
}

export async function fetchDag() {
  const r = await apiGet('/api/dag');
  if (!r.ok) throw new Error(`请求失败 ${r.status}`);
  return r.json();
}

export async function saveDag(dag) {
  const r = await apiPost('/api/dag', dag);
  if (!r.ok) throw new Error(`保存失败 ${r.status}`);
  return r.json();
}
