async function apiFetch(url, opts = {}) {
  const r = await fetch(url, opts);
  if (r.status === 401) { window.location.href = '/login'; throw new Error('Unauthorized'); }
  return r;
}

export async function apiGet(url) { return apiFetch(url); }
export async function apiPost(url, body) {
  return apiFetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
}
export async function apiDelete(url) { return apiFetch(url, { method: 'DELETE' }); }
export async function apiPatch(url, body) {
  return apiFetch(url, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
}
