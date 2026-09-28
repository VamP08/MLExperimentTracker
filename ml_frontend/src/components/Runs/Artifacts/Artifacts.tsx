import React, { Fragment, useEffect, useState, useCallback } from 'react';
import { FiChevronDown, FiChevronRight } from 'react-icons/fi';
import './Artifacts.css';
import { apiFetch } from '../../../lib/api';

interface Artifact {
  _id: string;
  name: string;
  type: string;
  version: string;
  createdAt: string | null;
  fileCount: number;
  metadata?: Record<string, unknown>;
}

interface Props {
  runId: string;
}

const formatDate = (value: string | null): string => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
};

const Artifacts: React.FC<Props> = ({ runId }) => {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedArtifact, setExpandedArtifact] = useState<string | null>(null);

  const fetchArtifacts = useCallback(async () => {
    try {
      setLoading(true);
      const response = await apiFetch(`/api/run/${runId}/artifacts`);

      if (!response.ok) {
        throw new Error('Failed to fetch artifacts');
      }

      const data = await response.json();
      setArtifacts(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchArtifacts();
  }, [runId, fetchArtifacts]);

  const toggleArtifactDetails = (artifactId: string) => {
    setExpandedArtifact(expandedArtifact === artifactId ? null : artifactId);
  };

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="artifacts-title">
        <div className="panel-head">
          <h2 id="artifacts-title">Artifacts</h2>
          {!loading && !error && <span className="sub num">{artifacts.length}</span>}
          <span className="spacer" />
          <span className="sub">Listed from the run record. Artifacts have no stored path, so they cannot be downloaded.</span>
        </div>
        {loading ? (
          <div className="state">Loading artifacts…</div>
        ) : error ? (
          <div className="state error">Could not load artifacts: {error}</div>
        ) : artifacts.length === 0 ? (
          <div className="state">
            <h3>No artifacts saved</h3>
            <p>
              This run recorded none. Call <code>run.log_artifact()</code> to add one.
            </p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table artifacts-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Version</th>
                  <th>Created</th>
                  <th className="r">Files</th>
                  <th>
                    <span className="sr-only">Metadata</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {artifacts.map((artifact) => {
                  const hasMetadata = Boolean(artifact.metadata && Object.keys(artifact.metadata).length > 0);
                  const open = expandedArtifact === artifact._id && hasMetadata;
                  return (
                    <Fragment key={artifact._id}>
                      <tr>
                        <td>{artifact.name}</td>
                        <td>
                          <span className="artifacts-type">{artifact.type}</span>
                        </td>
                        <td className="mono">{artifact.version}</td>
                        <td className="artifacts-nowrap">{formatDate(artifact.createdAt)}</td>
                        <td className="r">{artifact.fileCount}</td>
                        <td className="r">
                          {hasMetadata && (
                            <button
                              type="button"
                              className="btn btn-ghost artifacts-toggle"
                              aria-expanded={open}
                              onClick={() => toggleArtifactDetails(artifact._id)}
                            >
                              {open ? <FiChevronDown /> : <FiChevronRight />}
                              Metadata
                            </button>
                          )}
                        </td>
                      </tr>
                      {open && (
                        <tr className="artifacts-meta-row">
                          <td colSpan={6}>
                            <pre className="artifacts-meta">{JSON.stringify(artifact.metadata, null, 2)}</pre>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
};

export default Artifacts;
