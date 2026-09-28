/**
 * Last experiment and run the user actually loaded, so the sidebar can link back to them
 * (`/experiment` and `/runs` resolve to the newest instead). Ids are only recorded after a
 * successful load, and localStorage access is wrapped because it can throw.
 */

import { useEffect, useSyncExternalStore } from "react";

const STORAGE_KEYS = {
  experiment: "lastExperimentId",
  run: "lastRunId",
} as const;

export type RememberedEntity = keyof typeof STORAGE_KEYS;

function readStored(entity: RememberedEntity): string | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEYS[entity]);
    return value ? value : null;
  } catch {
    return null;
  }
}

function writeStored(entity: RememberedEntity, id: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEYS[entity], id);
  } catch {
    // Storage is unavailable or full. The in-memory copy below still serves
    // this session; only survival across a reload is lost.
  }
}

/** Cached value for useSyncExternalStore, which needs a stable snapshot between reads. */
const cache: Record<RememberedEntity, string | null> = {
  experiment: readStored("experiment"),
  run: readStored("run"),
};

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Record an id, after the page holding it has loaded. */
export function rememberVisited(entity: RememberedEntity, id: string): void {
  if (!id || cache[entity] === id) return;
  cache[entity] = id;
  writeStored(entity, id);
  listeners.forEach((listener) => listener());
}

/** The remembered id, re-rendering the caller when it changes. */
export function useLastVisited(entity: RememberedEntity): string | null {
  return useSyncExternalStore(
    subscribe,
    () => cache[entity],
    () => null,
  );
}

/** Record `id` once known. Pass null while loading or on error to keep the previous value. */
export function useRememberVisited(
  entity: RememberedEntity,
  id: string | null | undefined,
): void {
  useEffect(() => {
    if (id) rememberVisited(entity, id);
  }, [entity, id]);
}
