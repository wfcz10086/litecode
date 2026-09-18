import { signal } from './signal.js';

export const streaming = signal(false);
export const sending = signal(false);
export let curReader = null;
export function setCurReader(r) { curReader = r; }
export const lastUsage = signal(null);
