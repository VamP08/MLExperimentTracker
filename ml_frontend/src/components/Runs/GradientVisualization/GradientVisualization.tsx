import React, { useEffect, useState, useCallback } from 'react';
import './GradientVisualization.css';

interface GradientData {
  step: number;
  timestamp: number;
  layerName: string;
  gradientMean: number;
  gradientStd: number;
  gradientMin: number;
  gradientMax: number;
  gradientNorm: number;
}

interface Props {
  runId: string;
}

const GradientVisualization: React.FC<Props> = ({ runId }) => {
  const [gradients, setGradients] = useState<GradientData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedLayer, setSelectedLayer] = useState<string | 'all'>('all');
  const [viewMode, setViewMode] = useState<'norm' | 'mean' | 'std'>('norm');

  const fetchGradients = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch(`/api/runs/${runId}/metrics-timeseries`);
      
      if (!response.ok) {
        throw new Error(`Failed to fetch gradients: ${response.statusText}`);
      }

      const data = await response.json();
      
      // Extract gradient metrics from time series data
      const gradientMetrics: GradientData[] = [];
      
      data.forEach((metric: { name: string; values: Array<{ step: number; timestamp: number; value: number }> }) => {
        if (metric.name.startsWith('gradient/')) {
          const layerName = metric.name.split('/')[1];
          const metricType = metric.name.split('/')[2]; // mean, std, min, max, norm
          
          metric.values.forEach((point: { step: number; timestamp: number; value: number }) => {
            const existing = gradientMetrics.find(
              g => g.step === point.step && g.layerName === layerName
            );
            
            if (existing) {
              // Update existing entry
              if (metricType === 'mean') existing.gradientMean = point.value;
              else if (metricType === 'std') existing.gradientStd = point.value;
              else if (metricType === 'min') existing.gradientMin = point.value;
              else if (metricType === 'max') existing.gradientMax = point.value;
              else if (metricType === 'norm') existing.gradientNorm = point.value;
            } else {
              // Create new entry
              const newEntry: GradientData = {
                step: point.step,
                timestamp: point.timestamp,
                layerName,
                gradientMean: metricType === 'mean' ? point.value : 0,
                gradientStd: metricType === 'std' ? point.value : 0,
                gradientMin: metricType === 'min' ? point.value : 0,
                gradientMax: metricType === 'max' ? point.value : 0,
                gradientNorm: metricType === 'norm' ? point.value : 0,
              };
              gradientMetrics.push(newEntry);
            }
          });
        }
      });

      gradientMetrics.sort((a, b) => a.step - b.step);
      setGradients(gradientMetrics);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchGradients();
  }, [fetchGradients]);

  const layers = Array.from(new Set(gradients.map(g => g.layerName)));

  const filteredGradients = selectedLayer === 'all' 
    ? gradients 
    : gradients.filter(g => g.layerName === selectedLayer);

  const getGradientValue = (grad: GradientData): number => {
    switch (viewMode) {
      case 'norm': return grad.gradientNorm;
      case 'mean': return Math.abs(grad.gradientMean);
      case 'std': return grad.gradientStd;
      default: return grad.gradientNorm;
    }
  };

  // Detect vanishing/exploding gradients
  const analyzeGradients = () => {
    if (gradients.length === 0) return null;

    const norms = gradients.map(g => g.gradientNorm);
    const meanNorm = norms.reduce((a, b) => a + b, 0) / norms.length;
    const maxNorm = Math.max(...norms);
    const minNorm = Math.min(...norms);

    const vanishing = meanNorm < 1e-7;
    const exploding = maxNorm > 100;

    return {
      meanNorm: meanNorm.toExponential(3),
      maxNorm: maxNorm.toExponential(3),
      minNorm: minNorm.toExponential(3),
      status: vanishing ? 'vanishing' : exploding ? 'exploding' : 'healthy',
    };
  };

  const analysis = analyzeGradients();

  // Prepare chart data
  const chartData = () => {
    if (filteredGradients.length === 0) return null;

    const steps = Array.from(new Set(filteredGradients.map(g => g.step))).sort((a, b) => a - b);
    const layersToShow = selectedLayer === 'all' ? layers.slice(0, 5) : [selectedLayer];

    const maxValue = Math.max(...filteredGradients.map(getGradientValue));
    const minValue = Math.min(...filteredGradients.map(getGradientValue));

    return { steps, layersToShow, maxValue, minValue };
  };

  if (loading) {
    return <div className="gradient-visualization loading">Loading gradient data...</div>;
  }

  if (error) {
    return <div className="gradient-visualization error">Error: {error}</div>;
  }

  if (gradients.length === 0) {
    return <div className="gradient-visualization empty">No gradient data available for this run.</div>;
  }

  const chart = chartData();

  return (
    <div className="gradient-visualization">
      <div className="gradient-header">
        <h3>Gradient Flow Analysis</h3>
        
        {analysis && (
          <div className={`gradient-status ${analysis.status}`}>
            <span className="status-label">Status:</span>
            <span className="status-value">{analysis.status}</span>
            <div className="status-details">
              <span>Mean: {analysis.meanNorm}</span>
              <span>Max: {analysis.maxNorm}</span>
              <span>Min: {analysis.minNorm}</span>
            </div>
          </div>
        )}
      </div>

      <div className="gradient-controls">
        <div className="control-group">
          <label>View Mode:</label>
          <select value={viewMode} onChange={(e) => setViewMode(e.target.value as 'norm' | 'mean' | 'std')}>
            <option value="norm">Gradient Norm</option>
            <option value="mean">Gradient Mean (abs)</option>
            <option value="std">Gradient Std Dev</option>
          </select>
        </div>

        <div className="control-group">
          <label>Layer:</label>
          <select value={selectedLayer} onChange={(e) => setSelectedLayer(e.target.value)}>
            <option value="all">All Layers (top 5)</option>
            {layers.map(layer => (
              <option key={layer} value={layer}>{layer}</option>
            ))}
          </select>
        </div>
      </div>

      {chart && (
        <div className="gradient-chart">
          <svg viewBox="0 0 800 400" className="chart-svg">
            {/* Y-axis */}
            <line x1="50" y1="350" x2="50" y2="50" stroke="#444" strokeWidth="2" />
            {/* X-axis */}
            <line x1="50" y1="350" x2="750" y2="350" stroke="#444" strokeWidth="2" />
            
            {/* Y-axis labels */}
            {[0, 0.25, 0.5, 0.75, 1].map(ratio => {
              const y = 350 - ratio * 300;
              const value = (chart.minValue + (chart.maxValue - chart.minValue) * ratio).toExponential(2);
              return (
                <g key={ratio}>
                  <line x1="45" y1={y} x2="50" y2={y} stroke="#666" strokeWidth="1" />
                  <text x="40" y={y + 5} textAnchor="end" fontSize="12" fill="#aaa">{value}</text>
                </g>
              );
            })}

            {/* X-axis labels */}
            {chart.steps.filter((_, i) => i % Math.ceil(chart.steps.length / 10) === 0).map(step => {
              const x = 50 + ((step - chart.steps[0]) / (chart.steps[chart.steps.length - 1] - chart.steps[0])) * 700;
              return (
                <g key={step}>
                  <line x1={x} y1="350" x2={x} y2="355" stroke="#666" strokeWidth="1" />
                  <text x={x} y="370" textAnchor="middle" fontSize="12" fill="#aaa">{step}</text>
                </g>
              );
            })}

            {/* Plot lines for each layer */}
            {chart.layersToShow.map((layer, layerIdx) => {
              const layerData = filteredGradients
                .filter(g => g.layerName === layer)
                .sort((a, b) => a.step - b.step);
              
              const points = layerData.map(g => {
                const x = 50 + ((g.step - chart.steps[0]) / (chart.steps[chart.steps.length - 1] - chart.steps[0])) * 700;
                const value = getGradientValue(g);
                const y = 350 - ((value - chart.minValue) / (chart.maxValue - chart.minValue)) * 300;
                return { x, y };
              });

              const pathData = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
              const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];
              const color = colors[layerIdx % colors.length];

              return (
                <g key={layer}>
                  <path
                    d={pathData}
                    fill="none"
                    stroke={color}
                    strokeWidth="2"
                    opacity="0.8"
                  />
                  {/* Legend */}
                  <text
                    x={760}
                    y={60 + layerIdx * 20}
                    fill={color}
                    fontSize="12"
                  >
                    {layer}
                  </text>
                </g>
              );
            })}

            {/* Chart title */}
            <text x="400" y="30" textAnchor="middle" fontSize="16" fill="#fff" fontWeight="bold">
              Gradient {viewMode === 'norm' ? 'Norm' : viewMode === 'mean' ? 'Mean' : 'Std Dev'} Over Training
            </text>
            
            {/* Axis labels */}
            <text x="400" y="395" textAnchor="middle" fontSize="14" fill="#aaa">Training Step</text>
            <text x="20" y="200" textAnchor="middle" fontSize="14" fill="#aaa" transform="rotate(-90 20 200)">
              Gradient {viewMode === 'norm' ? 'Norm' : viewMode === 'mean' ? 'Mean' : 'Std Dev'}
            </text>
          </svg>
        </div>
      )}

      <div className="gradient-summary">
        <h4>Layer-wise Statistics</h4>
        <div className="layer-stats">
          {layers.map(layer => {
            const layerGrads = gradients.filter(g => g.layerName === layer);
            const meanNorm = layerGrads.reduce((sum, g) => sum + g.gradientNorm, 0) / layerGrads.length;
            const maxNorm = Math.max(...layerGrads.map(g => g.gradientNorm));
            const minNorm = Math.min(...layerGrads.map(g => g.gradientNorm));

            return (
              <div key={layer} className="layer-stat-item">
                <span className="layer-name">{layer}</span>
                <span className="stat-value">Mean: {meanNorm.toExponential(3)}</span>
                <span className="stat-value">Max: {maxNorm.toExponential(3)}</span>
                <span className="stat-value">Min: {minNorm.toExponential(3)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default GradientVisualization;
