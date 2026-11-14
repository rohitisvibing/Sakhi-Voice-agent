"use client";

/**
 * One payment sheet for the whole app. Anything that hits "subscribe to continue" calls
 * `openPaywall()`, which resolves true once the subscription is active (or false if closed).
 */
type Listener = () => void;

let open = false;
let waiters: ((ok: boolean) => void)[] = [];
const listeners = new Set<Listener>();
const notify = () => listeners.forEach((l) => l());

export function openPaywall(): Promise<boolean> {
  open = true;
  notify();
  return new Promise((resolve) => waiters.push(resolve));
}

export function closePaywall(ok: boolean) {
  open = false;
  const w = waiters;
  waiters = [];
  w.forEach((resolve) => resolve(ok));
  notify();
}

export const paywallStore = {
  subscribe(l: Listener) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  get: () => open,
  getServer: () => false,
};
