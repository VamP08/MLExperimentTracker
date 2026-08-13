import React, { useEffect, useState, useCallback } from 'react';
import './ROCCurve.css';

interface ROCData {
  fpr: number[];
  tpr: number[];
  thresholds: number[];
  auc: number;
  className?: string;
}

interface Props {
  runId: string;
}

const ROCCurve: React.FC<Props> = ({ runId }) => {
  const [curves, setCurves] = useState<ROCData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCurve, setSelectedCurve] = useState<number>(0);

  const isCurve = (value: unknown): value is ROCData => {
    const candidate = value as ROCData | null;
    return (
      !!candidate &&
      Array.isArray(candidate.fpr) &&
      Array.isArray(candidate.tpr) &&
      Array.isArray(candidate.thresholds) &&
      typeof candidate.auc === 'number' &&
      candidate.fpr.length > 0
    );
  };

  const fetchROCData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      // GAPS B9: the mounted prefix is singular. `/api/runs/...` was never a route.
      const response = await fetch(`/api/run/${runId}/artifacts`);

      if (!response.ok) {
        throw new Error(`Failed to fetch artifacts: ${response.statusText}`);
      }

      const artifacts = await response.json();
      const records: Array<{ type?: string; metadata?: unknown }> = Array.isArray(artifacts)
        ? artifacts
        : [];

      // One artifact line per class; `className` labels each curve.
      // A run with no ROC artifact is the ordinary case, not a failure.
      setCurves(
        records
          .filter((art) => art.type === 'roc_curve' || art.type === 'roc_auc')
          .map((art) => art.metadata)
          .filter(isCurve)
      );
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchROCData();
  }, [fetchROCData]);

  const renderROCCurve = (data: ROCData) => {
    const width = 500;
    const height = 500;
    const padding = 50;
    
    const points = data.fpr.map((fpr, i) => ({
      x: padding + (fpr * (width - 2 * padding)),
      y: height - padding - (data.tpr[i] * (height - 2 * padding)),
    }));

    const pathData = points.map((p, i) => 
      `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`
    ).join(' ');

    // Diagonal reference line (random classifier)
    const diagonalPath = `M ${padding} ${height - padding} L ${width - padding} ${padding}`;

    return (
      <svg viewBox={`0 0 ${width} ${height}`} className="roc-svg">
        {/* Background */}
        <rect x="0" y="0" width={width} height={height} fill="#0a0a15" />
        
        {/* Grid lines */}
        {[0, 0.2, 0.4, 0.6, 0.8, 1.0].map(val => {
          const x = padding + val * (width - 2 * padding);
          const y = height - padding - val * (height - 2 * padding);
          return (
            <g key={val}>
              <line
                x1={padding}
                y1={y}
                x2={width - padding}
                y2={y}
                stroke="rgba(255,255,255,0.1)"
                strokeWidth="1"
              />
              <line
                x1={x}
                y1={padding}
                x2={x}
                y2={height - padding}
                stroke="rgba(255,255,255,0.1)"
                strokeWidth="1"
              />
            </g>
          );
        })}

        {/* Axes */}
        <line
          x1={padding}
          y1={height - padding}
          x2={width - padding}
          y2={height - padding}
          stroke="#666"
          strokeWidth="2"
        />
        <line
          x1={padding}
          y1={padding}
          x2={padding}
          y2={height - padding}
          stroke="#666"
          strokeWidth="2"
        />

        {/* Axis labels */}
        {[0, 0.2, 0.4, 0.6, 0.8, 1.0].map(val => {
          const x = padding + val * (width - 2 * padding);
          const y = height - padding - val * (height - 2 * padding);
          return (
            <g key={`label-${val}`}>
              <text
                x={x}
                y={height - padding + 25}
                textAnchor="middle"
                fill="#aaa"
                fontSize="12"
              >
                {val.toFixed(1)}
              </text>
              <text
                x={padding - 25}
                y={y + 5}
                textAnchor="middle"
                fill="#aaa"
                fontSize="12"
              >
                {val.toFixed(1)}
              </text>
            </g>
          );
        })}

        {/* Diagonal reference line */}
        <path
          d={diagonalPath}
          fill="none"
          stroke="rgba(255,255,255,0.3)"
          strokeWidth="2"
          strokeDasharray="5,5"
        />

        {/* ROC curve */}
        <path
          d={pathData}
          fill="none"
          stroke="#3b82f6"
          strokeWidth="3"
          opacity="0.9"
        />

        {/* Fill area under curve */}
        <path
          d={`${pathData} L ${width - padding} ${height - padding} L ${padding} ${height - padding} Z`}
          fill="rgba(59, 130, 246, 0.1)"
        />

        {/* Points on curve */}
        {points.filter((_, i) => i % Math.ceil(points.length / 20) === 0).map((point, i) => (
          <circle
            key={i}
            cx={point.x}
            cy={point.y}
            r="4"
            fill="#3b82f6"
            stroke="#fff"
            strokeWidth="2"
          />
        ))}

        {/* Title */}
        <text x={width / 2} y={30} textAnchor="middle" fill="#fff" fontSize="18" fontWeight="bold">
          ROC Curve
        </text>

        {/* AUC label */}
        <text x={width - padding - 20} y={height - padding - 20} textAnchor="end" fill="#10b981" fontSize="16" fontWeight="bold">
          AUC = {data.auc.toFixed(3)}
        </text>

        {/* Axis titles */}
        <text x={width / 2} y={height - 10} textAnchor="middle" fill="#aaa" fontSize="14">
          False Positive Rate
        </text>
        <text
          x={20}
          y={height / 2}
          textAnchor="middle"
          fill="#aaa"
          fontSize="14"
          transform={`rotate(-90 20 ${height / 2})`}
        >
          True Positive Rate
        </text>
      </svg>
    );
  };

  if (loading) {
    return <div className="roc-curve loading">Loading ROC curve data...</div>;
  }

  if (error) {
    return <div className="roc-curve error">Error: {error}</div>;
  }

  // Nothing to draw is not an error and not worth a card of its own; the
  // Evaluation tab says once, quietly, when a run logged no evaluation artifacts.
  if (curves.length === 0) {
    return null;
  }

  const currentCurve = curves[selectedCurve];

  return (
    <div className="roc-curve">
      <div className="roc-header">
        <h3>ROC Curve Analysis</h3>
        
        {curves.length > 1 && (
          <div className="curve-selector">
            <label>Select Class:</label>
            <select value={selectedCurve} onChange={(e) => setSelectedCurve(parseInt(e.target.value))}>
              {curves.map((curve, idx) => (
                <option key={idx} value={idx}>
                  {curve.className || `Class ${idx + 1}`} (AUC: {curve.auc.toFixed(3)})
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="roc-chart">
        {renderROCCurve(currentCurve)}
      </div>

      <div className="roc-metrics">
        <div className="metric-card">
          <div className="metric-label">AUC Score</div>
          <div className="metric-value auc">{currentCurve.auc.toFixed(4)}</div>
          <div className="metric-interpretation">
            {currentCurve.auc >= 0.9 ? 'Excellent' :
             currentCurve.auc >= 0.8 ? 'Good' :
             currentCurve.auc >= 0.7 ? 'Fair' :
             currentCurve.auc >= 0.6 ? 'Poor' : 'Very Poor'}
          </div>
        </div>

        <div className="metric-card">
          <div className="metric-label">Total Points</div>
          <div className="metric-value">{currentCurve.fpr.length}</div>
          <div className="metric-interpretation">Thresholds evaluated</div>
        </div>

        <div className="metric-card">
          <div className="metric-label">Optimal Threshold</div>
          <div className="metric-value">
            {(() => {
              // Find threshold that maximizes (TPR - FPR)
              let maxDiff = -Infinity;
              let optimalIdx = 0;
              currentCurve.tpr.forEach((tpr, i) => {
                const diff = tpr - currentCurve.fpr[i];
                if (diff > maxDiff) {
                  maxDiff = diff;
                  optimalIdx = i;
                }
              });
              const threshold = currentCurve.thresholds[optimalIdx];
              return typeof threshold === 'number' ? threshold.toFixed(3) : '—';
            })()}
          </div>
          <div className="metric-interpretation">Maximizes TPR - FPR</div>
        </div>
      </div>

      {curves.length > 1 && (
        <div className="all-curves-summary">
          <h4>All Classes Summary</h4>
          <table className="curves-table">
            <thead>
              <tr>
                <th>Class</th>
                <th>AUC Score</th>
                <th>Quality</th>
              </tr>
            </thead>
            <tbody>
              {curves.map((curve, idx) => (
                <tr 
                  key={idx} 
                  className={idx === selectedCurve ? 'selected' : ''}
                  onClick={() => setSelectedCurve(idx)}
                >
                  <td>{curve.className || `Class ${idx + 1}`}</td>
                  <td className="auc-value">{curve.auc.toFixed(4)}</td>
                  <td className={`quality ${
                    curve.auc >= 0.9 ? 'excellent' :
                    curve.auc >= 0.8 ? 'good' :
                    curve.auc >= 0.7 ? 'fair' :
                    curve.auc >= 0.6 ? 'poor' : 'very-poor'
                  }`}>
                    {curve.auc >= 0.9 ? 'Excellent' :
                     curve.auc >= 0.8 ? 'Good' :
                     curve.auc >= 0.7 ? 'Fair' :
                     curve.auc >= 0.6 ? 'Poor' : 'Very Poor'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default ROCCurve;
