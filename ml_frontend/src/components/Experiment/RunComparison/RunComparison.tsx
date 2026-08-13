import React, { useEffect, useState, useCallback } from 'react';
import './RunComparison.css';

interface RunMetrics {
  _id: string;
  runId: string;
  name: string;
  parameters: Record<string, unknown>;
  metrics: Record<string, {
    latest: number;
    mean: number;
    min: number;
    max: number;
  }>;
  tags: string[];
  createdAt: string;
  duration: string;
}

interface Props {
  experimentId: string;
}

const RunComparison: React.FC<Props> = ({ experimentId }) => {
  const [runs, setRuns] = useState<RunMetrics[]>([]);
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRuns = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch(`http://localhost:5000/api/experiment/${experimentId}/runs`);
      
      if (!response.ok) {
        throw new Error('Failed to fetch runs');
      }

      const data = await response.json();
      setRuns(data);
      
      // Auto-select first two runs
      if (data.length >= 2) {
        setSelectedRuns([data[0]._id, data[1]._id]);
      } else if (data.length === 1) {
        setSelectedRuns([data[0]._id]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [experimentId]);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  const toggleRunSelection = (runId: string) => {
    setSelectedRuns(prev => {
      if (prev.includes(runId)) {
        return prev.filter(id => id !== runId);
      } else if (prev.length < 4) {
        return [...prev, runId];
      }
      return prev;
    });
  };

  const getMetricComparison = (metricName: string) => {
    const values = selectedRuns
      .map(runId => runs.find(r => r._id === runId))
      .filter(r => r && r.metrics[metricName])
      .map(r => r!.metrics[metricName].latest);

    if (values.length === 0) return null;

    const max = Math.max(...values);
    const min = Math.min(...values);

    return { max, min, range: max - min };
  };

  const getParameterDiff = (paramName: string) => {
    const values = selectedRuns
      .map(runId => runs.find(r => r._id === runId))
      .filter(r => r && r.parameters[paramName] !== undefined)
      .map(r => r!.parameters[paramName]);

    return new Set(values).size > 1; // Returns true if values differ
  };

  if (loading) {
    return <div className="comparison-loading">Loading runs...</div>;
  }

  if (error) {
    return <div className="comparison-error">Error: {error}</div>;
  }

  if (runs.length === 0) {
    return <div className="comparison-empty">No runs available for comparison</div>;
  }

  const selectedRunsData = selectedRuns
    .map(id => runs.find(r => r._id === id))
    .filter(r => r !== undefined) as RunMetrics[];

  const allMetrics = new Set<string>();
  const allParams = new Set<string>();

  selectedRunsData.forEach(run => {
    Object.keys(run.metrics).forEach(m => allMetrics.add(m));
    Object.keys(run.parameters).forEach(p => allParams.add(p));
  });

  return (
    <div className="run-comparison">
      <div className="comparison-header">
        <h3>Compare Runs</h3>
        <p className="subtitle">Select up to 4 runs to compare (selected: {selectedRuns.length})</p>
      </div>

      <div className="run-selector">
        {runs.map(run => (
          <div
            key={run._id}
            className={`run-item ${selectedRuns.includes(run._id) ? 'selected' : ''}`}
            onClick={() => toggleRunSelection(run._id)}
          >
            <div className="run-checkbox">
              {selectedRuns.includes(run._id) && '✓'}
            </div>
            <div className="run-info">
              <div className="run-name">{run.name}</div>
              <div className="run-meta">
                {new Date(run.createdAt).toLocaleDateString()} • {run.duration}
              </div>
            </div>
          </div>
        ))}
      </div>

      {selectedRunsData.length > 0 && (
        <>
          <div className="comparison-section">
            <h4>Metrics Comparison</h4>
            <div className="comparison-table-container">
              <table className="comparison-table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    {selectedRunsData.map(run => (
                      <th key={run._id}>
                        <div className="run-header">
                          <div className="run-name-short">{run.name.substring(0, 20)}</div>
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Array.from(allMetrics).map(metricName => {
                    const comparison = getMetricComparison(metricName);
                    return (
                      <tr key={metricName}>
                        <td className="metric-name">{metricName}</td>
                        {selectedRunsData.map(run => {
                          const value = run.metrics[metricName]?.latest;
                          const isBest = value === comparison?.max;
                          const isWorst = value === comparison?.min;
                          
                          return (
                            <td
                              key={run._id}
                              className={`metric-value ${isBest && comparison!.range > 0 ? 'best' : ''} ${isWorst && comparison!.range > 0 ? 'worst' : ''}`}
                            >
                              {value !== undefined ? value.toFixed(4) : 'N/A'}
                              {isBest && comparison!.range > 0 && <span className="badge">BEST</span>}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="comparison-section">
            <h4>Parameters Comparison</h4>
            <div className="comparison-table-container">
              <table className="comparison-table">
                <thead>
                  <tr>
                    <th>Parameter</th>
                    {selectedRunsData.map(run => (
                      <th key={run._id}>
                        <div className="run-header">
                          <div className="run-name-short">{run.name.substring(0, 20)}</div>
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Array.from(allParams).map(paramName => {
                    const differs = getParameterDiff(paramName);
                    return (
                      <tr key={paramName} className={differs ? 'param-differs' : ''}>
                        <td className="param-name">
                          {paramName}
                          {differs && <span className="diff-indicator">•</span>}
                        </td>
                        {selectedRunsData.map(run => (
                          <td key={run._id} className="param-value">
                            {String(run.parameters[paramName] ?? 'N/A')}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="comparison-section">
            <h4>Run Metadata</h4>
            <div className="comparison-table-container">
              <table className="comparison-table">
                <thead>
                  <tr>
                    <th>Property</th>
                    {selectedRunsData.map(run => (
                      <th key={run._id}>
                        <div className="run-header">
                          <div className="run-name-short">{run.name.substring(0, 20)}</div>
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="meta-name">Created</td>
                    {selectedRunsData.map(run => (
                      <td key={run._id}>{new Date(run.createdAt).toLocaleString()}</td>
                    ))}
                  </tr>
                  <tr>
                    <td className="meta-name">Duration</td>
                    {selectedRunsData.map(run => (
                      <td key={run._id}>{run.duration}</td>
                    ))}
                  </tr>
                  <tr>
                    <td className="meta-name">Tags</td>
                    {selectedRunsData.map(run => (
                      <td key={run._id}>
                        <div className="tags-cell">
                          {run.tags.map(tag => (
                            <span key={tag} className="tag">{tag}</span>
                          ))}
                        </div>
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default RunComparison;
