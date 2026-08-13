import React, { useEffect, useState, useCallback } from 'react';
import './FeatureImportance.css';

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
      const response = await fetch(`/api/runs/${runId}/artifacts`);
      
      if (!response.ok) {
        throw new Error(`Failed to fetch artifacts: ${response.statusText}`);
      }

      const artifacts = await response.json();
      
      // Look for feature importance artifact
      const fiArtifact = artifacts.find((art: { type: string }) => 
        art.type === 'feature_importance' || art.type === 'feature_importances'
      );

      if (fiArtifact && fiArtifact.metadata && fiArtifact.metadata.features) {
        setFeatures(fiArtifact.metadata.features as FeatureData[]);
      } else {
        throw new Error('No feature importance data found');
      }
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

  if (loading) {
    return <div className="feature-importance loading">Loading feature importance data...</div>;
  }

  if (error) {
    return <div className="feature-importance error">Error: {error}</div>;
  }

  if (features.length === 0) {
    return <div className="feature-importance empty">No feature importance data available.</div>;
  }

  return (
    <div className="feature-importance">
      <div className="fi-header">
        <h3>Feature Importance Analysis</h3>
        
        <div className="fi-controls">
          <div className="control-group">
            <label>Sort by:</label>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value as 'importance' | 'name')}>
              <option value="importance">Importance (high to low)</option>
              <option value="name">Name (alphabetical)</option>
            </select>
          </div>
          
          <div className="control-group">
            <label>Show top:</label>
            <select value={showTop} onChange={(e) => setShowTop(parseInt(e.target.value))}>
              <option value="10">10 features</option>
              <option value="20">20 features</option>
              <option value="50">50 features</option>
              <option value={features.length}>All {features.length} features</option>
            </select>
          </div>
        </div>
      </div>

      <div className="fi-summary">
        <div className="summary-stat">
          <span className="stat-label">Total Features:</span>
          <span className="stat-value">{features.length}</span>
        </div>
        <div className="summary-stat">
          <span className="stat-label">Showing:</span>
          <span className="stat-value">{displayedFeatures.length}</span>
        </div>
        <div className="summary-stat">
          <span className="stat-label">Top 5 Coverage:</span>
          <span className="stat-value">
            {((sortedFeatures.slice(0, 5).reduce((sum, f) => sum + f.importance, 0) / totalImportance) * 100).toFixed(1)}%
          </span>
        </div>
      </div>

      <div className="fi-chart">
        <div className="chart-bars">
          {displayedFeatures.map((feature, idx) => {
            const percentage = (feature.importance / maxImportance) * 100;
            const contribution = (feature.importance / totalImportance) * 100;
            
            return (
              <div key={feature.name} className="bar-item">
                <div className="bar-label">
                  <span className="feature-rank">#{idx + 1}</span>
                  <span className="feature-name" title={feature.name}>{feature.name}</span>
                </div>
                <div className="bar-container">
                  <div 
                    className="bar-fill"
                    style={{ width: `${percentage}%` }}
                  >
                    <span className="bar-value">
                      {feature.importance.toFixed(4)}
                      {feature.std && ` ± ${feature.std.toFixed(4)}`}
                    </span>
                  </div>
                  <span className="bar-percentage">{contribution.toFixed(1)}%</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="fi-table-section">
        <h4>Detailed Statistics</h4>
        <div className="table-wrapper">
          <table className="fi-table">
            <thead>
              <tr>
                <th>Rank</th>
                <th>Feature Name</th>
                <th>Importance</th>
                {features.some(f => f.std !== undefined) && <th>Std Dev</th>}
                <th>Contribution (%)</th>
                <th>Cumulative (%)</th>
              </tr>
            </thead>
            <tbody>
              {displayedFeatures.map((feature, idx) => {
                const contribution = (feature.importance / totalImportance) * 100;
                const cumulative = (sortedFeatures
                  .slice(0, idx + 1)
                  .reduce((sum, f) => sum + f.importance, 0) / totalImportance) * 100;
                
                return (
                  <tr key={feature.name}>
                    <td className="rank-cell">#{idx + 1}</td>
                    <td className="name-cell">{feature.name}</td>
                    <td className="importance-cell">{feature.importance.toFixed(4)}</td>
                    {features.some(f => f.std !== undefined) && (
                      <td className="std-cell">{feature.std ? feature.std.toFixed(4) : 'N/A'}</td>
                    )}
                    <td className="contribution-cell">{contribution.toFixed(2)}%</td>
                    <td className="cumulative-cell">
                      <div className="cumulative-bar-wrapper">
                        <div className="cumulative-bar" style={{ width: `${cumulative}%` }} />
                        <span className="cumulative-text">{cumulative.toFixed(1)}%</span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {sortBy === 'importance' && displayedFeatures.length >= 10 && (
        <div className="fi-insights">
          <h4>Insights</h4>
          <ul>
            <li>
              Top feature <strong>{sortedFeatures[0].name}</strong> contributes{' '}
              <strong>{((sortedFeatures[0].importance / totalImportance) * 100).toFixed(1)}%</strong> of total importance
            </li>
            <li>
              Top 5 features account for{' '}
              <strong>
                {((sortedFeatures.slice(0, 5).reduce((sum, f) => sum + f.importance, 0) / totalImportance) * 100).toFixed(1)}%
              </strong>{' '}
              of total importance
            </li>
            <li>
              Top 10 features account for{' '}
              <strong>
                {((sortedFeatures.slice(0, 10).reduce((sum, f) => sum + f.importance, 0) / totalImportance) * 100).toFixed(1)}%
              </strong>{' '}
              of total importance
            </li>
          </ul>
        </div>
      )}
    </div>
  );
};

export default FeatureImportance;
