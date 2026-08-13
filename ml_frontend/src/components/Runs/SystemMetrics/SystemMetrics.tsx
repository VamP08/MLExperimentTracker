import React, { useEffect, useState, useCallback } from 'react';
import './SystemMetrics.css';

interface SystemMetric {
  timestamp: number;
  cpu_percent?: number;
  memory_percent?: number;
  memory_used_mb?: number;
  memory_available_mb?: number;
  gpu_utilization?: number;
  gpu_memory_used_mb?: number;
  disk_usage_percent?: number;
}

interface SystemMetricsSummary {
  cpu?: {
    mean?: number;
    max?: number;
    min?: number;
  };
  memory?: {
    mean?: number;
    max?: number;
    min?: number;
  };
  gpu?: {
    mean?: number;
    max?: number;
    min?: number;
  };
}

interface Props {
  runId: string;
}

const SystemMetrics: React.FC<Props> = ({ runId }) => {
  const [systemMetrics, setSystemMetrics] = useState<SystemMetric[] | null>(null);
  const [summary, setSummary] = useState<SystemMetricsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSystemMetrics = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch(`/api/run/${runId}/system-metrics`);
      
      if (!response.ok) {
        throw new Error('Failed to fetch system metrics');
      }

      const data = await response.json();
      
      if (Array.isArray(data)) {
        setSystemMetrics(data);
        calculateSummary(data);
      } else if (data.summary) {
        setSummary(data.summary);
      } else {
        setSystemMetrics(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchSystemMetrics();
  }, [fetchSystemMetrics]);

  const calculateSummary = (metrics: SystemMetric[]) => {
    if (!metrics || metrics.length === 0) return;

    const cpuValues = metrics.map(m => m.cpu_percent).filter(v => v !== undefined) as number[];
    const memValues = metrics.map(m => m.memory_percent).filter(v => v !== undefined) as number[];
    const gpuValues = metrics.map(m => m.gpu_utilization).filter(v => v !== undefined) as number[];

    const calcStats = (values: number[]) => ({
      mean: values.reduce((a, b) => a + b, 0) / values.length,
      max: Math.max(...values),
      min: Math.min(...values)
    });

    setSummary({
      cpu: cpuValues.length > 0 ? calcStats(cpuValues) : undefined,
      memory: memValues.length > 0 ? calcStats(memValues) : undefined,
      gpu: gpuValues.length > 0 ? calcStats(gpuValues) : undefined
    });
  };

  if (loading) {
    return <div className="system-metrics-loading">Loading system metrics...</div>;
  }

  if (error) {
    return <div className="system-metrics-error">Error: {error}</div>;
  }

  if (!systemMetrics && !summary) {
    return <div className="system-metrics-empty">No system metrics available</div>;
  }

  return (
    <div className="system-metrics">
      <h3>System Metrics</h3>
      
      {summary && (
        <div className="metrics-summary">
          {summary.cpu && (
            <div className="metric-card">
              <h4>CPU Usage</h4>
              <div className="metric-stats">
                <div className="stat">
                  <span className="label">Mean:</span>
                  <span className="value">{summary.cpu.mean?.toFixed(1)}%</span>
                </div>
                <div className="stat">
                  <span className="label">Max:</span>
                  <span className="value">{summary.cpu.max?.toFixed(1)}%</span>
                </div>
                <div className="stat">
                  <span className="label">Min:</span>
                  <span className="value">{summary.cpu.min?.toFixed(1)}%</span>
                </div>
              </div>
            </div>
          )}

          {summary.memory && (
            <div className="metric-card">
              <h4>Memory Usage</h4>
              <div className="metric-stats">
                <div className="stat">
                  <span className="label">Mean:</span>
                  <span className="value">{summary.memory.mean?.toFixed(1)}%</span>
                </div>
                <div className="stat">
                  <span className="label">Max:</span>
                  <span className="value">{summary.memory.max?.toFixed(1)}%</span>
                </div>
                <div className="stat">
                  <span className="label">Min:</span>
                  <span className="value">{summary.memory.min?.toFixed(1)}%</span>
                </div>
              </div>
            </div>
          )}

          {summary.gpu && (
            <div className="metric-card">
              <h4>GPU Utilization</h4>
              <div className="metric-stats">
                <div className="stat">
                  <span className="label">Mean:</span>
                  <span className="value">{summary.gpu.mean?.toFixed(1)}%</span>
                </div>
                <div className="stat">
                  <span className="label">Max:</span>
                  <span className="value">{summary.gpu.max?.toFixed(1)}%</span>
                </div>
                <div className="stat">
                  <span className="label">Min:</span>
                  <span className="value">{summary.gpu.min?.toFixed(1)}%</span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {systemMetrics && systemMetrics.length > 0 && (
        <div className="metrics-table-container">
          <table className="metrics-table">
            <thead>
              <tr>
                <th>Timestamp</th>
                {systemMetrics[0].cpu_percent !== undefined && <th>CPU %</th>}
                {systemMetrics[0].memory_percent !== undefined && <th>Memory %</th>}
                {systemMetrics[0].memory_used_mb !== undefined && <th>Memory Used (MB)</th>}
                {systemMetrics[0].gpu_utilization !== undefined && <th>GPU %</th>}
                {systemMetrics[0].gpu_memory_used_mb !== undefined && <th>GPU Memory (MB)</th>}
              </tr>
            </thead>
            <tbody>
              {systemMetrics.slice(0, 20).map((metric, idx) => (
                <tr key={idx}>
                  <td>{new Date(metric.timestamp * 1000).toLocaleTimeString()}</td>
                  {metric.cpu_percent !== undefined && <td>{metric.cpu_percent.toFixed(1)}</td>}
                  {metric.memory_percent !== undefined && <td>{metric.memory_percent.toFixed(1)}</td>}
                  {metric.memory_used_mb !== undefined && <td>{metric.memory_used_mb.toFixed(0)}</td>}
                  {metric.gpu_utilization !== undefined && <td>{metric.gpu_utilization.toFixed(1)}</td>}
                  {metric.gpu_memory_used_mb !== undefined && <td>{metric.gpu_memory_used_mb.toFixed(0)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          {systemMetrics.length > 20 && (
            <p className="table-note">Showing first 20 of {systemMetrics.length} entries</p>
          )}
        </div>
      )}
    </div>
  );
};

export default SystemMetrics;
