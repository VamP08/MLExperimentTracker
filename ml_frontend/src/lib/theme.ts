import { useSyncExternalStore } from "react";

/**
 * The colour theme, as one external store read by every control that can change it.
 *
 * `index.html` applies the saved theme before the first paint, so this module only has to
 * keep the attribute, the storage key and the subscribers in step. Storage access is wrapped
 * because it throws in private-mode browsers and on a full quota; a theme that forgets itself
 * on reload is a far smaller failure than a toggle that cannot render.
 */
export type Theme = "light" | "dark";

const KEY = "theme";
const listeners = new Set<() => void>();

function read(): Theme {
  const attr = document.documentElement.getAttribute("data-theme");
  return attr === "dark" ? "dark" : "light";
}

export function setTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    window.localStorage.setItem(KEY, theme);
  } catch {
    // The attribute is already set; only survival across a reload is lost.
  }
  listeners.forEach((listener) => listener());
}

export function toggleTheme(): void {
  setTheme(read() === "dark" ? "light" : "dark");
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, read, () => "light");
}
