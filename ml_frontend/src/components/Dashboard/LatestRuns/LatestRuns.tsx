import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Sparkline from "../../Charts/Sparkline";
import { apiFetch } from "../../../lib/api";
import { seriesColor } from "../../../lib/chartScale";
import { camelCase } from "../../../lib/metrics";
import { shortDate } from "../format";
import "./LatestRuns.css";

export interface FeedRun {
  id: string;
  name: string;
  status: string;
  startedAt: string;
  experimentId: string;
  experimentName: string;
  colorIndex: number;
  /** Headline metric, camelCased like the comparison endpoint sends it. */
  headline: string | null;
}

interface Series {
  name: string;
  data: { step: number | null; value: unknown }[];
}

const LABEL: Record<string, string> = { completed: "Completed", failed: "Failed", running: "Running", archived: "Interrupted" };

/** One run with its training curve. */
const FeedRow = ({ run }: { run: FeedRun }) => {
  const [curve, setCurve] = useState<{ name: string; values: number[] } | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/api/run/${run.id}/metrics`)
      .then((res) => (res.ok ? res.json() : []))
      .then((body: Series[]) => {
        if (cancelled || !Array.isArray(body)) return;
        // The headline is camelCased but the series keep their logged names, so compare through
        // the same transform. Falls back to the first series.
        const series = body.find((s) => run.headline && camelCase(s.name) === run.headline) ?? body[0];
        const values = series ? series.data.map((d) => Number(d.value)).filter(Number.isFinite) : [];
        setCurve(series ? { name: series.name, values } : null);
      })
      .catch(() => !cancelled && setCurve(null));
    return () => {
      cancelled = true;
    };
  }, [run.id, run.headline]);

  const last = curve && curve.values.length ? curve.values[curve.values.length - 1] : undefined;

  return (
    <li className="feed-row">
      <div className="feed-main">
        <Link className="feed-name" to={`/runs/${encodeURIComponent(run.id)}`}>
          {run.name}
        </Link>
        <div className="feed-meta">
          <span className={`badge ${run.status}`}>{LABEL[run.status] ?? run.status}</span>
          <Link className="feed-exp" to={`/experiment/${encodeURIComponent(run.experimentId)}`}>
            <i style={{ background: seriesColor(run.colorIndex) }} aria-hidden="true" />
            {run.experimentName}
          </Link>
          <span className="num">{shortDate(run.startedAt)}</span>
        </div>
      </div>
      <div className="feed-curve">
        {curve === undefined ? (
          <span className="spark-empty">Loading…</span>
        ) : curve === null ? (
          <span className="spark-empty">No metrics</span>
        ) : (
          <Sparkline values={curve.values} color={seriesColor(run.colorIndex)} label={`${curve.name} over ${curve.values.length} steps`} />
        )}
      </div>
      <div className="feed-value">
        <b className="num">{typeof last === "number" ? last.toFixed(4) : "—"}</b>
        <span>{curve ? `${curve.name} · latest` : ""}</span>
      </div>
    </li>
  );
};

const LatestRuns = ({ runs }: { runs: FeedRun[] }) => (
  <section className="panel" aria-labelledby="dash-latest">
    <div className="panel-head">
      <h2 id="dash-latest">Latest runs</h2>
      <span className="sub">newest first, each with its training curve</span>
    </div>
    {runs.length === 0 ? (
      <div className="state">No runs in the experiments shown.</div>
    ) : (
      <ul className="feed">
        {runs.map((run) => (
          <FeedRow key={run.id} run={run} />
        ))}
      </ul>
    )}
  </section>
);

export default LatestRuns;
