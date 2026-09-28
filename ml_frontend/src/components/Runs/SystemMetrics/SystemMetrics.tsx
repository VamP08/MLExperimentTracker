import { useEffect, useState } from "react";
import SeriesChart from "../../Charts/SeriesChart";
import { apiFetch } from "../../../lib/api";

/** One sample from `system_metrics.json`: epoch-seconds `timestamp` plus numeric readings. */
type Sample = Record<string, unknown>;

interface Stats {
  mean?: number;
  max?: number;
  min?: number;
}

/** The older summary-only shape: three resources, percentages only. */
interface SummaryShape {
  cpu?: Stats;
  memory?: Stats;
  gpu?: Stats;
}

interface Row {
  key: string;
  label: string;
  unit: "%" | "MB" | "";
  stats: Stats;
}

interface Props {
  runId: string;
}

/** Known fields in display order; unknown fields follow by name. */
const FIELDS: Record<string, { label: string; unit: Row["unit"] }> = {
  cpu_percent: { label: "CPU", unit: "%" },
  memory_percent: { label: "Memory", unit: "%" },
  memory_used_mb: { label: "Memory used", unit: "MB" },
  memory_available_mb: { label: "Memory available", unit: "MB" },
  disk_usage_percent: { label: "Disk", unit: "%" },
  gpu_utilization: { label: "GPU utilisation", unit: "%" },
  gpu_memory_used_mb: { label: "GPU memory used", unit: "MB" },
};
const ORDER = Object.keys(FIELDS);

function statsOf(values: number[]): Stats {
  return {
    mean: values.reduce((a, b) => a + b, 0) / values.length,
    max: Math.max(...values),
    min: Math.min(...values),
  };
}

function show(value: number | undefined, unit: Row["unit"]): string {
  if (value === undefined) return "—";
  if (unit === "MB") return `${value.toFixed(0)} MB`;
  return `${value.toFixed(1)}${unit}`;
}

/** Host resources sampled during the run. Each field in the samples gets a row and a chart. */
const SystemMetrics = ({ runId }: Props) => {
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [summary, setSummary] = useState<SummaryShape | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSamples(null);
    setSummary(null);
    apiFetch(`/api/run/${runId}/system-metrics`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        // An absent or malformed file arrives as `{}`, which means "nothing recorded".
        if (Array.isArray(data)) setSamples(data.filter((s): s is Sample => !!s && typeof s === "object"));
        else if (data && data.summary) setSummary(data.summary);
      })
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : "Unknown error"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const keys = samples
    ? Array.from(
        new Set(
          samples.flatMap((s) =>
            Object.keys(s).filter((k) => k !== "timestamp" && typeof s[k] === "number" && Number.isFinite(s[k])),
          ),
        ),
      ).sort((a, b) => {
        const ia = ORDER.indexOf(a);
        const ib = ORDER.indexOf(b);
        return (ia < 0 ? ORDER.length : ia) - (ib < 0 ? ORDER.length : ib) || a.localeCompare(b);
      })
    : [];

  const valuesOf = (key: string) =>
    (samples ?? []).map((s) => s[key]).filter((v): v is number => typeof v === "number" && Number.isFinite(v));

  const rows: Row[] = samples
    ? keys.map((key) => ({ key, label: FIELDS[key]?.label ?? key, unit: FIELDS[key]?.unit ?? "", stats: statsOf(valuesOf(key)) }))
    : summary
      ? (["cpu", "memory", "gpu"] as const)
          .filter((k) => summary[k])
          .map((k) => ({
            key: k,
            label: k === "cpu" ? "CPU" : k === "memory" ? "Memory" : "GPU utilisation",
            unit: "%" as const,
            stats: summary[k] as Stats,
          }))
      : [];

  const t0 = samples?.find((s) => typeof s.timestamp === "number")?.timestamp as number | undefined;
  const span =
    samples && t0 !== undefined
      ? (samples[samples.length - 1].timestamp as number) - t0
      : undefined;

  const head = (
    <div className="panel-head">
      <h2 id="sys-head">System metrics</h2>
      {samples && samples.length > 0 && (
        <span className="sub num">
          {samples.length} samples{typeof span === "number" && span > 0 ? ` over ${span.toFixed(0)} s` : ""}
        </span>
      )}
    </div>
  );

  if (loading || error || rows.length === 0) {
    return (
      <div className="stack">
        <section className="panel" aria-labelledby="sys-head">
          {head}
          {loading ? (
            <div className="state">Loading system metrics…</div>
          ) : error ? (
            <div className="state error">Could not load system metrics: {error}</div>
          ) : (
            <div className="state">
              <h3>No system metrics available</h3>
              <p>
                Pass <code>system_metrics=True</code> to <code>init()</code> to sample CPU, memory and GPU while the run
                trains. Sampling needs the <code>system</code> extra installed.
              </p>
            </div>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="sys-head">
        {head}
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Resource</th>
                <th className="r">Mean</th>
                <th className="r">Min</th>
                <th className="r">Max</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td>{row.label}</td>
                  <td className="r mono">{show(row.stats.mean, row.unit)}</td>
                  <td className="r mono">{show(row.stats.min, row.unit)}</td>
                  <td className="r mono">{show(row.stats.max, row.unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {samples && samples.length > 0 && (
        <section className="panel" aria-labelledby="sys-series">
          <div className="panel-head">
            <h2 id="sys-series">Over the run</h2>
            <span className="sub">x-axis is the sample number</span>
          </div>
          <div className="series-clip">
            <div className="series-grid-wrap">
              {rows.map((row, i) => (
                <SeriesChart
                  key={row.key}
                  name={row.unit ? `${row.label} (${row.unit})` : row.label}
                  index={i}
                  data={samples.map((s, n) => ({
                    step: n,
                    value: s[row.key],
                    timestamp: typeof s.timestamp === "number" && t0 !== undefined ? s.timestamp - t0 : null,
                  }))}
                />
              ))}
            </div>
          </div>
        </section>
      )}
    </div>
  );
};

export default SystemMetrics;
