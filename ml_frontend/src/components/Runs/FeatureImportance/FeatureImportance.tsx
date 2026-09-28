import React, { useEffect, useState, useCallback } from 'react';
import './FeatureImportance.css';
import { apiFetch } from '../../../lib/api';

interface FeatureData {
  name: string;
  importance: number;
  std?: number;
}

interface Props {
  runId: string;
}

const FeatureImportance: React.FC<Props> = ({ runId }) => {
  const [features, setFeatures] = useState<FeatureData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<'importance' | 'name'>('importance');
  const [showTop, setShowTop] = useState<number>(20);

  const fetchFeatureImportance = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await apiFetch(`/api/run/${runId}/artifacts`);

      if (!response.ok) {
        throw new Error(`Failed to fetch artifacts: ${response.statusText}`);
      }

      const artifacts = await response.json();
      const records: Array<{ type?: string; metadata?: { features?: unknown } }> = Array.isArray(
        artifacts
      )
        ? artifacts
        : [];

      const fiArtifact = records.find(
        (art) => art.type === 'feature_importance' || art.type === 'feature_importances'
      );

      const raw = fiArtifact?.metadata?.features;
      // No feature importances is normal, not an error.
      setFeatures(
        Array.isArray(raw)
          ? (raw as FeatureData[]).filter(
              (feature) =>
                feature &&
                typeof feature.name === 'string' &&
                typeof feature.importance === 'number' &&
                Number.isFinite(feature.importance)
            )
          : []
      );
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchFeatureImportance();
  }, [fetchFeatureImportance]);

  const sortedFeatures = [...features].sort((a, b) => {
    if (sortBy === 'importance') {
      return b.importance - a.importance;
    } else {
      return a.name.localeCompare(b.name);
    }
  });

  const displayedFeatures = sortedFeatures.slice(0, showTop);
  const maxImportance = Math.max(...features.map(f => f.importance));
  const totalImportance = features.reduce((sum, f) => sum + f.importance, 0);

  const shell = (body: React.ReactNode, sub?: React.ReactNode, controls?: React.ReactNode) => (
    <section className="panel" aria-labelledby="fi-head">
      <div className="panel-head">
        <h2 id="fi-head">Feature importance</h2>
        {sub}
        <span className="spacer" />
        {controls}
      </div>
      {body}
    </section>
  );

  if (loading) {
    return shell(<div className="state">Loading feature importance data…</div>);
  }

  if (error) {
    return shell(<div className="state error">Could not load feature importances: {error}</div>);
  }

  // Nothing to draw. The Evaluation tab shows the empty message.
  if (features.length === 0) {
    return null;
  }

  const hasStd = features.some((f) => f.std !== undefined);
  const share = (value: number) => (value / totalImportance) * 100;
  const topFive = share(sortedFeatures.slice(0, 5).reduce((sum, f) => sum + f.importance, 0));

  return shell(
    <>
      <div className="panel-body">
        <ol className="fi-bars">
          {displayedFeatures.map((feature) => (
            <li key={feature.name} className="fi-bar">
              <span className="fi-name" title={feature.name}>
                {feature.name}
              </span>
              <span className="fi-track" aria-hidden="true">
                <span className="fi-fill" style={{ width: `${maxImportance > 0 ? (feature.importance / maxImportance) * 100 : 0}%` }} />
              </span>
              <span className="fi-value mono">
                {feature.importance.toFixed(4)}
                {typeof feature.std === 'number' && <span className="muted"> ± {feature.std.toFixed(4)}</span>}
              </span>
            </li>
          ))}
        </ol>
      </div>

      <div className="table-wrap fi-table">
        <table className="table">
          <thead>
            <tr>
              <th className="r">Rank</th>
              <th>Feature</th>
              <th className="r">Importance</th>
              {hasStd && <th className="r">Std dev</th>}
              <th className="r">Share</th>
              <th className="r">Cumulative</th>
            </tr>
          </thead>
          <tbody>
            {displayedFeatures.map((feature, idx) => {
              const cumulative = share(sortedFeatures.slice(0, idx + 1).reduce((sum, f) => sum + f.importance, 0));
              return (
                <tr key={feature.name}>
                  <td className="r mono muted">{idx + 1}</td>
                  <td>{feature.name}</td>
                  <td className="r mono">{feature.importance.toFixed(4)}</td>
                  {hasStd && <td className="r mono">{typeof feature.std === 'number' ? feature.std.toFixed(4) : '—'}</td>}
                  <td className="r mono">{share(feature.importance).toFixed(2)}%</td>
                  <td className="r mono">{cumulative.toFixed(1)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>,
    <span className="sub num">
      {displayedFeatures.length} of {features.length} · top 5 hold {topFive.toFixed(1)}%
    </span>,
    <>
      <label>
        <span className="sr-only">Sort by</span>
        <select className="select" value={sortBy} onChange={(e) => setSortBy(e.target.value as 'importance' | 'name')}>
          <option value="importance">Importance, high to low</option>
          <option value="name">Name, A to Z</option>
        </select>
      </label>
      <label>
        <span className="sr-only">Show</span>
        <select className="select" value={showTop} onChange={(e) => setShowTop(parseInt(e.target.value))}>
          <option value="10">Top 10</option>
          <option value="20">Top 20</option>
          <option value="50">Top 50</option>
          <option value={features.length}>All {features.length}</option>
        </select>
      </label>
    </>,
  );
};

export default FeatureImportance;
