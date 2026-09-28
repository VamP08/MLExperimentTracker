import { useSyncExternalStore } from "react";

/** Whether the nav drawer is open. Only matters on narrow screens. */
let open = false;
const listeners = new Set<() => void>();

export function setNavOpen(next: boolean): void {
  open = next;
  listeners.forEach((listener) => listener());
}

export function useNavOpen(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => open,
    () => false,
  );
}
