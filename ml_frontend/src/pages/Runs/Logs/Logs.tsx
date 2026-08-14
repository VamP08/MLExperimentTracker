import { useCallback, useEffect, useRef, useState } from 'react';
import './Logs.css';

/**
 * A record of `logs.jsonl` as the API hands it back (DATA-CONTRACT §3.11).
 *
 * `timestamp` is seconds since the run started — the same clock `metrics.jsonl` uses, so a
 * log line and a metric point at 74.1 are the same instant. `absolute_timestamp` is the
 * wall clock, kept for the hover title of the elapsed column.
 */
interface LogRecord {
  timestamp: number;
  absolute_timestamp: number;
  level: string;
  message: string;
  source: string;
}

interface Props {
  runId: string;
}

/**
 * The closed level vocabulary, in severity order (§3.11). The order is a display choice
 * only: the server filters on one level exactly and asserts no hierarchy, so the filter
 * says "Info only" rather than "Info and above" — a control that claimed to rank these
 * would be the UI inventing an ordering the format never recorded.
 */
const LEVELS = ['debug', 'info', 'warning', 'error', 'critical'] as const;

/**
 * Rows per request. A run's log is the largest thing the API can return — 8 MiB at the
 * default budget, tens of thousands of lines — so the tab asks for a page and offers the
 * next one rather than rendering the whole file into the DOM at once.
 */
const PAGE_SIZE = 500;

const Logs = ({ runId }: Props) => {
  const [entries, setEntries] = useState<LogRecord[]>([]);
  const [level, setLevel] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Kept apart from `error`, which replaces the whole tab. A failed *next* page must not
  // throw away the page the user is already reading — it belongs in the footer, next to
  // the button that failed, with the records still on screen.
  const [moreError, setMoreError] = useState<string | null>(null);
  // Whether the last page came back full. The endpoint returns a bare array with no total,
  // so a full page is the only evidence there may be more — and the honest control for
  // that is "Load more", not a page count derived from a number nobody sent.
  const [mayHaveMore, setMayHaveMore] = useState(false);

  // Guards against an out-of-order response overwriting a newer one when the run or the
  // filter changes while a request is still in flight.
  const requestRef = useRef(0);

  const fetchPage = useCallback(
    async (offset: number, wanted: string): Promise<LogRecord[]> => {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(offset),
      });
      if (wanted !== 'all') params.set('level', wanted);

      const response = await fetch(`/api/run/${runId}/logs?${params.toString()}`);
      if (!response.ok) {
        throw new Error(`Request failed with ${response.status}`);
      }
      const data: unknown = await response.json();
      return Array.isArray(data) ? (data as LogRecord[]) : [];
    },
    [runId],
  );

  useEffect(() => {
    const ticket = ++requestRef.current;

    const load = async () => {
      setLoading(true);
      setError(null);
      setMoreError(null);
      try {
        const page = await fetchPage(0, level);
        if (ticket !== requestRef.current) return;
        setEntries(page);
        setMayHaveMore(page.length === PAGE_SIZE);
      } catch (err) {
        if (ticket !== requestRef.current) return;
        setEntries([]);
        setMayHaveMore(false);
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        if (ticket === requestRef.current) setLoading(false);
      }
    };

    load();
  }, [fetchPage, level]);

  const loadMore = async () => {
    const ticket = requestRef.current;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await fetchPage(entries.length, level);
      if (ticket !== requestRef.current) return;
      setEntries((current) => [...current, ...page]);
      setMayHaveMore(page.length === PAGE_SIZE);
    } catch (err) {
      if (ticket !== requestRef.current) return;
      // The button stays: this is a retry, not a dead end.
      setMoreError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      if (ticket === requestRef.current) setLoadingMore(false);
    }
  };

  if (loading) {
    return (
      <div className="logs-page">
        <div className="logs-status">Loading logs...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="logs-page">
        <div className="logs-status logs-status-error">
          Could not load this run&apos;s logs: {error}
        </div>
      </div>
    );
  }

  // An unfiltered empty result is the ordinary state of every run recorded before format
  // 1.2 and of every run that turned capture off. It gets the whole panel with no filter
  // and no download control, because there is nothing to filter and nothing to download —
  // and it must read as a fact about the run, not as a failure of the page.
  if (level === 'all' && entries.length === 0) {
    return (
      <div className="logs-page">
        <div className="logs-status">
          No output recorded for this run. Capture writes <code>logs.jsonl</code> while the
          run is alive; a run tracked with <code>capture_output=False</code>, or one
          recorded before the log file existed, has none.
        </div>
      </div>
    );
  }

  return (
    <div className="logs-page">
      <div className="logs-header">
        <h2>Run Logs</h2>
        <div className="logs-controls">
          <label className="logs-filter-label" htmlFor="logs-level-filter">
            Level
          </label>
          <select
            id="logs-level-filter"
            className="logs-level-filter"
            value={level}
            onChange={(event) => setLevel(event.target.value)}
          >
            <option value="all">All levels</option>
            {LEVELS.map((name) => (
              <option key={name} value={name}>
                {name.charAt(0).toUpperCase() + name.slice(1)} only
              </option>
            ))}
          </select>
          <a
            className="logs-download-button"
            href={`/api/run/${runId}/logs/download`}
            download={`${runId}_logs.txt`}
          >
            Download full log
          </a>
        </div>
      </div>

      {entries.length === 0 ? (
        <div className="logs-status">
          Nothing was recorded at level <strong>{level}</strong> for this run.
        </div>
      ) : (
        <>
          <div className="logs-container">
            {entries.map((entry, index) => (
              <div className="logs-entry" key={`${index}-${entry.timestamp}`}>
                <span
                  className="logs-elapsed"
                  title={formatAbsolute(entry.absolute_timestamp)}
                >
                  {formatElapsed(entry.timestamp)}
                </span>
                <span className={`logs-level logs-level-${levelClass(entry.level)}`}>
                  {String(entry.level ?? '')}
                </span>
                <span className="logs-source">{String(entry.source ?? '')}</span>
                <span className="logs-message">{String(entry.message ?? '')}</span>
              </div>
            ))}
          </div>

          <div className="logs-footer">
            <span className="logs-count">
              {entries.length} {entries.length === 1 ? 'record' : 'records'}
              {mayHaveMore ? ' so far' : ''}
              {moreError && (
                <span className="logs-more-error"> — could not load more: {moreError}</span>
              )}
            </span>
            {mayHaveMore && (
              <button
                className="logs-more-button"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? 'Loading...' : moreError ? 'Retry' : `Load ${PAGE_SIZE} more`}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
};

/**
 * Elapsed run time as `mm:ss.s`, growing an hours field only when there is one. Rendered
 * from `timestamp` because that is the clock the rest of the run is recorded against; the
 * wall clock is a hover away.
 */
const formatElapsed = (seconds: number): string => {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '—';
  const total = Math.max(0, seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = (total % 60).toFixed(1).padStart(4, '0');
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${remainder}`;
  }
  return `${String(minutes).padStart(2, '0')}:${remainder}`;
};

/** Wall clock for the hover title, or nothing at all rather than an invented date. */
const formatAbsolute = (epochSeconds: number): string | undefined => {
  if (typeof epochSeconds !== 'number' || !Number.isFinite(epochSeconds)) return undefined;
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toLocaleString();
};

/**
 * The colour class for a level. A value outside the closed vocabulary is styled neutrally
 * and still shown verbatim: filing an unknown level under `info` would be the page
 * asserting something about a record it does not understand.
 */
const levelClass = (level: string): string => {
  const name = typeof level === 'string' ? level.trim().toLowerCase() : '';
  return (LEVELS as readonly string[]).includes(name) ? name : 'unknown';
};

export default Logs;
