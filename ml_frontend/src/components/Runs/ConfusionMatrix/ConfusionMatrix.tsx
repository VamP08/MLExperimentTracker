import React, { useEffect, useState, useCallback } from 'react';
import './ConfusionMatrix.css';
import { apiFetch } from '../../../lib/api';

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
      const response = await apiFetch(`/api/run/${runId}/artifacts`);

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

      // metadata is passed through as-is, so `f1Score` is camelCase on disk here.
      const payload = cmArtifact?.metadata as ConfusionMatrixData | undefined;
      const usable =
        payload && Array.isArray(payload.labels) && Array.isArray(payload.matrix) && payload.matrix.length > 0;

      // No confusion matrix is normal, not an error.
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

  const formatValue = (value: number): string => {
    if (normalizeMode === 'none') {
      return value.toString();
    }
    return (value * 100).toFixed(1) + '%';
  };

  const shell = (body: React.ReactNode, controls?: React.ReactNode, sub?: React.ReactNode) => (
    <section className="panel" aria-labelledby="cm-head">
      <div className="panel-head">
        <h2 id="cm-head">Confusion matrix</h2>
        {sub}
        <span className="spacer" />
        {controls}
      </div>
      {body}
    </section>
  );

  if (loading) {
    return shell(<div className="state">Loading confusion matrix…</div>);
  }

  if (error) {
    return shell(<div className="state error">Could not load the confusion matrix: {error}</div>);
  }

  // Nothing to draw. The Evaluation tab shows the empty message.
  if (!data) {
    return null;
  }

  const normalizedMatrix = getNormalizedMatrix();
  const maxValue = Math.max(...normalizedMatrix.flat());
  const perClass =
    data.precision?.length === data.labels.length &&
    data.recall?.length === data.labels.length &&
    data.f1Score?.length === data.labels.length;

  return shell(
    <>
      <div className="panel-body">
        <div className="cm-wrap">
          <table className="cm">
            <caption className="sr-only">Rows are the true label, columns the predicted label.</caption>
            <thead>
              <tr>
                <th className="cm-corner" rowSpan={2}>
                  True label
                </th>
                <th className="cm-axis-top" colSpan={data.labels.length} scope="colgroup">
                  Predicted label
                </th>
              </tr>
              <tr>
                {data.labels.map((label) => (
                  <th key={`pred-${label}`} scope="col" className="cm-label">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.labels.map((trueLabel, rowIdx) => (
                <tr key={`row-${trueLabel}`}>
                  <th scope="row" className="cm-label cm-row">
                    {trueLabel}
                  </th>
                  {data.labels.map((_, colIdx) => {
                    const value = normalizedMatrix[rowIdx][colIdx];
                    return (
                      <td
                        key={`cell-${rowIdx}-${colIdx}`}
                        className={`cm-cell mono${rowIdx === colIdx ? ' diag' : ''}`}
                        style={{ '--i': maxValue > 0 ? value / maxValue : 0 } as React.CSSProperties}
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
        <p className="cm-note">Rows are the true label, columns the predicted label; the deeper the blue, the larger the value.</p>
      </div>

      {perClass && (
        <div className="table-wrap cm-classes">
          <table className="table">
            <thead>
              <tr>
                <th>Class</th>
                <th className="r">Precision</th>
                <th className="r">Recall</th>
                <th className="r">F1 score</th>
              </tr>
            </thead>
            <tbody>
              {data.labels.map((label, idx) => (
                <tr key={`metrics-${label}`}>
                  <td>{label}</td>
                  <td className="r mono">{(data.precision![idx] * 100).toFixed(2)}%</td>
                  <td className="r mono">{(data.recall![idx] * 100).toFixed(2)}%</td>
                  <td className="r mono">{(data.f1Score![idx] * 100).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>,
    <label>
      <span className="sr-only">Normalise</span>
      <select className="select" value={normalizeMode} onChange={(e) => setNormalizeMode(e.target.value as typeof normalizeMode)}>
        <option value="none">Raw counts</option>
        <option value="true">Normalise by true label (rows)</option>
        <option value="pred">Normalise by predicted label (columns)</option>
        <option value="all">Normalise by total</option>
      </select>
    </label>,
    data.accuracy !== undefined ? (
      <span className="sub num">
        accuracy <span className="mono">{(data.accuracy * 100).toFixed(2)}%</span>
      </span>
    ) : undefined,
  );
};

export default ConfusionMatrix;
