import React, { useEffect, useState, useCallback } from 'react';
import './ConfusionMatrix.css';

interface ConfusionMatrixData {
  labels: string[];
  matrix: number[][];
  accuracy?: number;
  precision?: number[];
  recall?: number[];
  f1Score?: number[];
}

interface Props {
  runId: string;
}

const ConfusionMatrix: React.FC<Props> = ({ runId }) => {
  const [data, setData] = useState<ConfusionMatrixData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [normalizeMode, setNormalizeMode] = useState<'none' | 'true' | 'pred' | 'all'>('none');

  const fetchConfusionMatrix = useCallback(async () => {
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

      const cmArtifact = records.find(
        (art) => art.type === 'confusion_matrix' || art.type === 'classification_report'
      );

      // `metadata` is passed through verbatim, so the keys are read exactly as
      // written — `f1Score` is camelCase on disk for this payload alone.
      const payload = cmArtifact?.metadata as ConfusionMatrixData | undefined;
      const usable =
        payload && Array.isArray(payload.labels) && Array.isArray(payload.matrix) && payload.matrix.length > 0;

      // A run with no confusion matrix is the ordinary case, not a failure.
      setData(usable ? payload : null);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchConfusionMatrix();
  }, [fetchConfusionMatrix]);

  const getNormalizedMatrix = (): number[][] => {
    if (!data) return [];

    const { matrix } = data;
    
    switch (normalizeMode) {
      case 'true':
        // Normalize by true labels (rows)
        return matrix.map(row => {
          const sum = row.reduce((a, b) => a + b, 0);
          return row.map(val => sum > 0 ? val / sum : 0);
        });
      
      case 'pred': {
        // Normalize by predicted labels (columns)
        const colSums = matrix[0].map((_, colIdx) => 
          matrix.reduce((sum, row) => sum + row[colIdx], 0)
        );
        return matrix.map(row => 
          row.map((val, colIdx) => colSums[colIdx] > 0 ? val / colSums[colIdx] : 0)
        );
      }
      
      case 'all': {
        // Normalize by total
        const total = matrix.reduce((sum, row) => 
          sum + row.reduce((a, b) => a + b, 0), 0
        );
        return matrix.map(row => row.map(val => total > 0 ? val / total : 0));
      }
      
      default:
        return matrix;
    }
  };

  const getColor = (value: number, max: number): string => {
    const intensity = max > 0 ? value / max : 0;
    const hue = 220; // Blue
    const saturation = 70 + intensity * 30;
    const lightness = 95 - intensity * 60;
    return `hsl(${hue}, ${saturation}%, ${lightness}%)`;
  };

  const formatValue = (value: number): string => {
    if (normalizeMode === 'none') {
      return value.toString();
    }
    return (value * 100).toFixed(1) + '%';
  };

  if (loading) {
    return <div className="confusion-matrix loading">Loading confusion matrix...</div>;
  }

  if (error) {
    return <div className="confusion-matrix error">Error: {error}</div>;
  }

  // Nothing to draw is not an error; the Evaluation tab carries the one quiet line.
  if (!data) {
    return null;
  }

  const normalizedMatrix = getNormalizedMatrix();
  const maxValue = Math.max(...normalizedMatrix.flat());

  return (
    <div className="confusion-matrix">
      <div className="matrix-header">
        <h3>Confusion Matrix</h3>
        
        <div className="matrix-controls">
          <label>Normalize:</label>
          <select value={normalizeMode} onChange={(e) => setNormalizeMode(e.target.value as typeof normalizeMode)}>
            <option value="none">None (raw counts)</option>
            <option value="true">By True Label (rows)</option>
            <option value="pred">By Predicted Label (columns)</option>
            <option value="all">By Total</option>
          </select>
        </div>
      </div>

      {data.accuracy !== undefined && (
        <div className="matrix-metrics">
          <div className="metric-item">
            <span className="metric-label">Overall Accuracy:</span>
            <span className="metric-value">{(data.accuracy * 100).toFixed(2)}%</span>
          </div>
        </div>
      )}

      <div className="matrix-container">
        <div className="matrix-labels-vertical">
          <span className="axis-label">True Label</span>
        </div>
        
        <div className="matrix-content">
          <div className="matrix-table-wrapper">
            <table className="matrix-table">
              <thead>
                <tr>
                  <th className="corner-cell"></th>
                  {data.labels.map(label => (
                    <th key={`pred-${label}`} className="column-header">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.labels.map((trueLabel, rowIdx) => (
                  <tr key={`row-${trueLabel}`}>
                    <th className="row-header">{trueLabel}</th>
                    {data.labels.map((_, colIdx) => {
                      const value = normalizedMatrix[rowIdx][colIdx];
                      const isCorrect = rowIdx === colIdx;
                      
                      return (
                        <td
                          key={`cell-${rowIdx}-${colIdx}`}
                          className={`matrix-cell ${isCorrect ? 'correct' : 'incorrect'}`}
                          style={{
                            backgroundColor: getColor(value, maxValue),
                            color: value / maxValue > 0.5 ? '#fff' : '#000',
                          }}
                        >
                          {formatValue(value)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          
          <div className="matrix-labels-horizontal">
            <span className="axis-label">Predicted Label</span>
          </div>
        </div>
      </div>

      {data.precision?.length === data.labels.length &&
        data.recall?.length === data.labels.length &&
        data.f1Score?.length === data.labels.length && (
        <div className="class-metrics">
          <h4>Per-Class Metrics</h4>
          <table className="metrics-table">
            <thead>
              <tr>
                <th>Class</th>
                <th>Precision</th>
                <th>Recall</th>
                <th>F1-Score</th>
              </tr>
            </thead>
            <tbody>
              {data.labels.map((label, idx) => (
                <tr key={`metrics-${label}`}>
                  <td className="class-label">{label}</td>
                  <td className="metric-cell">{(data.precision![idx] * 100).toFixed(2)}%</td>
                  <td className="metric-cell">{(data.recall![idx] * 100).toFixed(2)}%</td>
                  <td className="metric-cell">{(data.f1Score![idx] * 100).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default ConfusionMatrix;
