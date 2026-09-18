// 极简 Signal 实现 — 无依赖，原生 JS
let _tracking = null;

export function signal(value) {
  const subs = new Set();
  return {
    get value() {
      if (_tracking) subs.add(_tracking);
      return value;
    },
    set value(v) {
      value = v;
      subs.forEach(fn => fn());
    },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
  };
}

export function effect(fn) {
  const run = () => { _tracking = run; try { fn(); } finally { _tracking = null; } };
  run();
}

export function computed(fn) {
  const s = signal(undefined);
  effect(() => { s.value = fn(); });
  return { get value() { return s.value; } };
}
