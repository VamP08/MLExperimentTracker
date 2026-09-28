import { useSyncExternalStore } from "react";

/**
 * Whether the navigation drawer is open. Only narrow screens have a drawer: on a wide one the
 * sidebar is always there and this value is ignored by the stylesheet.
 */
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
