import { useSyncExternalStore } from "react";

/**
 * Colour theme as a small external store. index.html applies the saved theme before first
 * paint. Storage access is wrapped because it throws in private mode.
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
