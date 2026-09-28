import { useEffect, useState } from "react";
import "./MetricsChart.css";
import LineChart from "./LineChart";
import { apiFetch } from "../../../lib/api";

interface MetricDataPoint {
  step: number | null;
  value: unknown;
  timestamp?: number | null;
}

interface MetricTimeSeries {
  name: string;
  data: MetricDataPoint[];
  /** Position in the unfiltered list, so a series keeps the colour it has on the overview. */
  index: number;
  pts: { x: number; y: number }[];
}

interface Props {
  runId: string;
}

/** One metric at a time, drawn large, with a selector over every series that has a curve. */
const MetricsChart = ({ runId }: Props) => {
  const [metrics, setMetrics] = useState<MetricTimeSeries[] | null>(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMetrics(null);
    setError(null);
    apiFetch(`/api/run/${runId}/metrics`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);
        const body = await res.json();
        const list: MetricTimeSeries[] = (Array.isArray(body) ? body : [])
          .map((m: { name: string; data: MetricDataPoint[] }, index: number) => ({
            name: m.name,
            data: m.data,
            index,
            pts: (Array.isArray(m.data) ? m.data : [])
              .map((d) => ({ x: d.step, y: Number(d.value) }))
              .filter((p): p is { x: number; y: number } => typeof p.x === "number" && Number.isFinite(p.y)),
          }))
          // A single point is not a curve; the summary table already carries it.
          .filter((m) => m.pts.length > 1);
        if (cancelled) return;
        setMetrics(list);
        setSelected((current) => (list.some((m) => m.name === current) ? current : (list[0]?.name ?? "")));
      })
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : "Unknown error"));
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const current = metrics?.find((m) => m.name === selected) ?? metrics?.[0];
  const first = current?.pts[0];
  const last = current?.pts[current.pts.length - 1];
  const values = current?.pts.map((p) => p.y) ?? [];

  return (
    <section className="panel" aria-labelledby="mx-series">
      <div className="panel-head">
        <h2 id="mx-series">Time series</h2>
        {current && <span className="sub num">{current.pts.length} points</span>}
        <span className="spacer" />
        {metrics && metrics.length > 1 && (
          <label className="mc-pick">
            <span className="sr-only">Metric</span>
            <select className="select" value={current?.name} onChange={(e) => setSelected(e.target.value)}>
              {metrics.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {error ? (
        <div className="state error">Could not load the metric series: {error}</div>
      ) : metrics === null ? (
        <div className="state">Loading metric series…</div>
      ) : !current || !first || !last ? (
        <div className="state">
          <h3>No time series data available</h3>
          <p>A curve appears once a metric has been logged at two or more steps.</p>
        </div>
      ) : (
        <div className="panel-body">
          <dl className="mc-facts num">
            <div>
              <dt>First</dt>
              <dd className="mono">{first.y.toFixed(4)}</dd>
            </div>
            <div>
              <dt>Latest</dt>
              <dd className="mono">{last.y.toFixed(4)}</dd>
            </div>
            <div>
              <dt>Change</dt>
              <dd className="mono">
                {last.y - first.y >= 0 ? "+" : ""}
                {(last.y - first.y).toFixed(4)}
              </dd>
            </div>
            <div>
              <dt>Min</dt>
              <dd className="mono">{Math.min(...values).toFixed(4)}</dd>
            </div>
            <div>
              <dt>Max</dt>
              <dd className="mono">{Math.max(...values).toFixed(4)}</dd>
            </div>
          </dl>
          <LineChart
            series={[{ name: current.name, index: current.index, pts: current.pts }]}
            label={`${current.name} over ${current.pts.length} steps, from ${first.y.toFixed(4)} to ${last.y.toFixed(4)}`}
          />
        </div>
      )}
    </section>
  );
};

export default MetricsChart;
