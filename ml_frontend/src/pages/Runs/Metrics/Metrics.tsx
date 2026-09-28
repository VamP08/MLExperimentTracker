import { useEffect, useState } from "react";
import { FiDownload } from "react-icons/fi";
import "./Metrics.css";
import MetricsChart from "../../../components/Runs/MetricsChart/MetricsChart";
import GradientVisualization from "../../../components/Runs/GradientVisualization/GradientVisualization";
import { groupFlatMetrics, recordedNames, type MetricGroup } from "../../../lib/metrics";
import { apiFetch } from "../../../lib/api";
import { downloadFrom } from "../../../lib/download";

interface MetricsProps {
  runId: string;
}

const stat = (value: number | undefined) => (value !== undefined ? value.toFixed(4) : "—");

const Metrics = ({ runId }: MetricsProps) => {
  const [metrics, setMetrics] = useState<MetricGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setMetrics(null);
    setError(null);
    apiFetch(`/api/run/${runId}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);
        const data = await res.json();
        const names = recordedNames(data.metricsHistory);
        if (!cancelled)
          setMetrics(
            groupFlatMetrics(data.metrics || {}).map((g) => ({ ...g, name: names.get(g.name) ?? g.name })),
          );
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load metrics");
        setMetrics([]);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const exportCSV = async () => {
    setExporting(true);
    setExportError(null);
    try {
      await downloadFrom(`/api/run/${runId}/metrics/export`, `metrics_${runId}.csv`);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="mx-summary">
        <div className="panel-head">
          <h2 id="mx-summary">Summary statistics</h2>
          {metrics && metrics.length > 0 && <span className="sub num">{metrics.length}</span>}
          <span className="spacer" />
          {exportError && (
            <span className="mx-export-error" role="alert">
              {exportError}
            </span>
          )}
          <button type="button" className="btn" onClick={exportCSV} disabled={exporting}>
            <FiDownload aria-hidden="true" />
            Export CSV
          </button>
        </div>
        {error ? (
          <div className="state error">Could not load metrics: {error}</div>
        ) : metrics === null ? (
          <div className="state">Loading metrics…</div>
        ) : metrics.length === 0 ? (
          <div className="state">
            <h3>No metrics recorded for this run</h3>
            <p>
              Summary metrics come from <code>summary.json</code>&rsquo;s <code>metrics_summary</code>; a metric
              appears only once it has a <code>latest</code> value.
            </p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th className="r">Latest</th>
                  <th className="r">Mean</th>
                  <th className="r">Min</th>
                  <th className="r">Max</th>
                  <th className="r">Std dev</th>
                </tr>
              </thead>
              <tbody>
                {metrics.map((metric) => (
                  <tr key={metric.name}>
                    <td>{metric.name}</td>
                    <td className="r mono">{metric.latest.toFixed(4)}</td>
                    <td className="r mono">{stat(metric.mean)}</td>
                    <td className="r mono">{stat(metric.min)}</td>
                    <td className="r mono">{stat(metric.max)}</td>
                    <td className="r mono">{stat(metric.stddev)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <MetricsChart runId={runId} />

      {/* Renders nothing unless the run logged `gradient/<layer>/<stat>` series. */}
      <GradientVisualization runId={runId} />
    </div>
  );
};

export default Metrics;
