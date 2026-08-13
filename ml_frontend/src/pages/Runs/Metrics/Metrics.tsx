import { useState, useEffect, useCallback } from 'react';
import './Metrics.css';
import MetricsChart from '../../../components/Runs/MetricsChart/MetricsChart';

interface MetricData {
  name: string;
  value: string;
  description: string;
  latest?: number;
  mean?: number;
  min?: number;
  max?: number;
}

interface MetricsProps {
  runId?: string;
}

const STAT_SUFFIXES = ['Mean', 'Max', 'Min', 'Stddev'] as const;

/**
 * The API returns `metrics` as a flat map of scalars, not as nested stat
 * objects: `summary.json`'s `{loss: {latest, mean, max, min, stddev}}` has
 * already been flattened by the backend into `loss`, `lossMean`, `lossMax`,
 * `lossMin`, `lossStddev`. Reassemble the groups here.
 *
 * Known ambiguity: a metric genuinely named `loss_mean` arrives as `lossMean`
 * and is indistinguishable from the derived mean of `loss`. Avoid metric names
 * ending in _mean/_max/_min/_stddev — see docs/DATA-CONTRACT.md.
 */
const groupFlatMetrics = (flat: Record<string, unknown>): MetricData[] => {
  const numeric = new Map<string, number>();
  for (const [key, value] of Object.entries(flat)) {
    if (typeof value === 'number' && Number.isFinite(value)) numeric.set(key, value);
  }

  const isStatOf = (key: string): string | null => {
    for (const suffix of STAT_SUFFIXES) {
      if (key.endsWith(suffix)) {
        const base = key.slice(0, -suffix.length);
        if (base.length > 0 && numeric.has(base)) return base;
      }
    }
    return null;
  };

  const metrics: MetricData[] = [];
  for (const [key, latest] of numeric) {
    if (isStatOf(key) !== null) continue;

    const mean = numeric.get(`${key}Mean`);
    const min = numeric.get(`${key}Min`);
    const max = numeric.get(`${key}Max`);

    const stats = [
      mean !== undefined ? `Mean: ${mean.toFixed(4)}` : null,
      min !== undefined ? `Min: ${min.toFixed(4)}` : null,
      max !== undefined ? `Max: ${max.toFixed(4)}` : null,
    ].filter(Boolean);

    metrics.push({
      name: key,
      value: latest.toFixed(4),
      description: stats.length > 0 ? stats.join(', ') : 'Latest value only — no summary statistics logged.',
      latest,
      mean,
      min,
      max,
    });
  }

  return metrics.sort((a, b) => a.name.localeCompare(b.name));
};

const Metrics = ({ runId }: MetricsProps) => {
  const [viewMode, setViewMode] = useState<'table' | 'chart'>('table');
  const [metrics, setMetrics] = useState<MetricData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchMetrics = useCallback(async () => {
    if (!runId) {
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      setError(null);

      // Relative URL so the Vite dev proxy handles it; an absolute origin here
      // is cross-origin and gets blocked by CORS.
      const response = await fetch(`/api/run/${runId}`);
      if (!response.ok) {
        throw new Error(`Request failed with ${response.status}`);
      }

      const data = await response.json();
      setMetrics(groupFlatMetrics(data.metrics || {}));
    } catch (err) {
      console.error('Error fetching metrics:', err);
      setError(err instanceof Error ? err.message : 'Failed to load metrics');
      setMetrics([]);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchMetrics();
  }, [runId, fetchMetrics]);

  const handleExportCSV = async () => {
    if (!runId) return;

    try {
      const response = await fetch(`/api/run/${runId}/metrics/export`);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `metrics_${runId}.csv`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      console.error('Error exporting metrics:', err);
    }
  };

  if (loading) {
    return <div className="metrics-loading">Loading metrics...</div>;
  }

  return (
    <div className="metrics-page">
      <div className="metrics-header">
        <h2>Run Metrics</h2>
        <div className="metrics-actions">
          <button className="export-button" onClick={handleExportCSV}>Export CSV</button>
          <button
            className={`visualize-button ${viewMode === 'chart' ? 'active' : ''}`}
            onClick={() => setViewMode(viewMode === 'chart' ? 'table' : 'chart')}
          >
            Time Series
          </button>
        </div>
      </div>

      {error && (
        <div className="metrics-error">Could not load metrics: {error}</div>
      )}

      {viewMode === 'chart' && runId && (
        <MetricsChart runId={runId} />
      )}

      {viewMode === 'table' && !error && metrics.length === 0 && (
        <div className="metrics-empty">
          No metrics recorded for this run. Summary metrics come from
          <code> summary.json</code>&rsquo;s <code>metrics_summary</code>; a metric appears
          only once it has a <code>latest</code> value.
        </div>
      )}

      {viewMode === 'table' && metrics.length > 0 && (
        <>
          <div className="metrics-grid">
            {metrics.map((metric) => (
              <div key={metric.name} className="metric-card">
                <div className="metric-header">
                  <h3 className="metric-name">{metric.name}</h3>
                </div>
                <div className="metric-value">{metric.value}</div>
                <div className="metric-description">{metric.description}</div>
              </div>
            ))}
          </div>

          <div className="metrics-table-container">
            <h3>Detailed Metrics</h3>
            <table className="metrics-table">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>Latest</th>
                  <th>Mean</th>
                  <th>Min</th>
                  <th>Max</th>
                </tr>
              </thead>
              <tbody>
                {metrics.map((metric) => (
                  <tr key={metric.name}>
                    <td className="metric-name-cell">{metric.name}</td>
                    <td className="metric-value-cell">{metric.value}</td>
                    <td>{metric.mean !== undefined ? metric.mean.toFixed(4) : '—'}</td>
                    <td>{metric.min !== undefined ? metric.min.toFixed(4) : '—'}</td>
                    <td>{metric.max !== undefined ? metric.max.toFixed(4) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
};

export default Metrics;
