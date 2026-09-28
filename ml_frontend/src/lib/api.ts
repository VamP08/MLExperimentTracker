/**
 * Every API call goes through here. In the normal build `apiFetch` is `fetch`; in the demo
 * build (VITE_DEMO=1) requests are answered from a captured snapshot as real Responses.
 */

/** True in the static demo build. Folds to false in the normal build. */
export const IS_DEMO = import.meta.env.VITE_DEMO === '1';

/** Whether a write survives a reload. False in the demo, where edits only touch memory. */
export const WRITES_PERSIST = !IS_DEMO;

/** Make an API request. In the demo, uncaptured paths 404 with the server's error body. */
export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  // Check import.meta.env directly, not IS_DEMO, so Vite can drop the dynamic import
  // (and the snapshot) from the normal bundle.
  if (import.meta.env.VITE_DEMO === '1') {
    return import('./demoData').then((demo) => demo.resolveDemoRequest(path, init));
  }
  return fetch(path, init);
}

/**
 * Where the demo data came from, or null in the normal build. Behind the same dynamic
 * import so the snapshot stays out of the normal bundle.
 */
export function demoSnapshotInfo(): Promise<{ capturedAt: string | null; source: string } | null> {
  if (import.meta.env.VITE_DEMO === '1') {
    return import('./demoData').then((demo) => demo.snapshotInfo());
  }
  return Promise.resolve(null);
}
