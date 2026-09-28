import React, { useEffect, useState, useCallback } from 'react';
import './Checkpoints.css';
import { apiFetch } from '../../../lib/api';

interface Checkpoint {
  name: string;
  path: string;
  createdAt: string | null;
  step?: number | null;
  size: number;
}

interface Props {
  runId: string;
}

const formatSize = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
};

const formatDate = (value: string | null): string => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
};

/** Saved checkpoints, newest first (server order). */
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

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="checkpoints-title">
        <div className="panel-head">
          <h2 id="checkpoints-title">Checkpoints</h2>
          {!loading && !error && <span className="sub num">{checkpoints.length}</span>}
        </div>
        {loading ? (
          <div className="state">Loading checkpoints…</div>
        ) : error ? (
          <div className="state error">Could not load checkpoints: {error}</div>
        ) : checkpoints.length === 0 ? (
          <div className="state">
            <h3>No checkpoints saved</h3>
            <p>
              This run recorded none. Call <code>run.log_checkpoint()</code> during training to save them.
            </p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table checkpoints-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th className="r">Step</th>
                  <th>Created</th>
                  <th className="r">Size</th>
                  <th>Path</th>
                </tr>
              </thead>
              <tbody>
                {checkpoints.map((checkpoint, idx) => (
                  <tr key={idx}>
                    <td>{checkpoint.name}</td>
                    <td className="r mono">{typeof checkpoint.step === 'number' ? checkpoint.step : '—'}</td>
                    <td className="checkpoints-nowrap">{formatDate(checkpoint.createdAt)}</td>
                    <td className="r checkpoints-nowrap">{formatSize(checkpoint.size)}</td>
                    <td className="mono checkpoints-path" title={checkpoint.path}>
                      {checkpoint.path}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default Checkpoints;
