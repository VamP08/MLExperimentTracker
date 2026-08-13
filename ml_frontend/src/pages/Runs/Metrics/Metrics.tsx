import { useState, useEffect, useCallback } from 'react';
import './Metrics.css';
import MetricsChart from '../../../components/Runs/MetricsChart/MetricsChart';
import GradientVisualization from '../../../components/Runs/GradientVisualization/GradientVisualization';
import { groupFlatMetrics, describeMetricStats, type MetricGroup } from '../../../lib/metrics';

interface MetricsProps {
  runId?: string;
}

const Metrics = ({ runId }: MetricsProps) => {
  const [viewMode, setViewMode] = useState<'table' | 'chart'>('table');
  const [metrics, setMetrics] = useState<MetricGroup[]>([]);
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
                <div className="metric-value">{metric.latest.toFixed(4)}</div>
                <div className="metric-description">{describeMetricStats(metric)}</div>
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
                    <td className="metric-value-cell">{metric.latest.toFixed(4)}</td>
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

      {/* Renders nothing unless the run logged `gradient/<layer>/<stat>` series,
          which most runs do not. */}
      {runId && (
        <div className="metrics-gradients">
          <GradientVisualization runId={runId} />
        </div>
      )}
    </div>
  );
};

export default Metrics;
