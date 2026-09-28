import { useCallback, useEffect, useRef, useState } from 'react';
import { FiChevronLeft, FiChevronRight, FiDownload } from 'react-icons/fi';
import './Logs.css';
import '../Overview/Overview.css';
import { apiFetch, IS_DEMO } from '../../../lib/api';
import { downloadFrom } from '../../../lib/download';

/**
 * One `logs.jsonl` record as the API returns it. `timestamp` is seconds since run start (same
 * clock as metrics.jsonl); `absolute_timestamp` is wall clock, used for the hover title.
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
 * Closed level vocabulary. The server filters on one exact level, so the filter says
 * "Info only", not "and above".
 */
const LEVELS = ['debug', 'info', 'warning', 'error', 'critical'] as const;

/** Rows per request. A log can be tens of thousands of lines, so page it. */
const PAGE_SIZE = 500;

const DEMO_DOWNLOAD_NOTE =
  'Not available in the static demo: the file is streamed from the server, and there is no server. The log shown here is the same content.';

const Logs = ({ runId }: Props) => {
  const [entries, setEntries] = useState<LogRecord[]>([]);
  const [offset, setOffset] = useState(0);
  const [level, setLevel] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [paging, setPaging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Separate from `error` (which replaces the tab) so a failed page turn keeps the current rows.
  const [pageError, setPageError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  // No total from the endpoint, so a full page is the only hint there may be more.
  const [mayHaveMore, setMayHaveMore] = useState(false);

  // Ignore out-of-order responses when the run or filter changes mid-request.
  const requestRef = useRef(0);

  const fetchPage = useCallback(
    async (from: number, wanted: string): Promise<LogRecord[]> => {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(from),
      });
      if (wanted !== 'all') params.set('level', wanted);

      const response = await apiFetch(`/api/run/${runId}/logs?${params.toString()}`);
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
      setPageError(null);
      setOffset(0);
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

  const goTo = async (from: number) => {
    const ticket = requestRef.current;
    setPaging(true);
    setPageError(null);
    try {
      const page = await fetchPage(from, level);
      if (ticket !== requestRef.current) return;
      if (page.length === 0 && from > 0) {
        // Last page was full but nothing follows it: stay put and stop offering Next.
        setMayHaveMore(false);
        return;
      }
      setEntries(page);
      setOffset(from);
      setMayHaveMore(page.length === PAGE_SIZE);
      document.getElementById('logs-title')?.scrollIntoView({ block: 'nearest' });
    } catch (err) {
      if (ticket !== requestRef.current) return;
      // Keep the buttons so the user can retry.
      setPageError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      if (ticket === requestRef.current) setPaging(false);
    }
  };

  const download = async () => {
    if (IS_DEMO) return;
    setDownloadError(null);
    try {
      await downloadFrom(`/api/run/${runId}/logs/download`, `${runId}_logs.txt`);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'Download failed');
    }
  };

  // No logs is normal for runs before format 1.2 or with capture off. Show a plain
  // message, no filter or download.
  const nothingCaptured = !loading && !error && level === 'all' && offset === 0 && entries.length === 0;
  const seen = offset + entries.length;
  const showControls = !loading && !error && !nothingCaptured;

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="logs-title">
        <div className="panel-head">
          <h2 id="logs-title">Logs</h2>
          {showControls && (
            // A total is known only once the last page has been reached; before that, "at least".
            <span className="sub num">
              {mayHaveMore ? `${seen}+` : seen} {seen === 1 && !mayHaveMore ? 'line' : 'lines'}
            </span>
          )}
          <span className="spacer" />
          {showControls && (
            <div className="logs-toolbar">
              <label className="sr-only" htmlFor="logs-level-filter">
                Level
              </label>
              <select
                id="logs-level-filter"
                className="select"
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
              {/* Streamed from disk, so the demo shows it disabled. aria-disabled keeps the title reachable. */}
              <button
                type="button"
                className="btn logs-download"
                onClick={download}
                aria-disabled={IS_DEMO || undefined}
                title={IS_DEMO ? DEMO_DOWNLOAD_NOTE : undefined}
              >
                <FiDownload />
                Download full log
              </button>
            </div>
          )}
          {downloadError && (
            <span className="logs-error" role="alert">
              Could not download the log: {downloadError}
            </span>
          )}
        </div>

        {loading ? (
          <div className="state">Loading logs…</div>
        ) : error ? (
          <div className="state error">Could not load this run&apos;s logs: {error}</div>
        ) : nothingCaptured ? (
          <div className="state">
            <h3>No output recorded</h3>
            <p>
              Capture writes <code>logs.jsonl</code> while the run is alive; a run tracked with{' '}
              <code>capture_output=False</code>, or one recorded before the log file existed, has none.
            </p>
          </div>
        ) : entries.length === 0 ? (
          <div className="state">
            Nothing was recorded at level <strong>{level}</strong> for this run.
          </div>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table logs-table">
                <thead>
                  <tr>
                    <th className="r">Time (s)</th>
                    <th>Level</th>
                    <th>Source</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry, index) => (
                    <tr key={`${offset + index}-${entry.timestamp}`}>
                      <td className="r mono logs-time" title={formatAbsolute(entry.absolute_timestamp)}>
                        {formatElapsed(entry.timestamp)}
                      </td>
                      <td>
                        <span className={`lvl lvl-${levelClass(entry.level)}`}>{String(entry.level ?? '')}</span>
                      </td>
                      <td>
                        <span className={`src src-${String(entry.source ?? '')}`}>{String(entry.source ?? '')}</span>
                      </td>
                      {/* Keep newlines; a traceback is one multi-line record. */}
                      <td className="mono logs-message">{String(entry.message ?? '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="logs-pager">
              <span className="muted num">
                Lines {offset + 1}–{seen}
              </span>
              {pageError && (
                <span className="logs-error" role="alert">
                  Could not load that page: {pageError}
                </span>
              )}
              <span className="spacer" />
              <button
                type="button"
                className="btn"
                onClick={() => goTo(Math.max(0, offset - PAGE_SIZE))}
                disabled={paging || offset === 0}
              >
                <FiChevronLeft />
                Previous
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => goTo(offset + PAGE_SIZE)}
                disabled={paging || !mayHaveMore}
              >
                Next
                <FiChevronRight />
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
};

/** Seconds since the run started, in the same form as the overview's log tail. */
const formatElapsed = (seconds: number): string => {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '—';
  return `+${Math.max(0, seconds).toFixed(3)}`;
};

/** Wall clock for the hover title, or nothing. */
const formatAbsolute = (epochSeconds: number): string | undefined => {
  if (typeof epochSeconds !== 'number' || !Number.isFinite(epochSeconds)) return undefined;
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toLocaleString();
};

/** Colour class for a level. Unknown levels get a neutral style and are shown as-is. */
const levelClass = (level: string): string => {
  const name = typeof level === 'string' ? level.trim().toLowerCase() : '';
  return (LEVELS as readonly string[]).includes(name) ? name : 'unknown';
};

export default Logs;
