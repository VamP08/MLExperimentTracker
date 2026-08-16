/**
 * The single door every API call in this application goes through.
 *
 * There are two builds of this frontend and only this file knows which one it is in:
 *
 * - **Live** (`npm run build`, and the dev server) — `apiFetch` is `fetch`. Nothing below
 *   this line runs, and nothing in `demoData.ts` reaches the bundle.
 * - **Demo** (`VITE_DEMO=1 npm run build`) — there is no server to talk to, so the request
 *   is answered from a snapshot of real responses captured from a real run of the API.
 *
 * The signature is deliberately `fetch`'s. Call sites keep using `res.ok`, `res.status`,
 * `res.json()` and `res.blob()` exactly as before, because the demo path returns a real
 * `Response` object rather than a parsed body — the mode is invisible above this seam, and
 * a component cannot accidentally grow a branch that only works in one of the two builds.
 */

/**
 * Whether this bundle is the static demo.
 *
 * Anything that renders differently in the demo — the banner that says so, above all —
 * reads this. It is `false` in the normal build and folds away with the code it guards.
 */
export const IS_DEMO = import.meta.env.VITE_DEMO === '1';

/**
 * Whether a successful write survives a reload.
 *
 * `true` in the live build, where a PATCH reaches the server and the server reaches disk.
 * `false` in the demo, where the three editable fields — a run's tags, a run's description,
 * an experiment's description — are applied to the in-memory snapshot and returned as 200
 * so the editor behaves like the real one, and are gone on the next page load. Code that
 * wants to say so in the UI reads this rather than `IS_DEMO`, because it is the narrower
 * claim and the one that is actually about the user's edit.
 */
export const WRITES_PERSIST = !IS_DEMO;

/**
 * Make an API request.
 *
 * `path` is the same relative `/api/...` string the live build sends; the demo resolves it
 * against the snapshot and a path that was never captured comes back as a real 404 with
 * the `{"message": ...}` body the server sends, because the components already handle
 * exactly that.
 */
export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  // Written against `import.meta.env` rather than `IS_DEMO` on purpose: Vite substitutes a
  // string literal here, the condition becomes statically false in the live build, and the
  // dynamic import inside a dead branch is what keeps the adapter and the snapshot out of
  // the normal bundle entirely. Reading the constant instead would leave the bundler to
  // prove the fold, and a live build that ships the snapshot is a bug.
  if (import.meta.env.VITE_DEMO === '1') {
    return import('./demoData').then((demo) => demo.resolveDemoRequest(path, init));
  }
  return fetch(path, init);
}

/**
 * Where the demo's data came from, or `null` in the live build.
 *
 * Kept behind the same dynamic import as the adapter so that asking the question from a
 * component does not drag the snapshot into the live bundle. The demo has to say it is a
 * demo, and it should be able to say when the numbers on screen were measured.
 */
export function demoSnapshotInfo(): Promise<{ capturedAt: string | null; source: string } | null> {
  if (import.meta.env.VITE_DEMO === '1') {
    return import('./demoData').then((demo) => demo.snapshotInfo());
  }
  return Promise.resolve(null);
}
