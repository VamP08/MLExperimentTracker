import React, { useEffect, useState } from 'react';
import './MetricsChart.css';

interface MetricDataPoint {
  step: number;
  value: number;
  timestamp: number;
}

interface MetricTimeSeries {
  name: string;
  data: MetricDataPoint[];
}

interface Props {
  runId: string;
  metricName?: string;
}

const MetricsChart: React.FC<Props> = ({ runId, metricName }) => {
  const [metrics, setMetrics] = useState<MetricTimeSeries[]>([]);
  const [selectedMetric, setSelectedMetric] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchMetricsTimeSeries = async () => {
      try {
        setLoading(true);
        const response = await fetch(`/api/run/${runId}/metrics`);
        
        if (!response.ok) {
          throw new Error('Failed to fetch metrics time series');
        }

        const data = await response.json();
        
        // Filter out metrics with only one data point
        const filteredData = data.filter((metric: MetricTimeSeries) => metric.data.length > 1);
        
        setMetrics(filteredData);
        
        // Set initial selected metric only on first load
        if (filteredData.length > 0) {
          setSelectedMetric(metricName || filteredData[0].name);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchMetricsTimeSeries();
  }, [runId, metricName]); // Only re-fetch when runId or metricName prop changes

  const getMinMax = (data: MetricDataPoint[]) => {
    if (data.length === 0) return { min: 0, max: 1 };
    const values = data.map(d => d.value);
    return {
      min: Math.min(...values),
      max: Math.max(...values)
    };
  };

  const renderChart = (metric: MetricTimeSeries) => {
    const { min, max } = getMinMax(metric.data);
    const range = max - min || 1;
    const chartHeight = 200;
    const chartWidth = 600;
    const padding = 40;

    const points = metric.data.map((d, i) => {
      const x = padding + (i / (metric.data.length - 1 || 1)) * (chartWidth - 2 * padding);
      const y = chartHeight - padding - ((d.value - min) / range) * (chartHeight - 2 * padding);
      return { x, y, value: d.value, step: d.step };
    });

    const pathData = points.map((p, i) => 
      `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`
    ).join(' ');

    return (
      <div className="chart-container">
        <div className="chart-header">
          <h4>{metric.name}</h4>
          <div className="chart-stats">
            <span>Min: {min.toFixed(4)}</span>
            <span>Max: {max.toFixed(4)}</span>
            <span>Latest: {metric.data[metric.data.length - 1]?.value.toFixed(4)}</span>
          </div>
        </div>
        
        <svg viewBox={`0 0 ${chartWidth} ${chartHeight}`} className="metrics-svg">
          {/* Grid lines */}
          <g className="grid">
            {[0, 0.25, 0.5, 0.75, 1].map(ratio => (
              <line
                key={ratio}
                x1={padding}
                y1={padding + ratio * (chartHeight - 2 * padding)}
                x2={chartWidth - padding}
                y2={padding + ratio * (chartHeight - 2 * padding)}
                stroke="#333"
                strokeWidth="1"
              />
            ))}
          </g>

          {/* Y-axis labels */}
          <g className="y-axis">
            {[0, 0.25, 0.5, 0.75, 1].map(ratio => {
              const value = max - ratio * range;
              return (
                <text
                  key={ratio}
                  x={padding - 10}
                  y={padding + ratio * (chartHeight - 2 * padding) + 5}
                  textAnchor="end"
                  fill="#999"
                  fontSize="12"
                >
                  {value.toFixed(3)}
                </text>
              );
            })}
          </g>

          {/* Line chart */}
          <path
            d={pathData}
            fill="none"
            stroke="#4a9eff"
            strokeWidth="2"
            className="metric-line"
          />

          {/* Data points */}
          {points.map((p, i) => (
            <circle
              key={i}
              cx={p.x}
              cy={p.y}
              r="4"
              fill="#4a9eff"
              className="data-point"
            >
              <title>Step {p.step}: {p.value.toFixed(4)}</title>
            </circle>
          ))}

          {/* X-axis */}
          <line
            x1={padding}
            y1={chartHeight - padding}
            x2={chartWidth - padding}
            y2={chartHeight - padding}
            stroke="#666"
            strokeWidth="2"
          />

          {/* Y-axis */}
          <line
            x1={padding}
            y1={padding}
            x2={padding}
            y2={chartHeight - padding}
            stroke="#666"
            strokeWidth="2"
          />

          {/* X-axis label */}
          <text
            x={chartWidth / 2}
            y={chartHeight - 5}
            textAnchor="middle"
            fill="#999"
            fontSize="14"
          >
            Training Steps
          </text>
        </svg>
      </div>
    );
  };

  if (loading) {
    return <div className="metrics-chart-loading">Loading metrics...</div>;
  }

  if (error) {
    return <div className="metrics-chart-error">Error: {error}</div>;
  }

  if (metrics.length === 0) {
    return <div className="metrics-chart-empty">No time series data available</div>;
  }

  const currentMetric = metrics.find(m => m.name === selectedMetric) || metrics[0];

  return (
    <div className="metrics-chart">
      <div className="metric-selector">
        <label>Select Metric:</label>
        <select 
          value={selectedMetric} 
          onChange={(e) => setSelectedMetric(e.target.value)}
        >
          {metrics.map(m => (
            <option key={m.name} value={m.name}>
              {m.name} ({m.data.length} points)
            </option>
          ))}
        </select>
      </div>

      {renderChart(currentMetric)}

      <div className="metric-details">
        <h5>Metric Details</h5>
        <div className="details-grid">
          <div className="detail-item">
            <span className="label">Total Points:</span>
            <span className="value">{currentMetric.data.length}</span>
          </div>
          <div className="detail-item">
            <span className="label">First Value:</span>
            <span className="value">{currentMetric.data[0]?.value.toFixed(4)}</span>
          </div>
          <div className="detail-item">
            <span className="label">Last Value:</span>
            <span className="value">{currentMetric.data[currentMetric.data.length - 1]?.value.toFixed(4)}</span>
          </div>
          <div className="detail-item">
            <span className="label">Change:</span>
            <span className={`value ${
              (currentMetric.data[currentMetric.data.length - 1]?.value - currentMetric.data[0]?.value) > 0 
                ? 'positive' 
                : 'negative'
            }`}>
              {((currentMetric.data[currentMetric.data.length - 1]?.value - currentMetric.data[0]?.value) * 100).toFixed(2)}%
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default MetricsChart;
