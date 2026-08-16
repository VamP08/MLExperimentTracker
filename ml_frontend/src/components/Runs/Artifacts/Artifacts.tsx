import React, { useEffect, useState, useCallback } from 'react';
import './Artifacts.css';
import { apiFetch } from '../../../lib/api';

interface Artifact {
  _id: string;
  name: string;
  type: string;
  version: string;
  createdAt: string;
  fileCount: number;
  metadata?: Record<string, unknown>;
}

interface Props {
  runId: string;
}

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

  const formatDate = (dateString: string): string => {
    return new Date(dateString).toLocaleString();
  };

  const toggleArtifactDetails = (artifactId: string) => {
    setExpandedArtifact(expandedArtifact === artifactId ? null : artifactId);
  };

  if (loading) {
    return <div className="artifacts-loading">Loading artifacts...</div>;
  }

  if (error) {
    return <div className="artifacts-error">Error: {error}</div>;
  }

  if (artifacts.length === 0) {
    return <div className="artifacts-empty">No artifacts saved for this run</div>;
  }

  return (
    <div className="artifacts">
      <h3>Artifacts ({artifacts.length})</h3>
      
      <div className="artifacts-list">
        {artifacts.map((artifact) => (
          <div key={artifact._id} className="artifact-card">
            <div 
              className="artifact-header"
              onClick={() => toggleArtifactDetails(artifact._id)}
            >
              <div className="artifact-info">
                <h4>{artifact.name}</h4>
                <div className="artifact-badges">
                  <span className="badge type">{artifact.type}</span>
                  <span className="badge version">v{artifact.version}</span>
                  {artifact.fileCount > 0 && (
                    <span className="badge files">{artifact.fileCount} files</span>
                  )}
                </div>
              </div>
              <div className="artifact-date">
                {formatDate(artifact.createdAt)}
              </div>
            </div>

            {expandedArtifact === artifact._id && artifact.metadata && (
              <div className="artifact-metadata">
                <h5>Metadata</h5>
                <pre>{JSON.stringify(artifact.metadata, null, 2)}</pre>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

export default Artifacts;
