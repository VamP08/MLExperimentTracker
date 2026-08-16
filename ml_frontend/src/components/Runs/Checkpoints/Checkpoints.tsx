import React, { useEffect, useState, useCallback } from 'react';
import './Checkpoints.css';
import { apiFetch } from '../../../lib/api';

interface Checkpoint {
  name: string;
  path: string;
  createdAt: string;
  step?: number;
  size: number;
}

interface Props {
  runId: string;
}

const Checkpoints: React.FC<Props> = ({ runId }) => {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchCheckpoints = useCallback(async () => {
    try {
      setLoading(true);
      const response = await apiFetch(`/api/run/${runId}/checkpoints`);
      
      if (!response.ok) {
        throw new Error('Failed to fetch checkpoints');
      }

      const data = await response.json();
      setCheckpoints(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchCheckpoints();
  }, [runId, fetchCheckpoints]);

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  const formatDate = (dateString: string): string => {
    return new Date(dateString).toLocaleString();
  };

  if (loading) {
    return <div className="checkpoints-loading">Loading checkpoints...</div>;
  }

  if (error) {
    return <div className="checkpoints-error">Error: {error}</div>;
  }

  if (checkpoints.length === 0) {
    return <div className="checkpoints-empty">No checkpoints saved for this run</div>;
  }

  return (
    <div className="checkpoints">
      <h3>Checkpoints ({checkpoints.length})</h3>
      
      <div className="checkpoints-list">
        {checkpoints.map((checkpoint, idx) => (
          <div key={idx} className="checkpoint-card">
            <div className="checkpoint-header">
              <h4>{checkpoint.name}</h4>
              {checkpoint.step !== undefined && (
                <span className="checkpoint-step">Step {checkpoint.step}</span>
              )}
            </div>
            <div className="checkpoint-details">
              <div className="detail-row">
                <span className="label">Created:</span>
                <span className="value">{formatDate(checkpoint.createdAt)}</span>
              </div>
              <div className="detail-row">
                <span className="label">Size:</span>
                <span className="value">{formatSize(checkpoint.size)}</span>
              </div>
              <div className="detail-row">
                <span className="label">Path:</span>
                <span className="value path">{checkpoint.path}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Checkpoints;
