/**
 * The last experiment and the last run the user actually looked at.
 *
 * The sidebar's Experiment and Runs entries used to point at `/experiment` and
 * `/runs`, which are the *parameterless* routes: they resolve server-side to the
 * most recently active experiment and the newest run. So clicking "Experiment"
 * after opening one of a run's tabs did not go back to the experiment you were
 * in — it jumped to whichever one the storage tree reports as newest.
 * Remembering the ids here lets those two entries point somewhere the user has
 * actually been.
 *
 * Two rules the callers depend on:
 *
 * - An id is recorded when a page has *successfully loaded* that entity, never
 *   when a link is clicked. A navigation that 404s therefore leaves the memory
 *   pointing at the last thing that really rendered, instead of poisoning the
 *   sidebar with an id that does not resolve.
 * - Every `localStorage` access is wrapped. It throws outright in a private-mode
 *   browser and when the origin's storage quota is full, and a sidebar that
 *   cannot render is a far worse failure than a sidebar that has forgotten
 *   where you were.
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

/**
 * The snapshot `useSyncExternalStore` reads. It has to be a stable value rather
 * than a fresh `localStorage` read, because that hook calls the getter during
 * render and compares by identity.
 */
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

/**
 * Record `id` once it is known. Pass `null` while a page is loading or after it
 * has failed — nothing is written, so the previous memory survives.
 */
export function useRememberVisited(
  entity: RememberedEntity,
  id: string | null | undefined,
): void {
  useEffect(() => {
    if (id) rememberVisited(entity, id);
  }, [entity, id]);
}
