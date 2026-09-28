import { useEffect, useState } from "react";
import SeriesChart from "../../../components/Charts/SeriesChart";
import type { SeriesPoint } from "../../../components/Charts/SeriesChart";
import { apiFetch } from "../../../lib/api";
import "./Overview.css";

export interface LogRecord {
  timestamp?: number;
  level?: string;
  source?: string;
  message?: string;
}

interface Series {
  name: string;
  data: SeriesPoint[];
}

interface OverviewProps {
  runId: string;
  parameters: Record<string, unknown>;
  logs: LogRecord[] | null;
  logsCapped: boolean;
  onOpenTab: (tab: "run-params" | "logs" | "metrics") => void;
}

/** Most recent lines shown here; the Logs tab has the rest. */
const LOG_TAIL = 12;

function paramValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Run at a glance: metric series, hyperparameters and log tail. Latest values come from summary.json. */
const Overview = ({ runId, parameters, logs, logsCapped, onOpenTab }: OverviewProps) => {
  const [series, setSeries] = useState<Series[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSeries(null);
    setError(null);
    apiFetch(`/api/run/${runId}/metrics`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);
        const body = await res.json();
        if (!cancelled) setSeries(Array.isArray(body) ? body : []);
      })
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : "Unknown error"));
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const steps = series?.flatMap((s) => s.data.map((d) => d.step)).filter((s): s is number => typeof s === "number") ?? [];
  const stepRange = steps.length ? `steps ${Math.min(...steps)}–${Math.max(...steps)}` : "";
  const paramEntries = Object.entries(parameters);
  const tail = logs ? logs.slice(-LOG_TAIL) : [];

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="ov-metrics">
        <div className="panel-head">
          <h2 id="ov-metrics">Metrics</h2>
          {series && series.length > 0 && (
            <span className="sub num">
              {series.length} {series.length === 1 ? "series" : "series"} · {stepRange}
            </span>
          )}
          <span className="spacer" />
          {series && series.length > 0 && (
            <button type="button" className="link" onClick={() => onOpenTab("metrics")}>
              Summary statistics
            </button>
          )}
        </div>
        {error ? (
          <div className="state error">Could not load the metric series: {error}</div>
        ) : series === null ? (
          <div className="state">Loading metric series…</div>
        ) : series.length === 0 ? (
          <div className="state">
            <h3>No metrics logged</h3>
            <p>
              This run wrote no rows to <code>metrics.jsonl</code>. Call <code>run.log({"{...}"}, step=i)</code> in the
              training loop to record them.
            </p>
          </div>
        ) : (
          <div className="series-clip">
            <div className="series-grid-wrap">
              {series.map((s, i) => (
                <SeriesChart key={s.name} name={s.name} data={s.data} index={i} />
              ))}
            </div>
          </div>
        )}
      </section>

      <div className="overview-lower">
        <section className="panel" aria-labelledby="ov-params">
          <div className="panel-head">
            <h2 id="ov-params">Parameters</h2>
            <span className="sub num">{paramEntries.length}</span>
            <span className="spacer" />
            {paramEntries.length > 0 && (
              <button type="button" className="link" onClick={() => onOpenTab("run-params")}>
                All parameters
              </button>
            )}
          </div>
          {paramEntries.length === 0 ? (
            <div className="state">No parameters recorded. Pass a <code>config</code> to <code>init()</code>.</div>
          ) : (
            <table className="kv">
              <tbody>
                {paramEntries.map(([key, value]) => (
                  <tr key={key}>
                    <td>{key}</td>
                    <td>{paramValue(value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="panel" aria-labelledby="ov-logs">
          <div className="panel-head">
            <h2 id="ov-logs">Logs</h2>
            {logs && (
              <span className="sub num">
                {logsCapped ? `${logs.length}+` : logs.length} lines
              </span>
            )}
            <span className="spacer" />
            {logs && logs.length > 0 && (
              <button type="button" className="link" onClick={() => onOpenTab("logs")}>
                Open full log
              </button>
            )}
          </div>
          {logs === null ? (
            <div className="state">Loading the captured log…</div>
          ) : logs.length === 0 ? (
            <div className="state">Nothing was captured for this run.</div>
          ) : (
            <div className="table-wrap">
              <table className="table log-table">
                <thead>
                  <tr>
                    <th className="r">Time (s)</th>
                    <th>Level</th>
                    <th>Source</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {tail.map((line, i) => (
                    <tr key={i}>
                      <td className="r mono t">{typeof line.timestamp === "number" ? `+${line.timestamp.toFixed(3)}` : ""}</td>
                      <td>
                        <span className={`lvl lvl-${line.level ?? "info"}`}>{line.level ?? ""}</span>
                      </td>
                      <td>
                        <span className={`src src-${line.source ?? ""}`}>{line.source ?? ""}</span>
                      </td>
                      <td className="mono m" title={line.message}>
                        {line.message}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default Overview;
