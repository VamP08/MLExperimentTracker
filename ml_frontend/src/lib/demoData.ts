/**
 * Answers API requests from demo/snapshot.json (real responses captured from `mlexp demo`).
 * Demo build only. Missing paths 404. Query strings aren't part of the key, so logs are
 * filtered and paged here the same way the server does it.
 */

import rawSnapshot from '../demo/snapshot.json?raw';

/** One captured response. `json` or `text`, not both. */
interface SnapshotEntry {
  /** Defaults to 200. */
  status?: number;
  /** Parsed body for JSON routes. */
  json?: unknown;
  /** Raw body for the CSV export and other non-JSON routes. */
  text?: string;
  /** Overrides the response's `Content-Type`. */
  contentType?: string;
}

interface DemoSnapshot {
  /** Capture time (ISO-8601), or null. */
  capturedAt: string | null;
  /** One line on how it was captured, for the banner. */
  source: string;
  routes: Record<string, SnapshotEntry>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** Imported as text and parsed once; faster than a huge object literal. */
const snapshot: DemoSnapshot = (() => {
  const empty: DemoSnapshot = { capturedAt: null, source: '', routes: {} };
  try {
    const value: unknown = JSON.parse(rawSnapshot);
    if (!isRecord(value)) return empty;
    return {
      capturedAt: typeof value.capturedAt === 'string' ? value.capturedAt : null,
      source: typeof value.source === 'string' ? value.source : '',
      routes: isRecord(value.routes) ? (value.routes as Record<string, SnapshotEntry>) : {},
    };
  } catch {
    // A bad snapshot just makes every route 404. Don't crash the page.
    return empty;
  }
})();

/** Decode path keys so encoded and raw ids (`my%20project` vs `my project`) hit the same entry. */
const routes = new Map<string, SnapshotEntry>();
for (const [key, entry] of Object.entries(snapshot.routes)) {
  if (isRecord(entry)) routes.set(normaliseKey(key), entry as SnapshotEntry);
}

function decodeSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    // Treat a stray `%` as a literal.
    return segment;
  }
}

function normalisePath(pathname: string): string {
  const decoded = pathname.split('/').map(decodeSegment).join('/');
  return decoded.length > 1 && decoded.endsWith('/') ? decoded.slice(0, -1) : decoded;
}

/** Query parameters sorted, so the key does not depend on the order a caller wrote them. */
function canonicalQuery(search: string): string {
  const params = [...new URLSearchParams(search).entries()];
  params.sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));
  return params.map(([name, value]) => `${name}=${value}`).join('&');
}

function normaliseKey(key: string): string {
  const cut = key.indexOf('?');
  if (cut === -1) return normalisePath(key);
  const query = canonicalQuery(key.slice(cut + 1));
  const path = normalisePath(key.slice(0, cut));
  return query ? `${path}?${query}` : path;
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function notFound(pathname: string): Response {
  // Same error shape as the server (`server/app.py`, `_register_errors`).
  return jsonResponse({ message: `No demo data was captured for ${pathname}` }, 404);
}

function toResponse(entry: SnapshotEntry): Response {
  const status = typeof entry.status === 'number' ? entry.status : 200;
  if (typeof entry.text === 'string') {
    return new Response(entry.text, {
      status,
      headers: { 'Content-Type': entry.contentType ?? 'text/plain; charset=utf-8' },
    });
  }
  return new Response(JSON.stringify(entry.json ?? null), {
    status,
    headers: { 'Content-Type': entry.contentType ?? 'application/json' },
  });
}

// --------------------------------------------------------------------------------------
// Reads
// --------------------------------------------------------------------------------------

const LOGS_PATH = /^\/api\/run\/[^/]+\/logs$/;

/** Closed level vocabulary, as in `storage.read_logs`. */
const LOG_LEVELS = new Set(['debug', 'info', 'warning', 'error', 'critical']);

function toInt(value: string | null, fallback: number): number {
  if (value === null) return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

/**
 * Filter by `level`, then apply `offset` and `limit`, same as `Storage.read_logs`.
 * An unknown level matches nothing. Offsets are clamped and a limit <= 0 returns no rows.
 */
function pageLogs(body: unknown, params: URLSearchParams): unknown[] {
  let records: unknown[] = Array.isArray(body) ? body : [];

  const level = params.get('level');
  if (level !== null) {
    const wanted = level.trim().toLowerCase();
    records = LOG_LEVELS.has(wanted)
      ? records.filter(
          (record) =>
            isRecord(record) &&
            typeof record.level === 'string' &&
            record.level.trim().toLowerCase() === wanted,
        )
      : [];
  }

  const start = Math.max(0, toInt(params.get('offset'), 0));
  const limit = params.get('limit');
  if (limit === null) return records.slice(start);
  const count = toInt(limit, 0);
  return count <= 0 ? [] : records.slice(start, start + count);
}

// --------------------------------------------------------------------------------------
// Writes
// --------------------------------------------------------------------------------------

const RUN_TAGS = /^\/api\/run\/([^/]+)\/tags$/;
const RUN_DESCRIPTION = /^\/api\/run\/([^/]+)\/description$/;
const EXPERIMENT_DESCRIPTION = /^\/api\/experiment\/([^/]+)$/;

function readBody(init?: RequestInit): Record<string, unknown> {
  if (typeof init?.body !== 'string') return {};
  try {
    const value: unknown = JSON.parse(init.body);
    return isRecord(value) ? value : {};
  } catch {
    // Same as `_body` on the server: an unparseable body is treated as empty.
    return {};
  }
}

/**
 * Apply a field to every copy of a record in the snapshot (run, experiment runs, dashboard)
 * so an edit shows up everywhere. Only writes keys that already exist.
 */
function patchEverywhere(id: string, field: string, value: unknown): void {
  const visit = (node: unknown, depth: number): void => {
    if (depth > 6 || node === null || typeof node !== 'object') return;
    if (Array.isArray(node)) {
      for (const item of node) visit(item, depth + 1);
      return;
    }
    const record = node as Record<string, unknown>;
    if (record._id === id && field in record) record[field] = value;
    for (const child of Object.values(record)) visit(child, depth + 1);
  };

  for (const entry of routes.values()) visit(entry.json, 0);
}

/** The canonical entry for a resource, or `undefined` if it was never captured. */
function resourceBody(path: string): Record<string, unknown> | undefined {
  const entry = routes.get(path);
  return entry && isRecord(entry.json) ? entry.json : undefined;
}

function writeRunTags(runId: string, body: Record<string, unknown>): Response {
  const tags = body.tags;
  if (!Array.isArray(tags)) return jsonResponse({ message: 'Tags must be an array' }, 400);

  const run = resourceBody(`/api/run/${runId}`);
  if (!run) return jsonResponse({ message: 'Run not found' }, 404);

  run.tags = tags;
  patchEverywhere(runId, 'tags', tags);
  return jsonResponse({ message: 'Tags updated', tags }, 200);
}

function writeDescription(
  path: string,
  id: string,
  missing: string,
  body: Record<string, unknown>,
): Response {
  const raw = body.description;
  if (raw !== undefined && raw !== null && typeof raw !== 'string') {
    return jsonResponse({ message: 'Description must be a string' }, 400);
  }
  // Missing key means empty description, same as the server.
  const description = typeof raw === 'string' ? raw : '';

  const resource = resourceBody(path);
  if (!resource) return jsonResponse({ message: missing }, 404);

  resource.description = description;
  patchEverywhere(id, 'description', description);
  return jsonResponse({ message: 'Description updated', description }, 200);
}

function applyWrite(pathname: string, init?: RequestInit): Response {
  const body = readBody(init);

  const tags = RUN_TAGS.exec(pathname);
  if (tags) return writeRunTags(tags[1], body);

  const runDescription = RUN_DESCRIPTION.exec(pathname);
  if (runDescription) {
    const runId = runDescription[1];
    return writeDescription(`/api/run/${runId}`, runId, 'Run not found', body);
  }

  const experiment = EXPERIMENT_DESCRIPTION.exec(pathname);
  if (experiment) {
    const experimentId = experiment[1];
    return writeDescription(
      `/api/experiment/${experimentId}`,
      experimentId,
      'Experiment not found',
      body,
    );
  }

  return notFound(pathname);
}

// --------------------------------------------------------------------------------------
// Entry point
// --------------------------------------------------------------------------------------

/**
 * Resolve one request against the snapshot. Writes change the in-memory copy and return 200;
 * a reload restores the captured state.
 */
export function resolveDemoRequest(path: string, init?: RequestInit): Response {
  const method = (init?.method ?? 'GET').toUpperCase();
  const cut = path.indexOf('?');
  const pathname = normalisePath(cut === -1 ? path : path.slice(0, cut));
  const search = cut === -1 ? '' : path.slice(cut + 1);

  if (method === 'PATCH') return applyWrite(pathname, init);
  if (method !== 'GET' && method !== 'HEAD') {
    return jsonResponse({ message: `${method} is not available in the demo build` }, 405);
  }

  const query = canonicalQuery(search);
  if (query) {
    const exact = routes.get(`${pathname}?${query}`);
    if (exact) return toResponse(exact);
  }

  const entry = routes.get(pathname);
  if (!entry) return notFound(pathname);

  if (LOGS_PATH.test(pathname)) {
    const status = typeof entry.status === 'number' ? entry.status : 200;
    return jsonResponse(pageLogs(entry.json, new URLSearchParams(search)), status);
  }

  return toResponse(entry);
}

/** When and how the snapshot was captured, for the banner. */
export function snapshotInfo(): { capturedAt: string | null; source: string } {
  return { capturedAt: snapshot.capturedAt, source: snapshot.source };
}
