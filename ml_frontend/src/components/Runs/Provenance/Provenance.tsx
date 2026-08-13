import React, { useCallback, useEffect, useState } from 'react';
import './Provenance.css';

interface GitState {
  available?: boolean;
  reason?: string | null;
  commit?: string | null;
  branch?: string | null;
  remote?: string | null;
  dirty?: boolean;
  untracked?: string[];
  untracked_truncated?: boolean;
  diff_file?: string | null;
  diff_sha256?: string | null;
  diff_bytes?: number;
  diff_truncated?: boolean;
}

interface Gpu {
  name?: string | null;
  memory_total_mb?: number | null;
  driver_version?: string | null;
  cuda_version?: string | null;
}

interface Dataset {
  path?: string;
  digest?: string;
  sha256?: string;
  bytes?: number;
  files?: number;
  algorithm?: string;
  error?: string;
}

interface Manifest {
  captured_at?: string;
  git?: GitState;
  python?: { version?: string; implementation?: string; executable?: string };
  platform?: { system?: string; release?: string; machine?: string; processor?: string };
  packages?: Record<string, string>;
  hardware?: { cpu_count?: number | null; gpus?: Gpu[] };
  environment?: Record<string, string>;
  datasets?: Dataset[];
  command?: { argv?: string[]; cwd?: string };
}

interface Props {
  runId: string;
}

const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const shortDigest = (digest: string): string => digest.slice(0, 12);

const Provenance: React.FC<Props> = ({ runId }) => {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showPackages, setShowPackages] = useState(false);

  const fetchProvenance = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch(`/api/run/${runId}/provenance`);

      // Most runs have no manifest: everything written before format 1.1, and every run
      // whose capture failed. That is a normal state, not a failure, and it renders as
      // nothing at all rather than as an empty card the user has to learn to ignore.
      if (response.status === 404) {
        setManifest(null);
        return;
      }
      if (!response.ok) {
        throw new Error('Failed to fetch provenance');
      }
      setManifest(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchProvenance();
  }, [runId, fetchProvenance]);

  if (loading) {
    return <div className="provenance-placeholder">Loading provenance...</div>;
  }

  if (error) {
    return <div className="provenance-placeholder provenance-error">Error: {error}</div>;
  }

  if (!manifest) {
    return null;
  }

  const git = manifest.git ?? {};
  const packages = manifest.packages ?? {};
  const packageNames = Object.keys(packages);
  const gpus = manifest.hardware?.gpus ?? [];
  const datasets = manifest.datasets ?? [];
  const hasPatch = Boolean(git.diff_file);

  const badge = !git.available
    ? { label: 'NO REPOSITORY', className: 'provenance-badge provenance-badge-unknown' }
    : git.dirty
      ? { label: 'UNCOMMITTED CHANGES', className: 'provenance-badge provenance-badge-dirty' }
      : { label: 'CLEAN', className: 'provenance-badge provenance-badge-clean' };

  return (
    <div className="provenance">
      <div className="provenance-header">
        <h3 className="provenance-title">Provenance</h3>
        <span className={badge.className}>{badge.label}</span>
      </div>

      <div className="provenance-section">
        {git.available ? (
          <>
            <div className="provenance-row">
              <span className="provenance-label">Commit</span>
              <span className="provenance-value provenance-mono">
                {git.commit ? shortDigest(git.commit) : 'none yet'}
                {git.branch ? <span className="provenance-branch">{git.branch}</span> : null}
              </span>
            </div>
            {git.remote ? (
              <div className="provenance-row">
                <span className="provenance-label">Remote</span>
                <span className="provenance-value provenance-break">{git.remote}</span>
              </div>
            ) : null}
            {git.untracked && git.untracked.length > 0 ? (
              <div className="provenance-row">
                <span className="provenance-label">Untracked</span>
                <span className="provenance-value">
                  {git.untracked.length}
                  {git.untracked_truncated ? '+ (capped)' : ''} file
                  {git.untracked.length === 1 ? '' : 's'}
                </span>
              </div>
            ) : null}
          </>
        ) : (
          <div className="provenance-row">
            <span className="provenance-label">Git</span>
            <span className="provenance-value">{git.reason ?? 'not recorded'}</span>
          </div>
        )}

        {hasPatch ? (
          <div className="provenance-row">
            <span className="provenance-label">Patch</span>
            <span className="provenance-value">
              <a className="provenance-download" href={`/api/run/${runId}/patch`}>
                Download diff
              </a>
              <span className="provenance-note">
                {formatBytes(git.diff_bytes ?? 0)}
                {git.diff_truncated ? ' — truncated, will not apply cleanly' : ''}
              </span>
            </span>
          </div>
        ) : null}

        {git.available && git.reason ? (
          <p className="provenance-reason">{git.reason}</p>
        ) : null}
      </div>

      <div className="provenance-section">
        <div className="provenance-row">
          <span className="provenance-label">Python</span>
          <span className="provenance-value">
            {manifest.python?.version ?? 'unknown'}
            {manifest.python?.implementation ? ` (${manifest.python.implementation})` : ''}
          </span>
        </div>
        <div className="provenance-row">
          <span className="provenance-label">Platform</span>
          <span className="provenance-value">
            {[manifest.platform?.system, manifest.platform?.release, manifest.platform?.machine]
              .filter(Boolean)
              .join(' ') || 'unknown'}
          </span>
        </div>
        {manifest.hardware?.cpu_count ? (
          <div className="provenance-row">
            <span className="provenance-label">CPUs</span>
            <span className="provenance-value">{manifest.hardware.cpu_count}</span>
          </div>
        ) : null}
        {gpus.map((gpu, index) => (
          <div className="provenance-row" key={index}>
            <span className="provenance-label">GPU</span>
            <span className="provenance-value">
              {gpu.name ?? 'unknown'}
              {gpu.memory_total_mb ? ` — ${gpu.memory_total_mb} MB` : ''}
              {gpu.cuda_version ? ` — CUDA ${gpu.cuda_version}` : ''}
            </span>
          </div>
        ))}
      </div>

      {datasets.length > 0 ? (
        <div className="provenance-section">
          <span className="provenance-subtitle">Datasets</span>
          {datasets.map((dataset, index) => (
            <div className="provenance-dataset" key={index}>
              <span className="provenance-value provenance-break">{dataset.path}</span>
              {dataset.error ? (
                <span className="provenance-note provenance-error">{dataset.error}</span>
              ) : (
                <span className="provenance-note provenance-mono">
                  {dataset.algorithm ?? 'sha256'}:
                  {dataset.digest ? shortDigest(dataset.digest) : '—'}
                  {typeof dataset.bytes === 'number' ? ` — ${formatBytes(dataset.bytes)}` : ''}
                  {typeof dataset.files === 'number' ? ` — ${dataset.files} file(s)` : ''}
                </span>
              )}
            </div>
          ))}
        </div>
      ) : null}

      <div className="provenance-section">
        <div className="provenance-row">
          <span className="provenance-label">Packages</span>
          <span className="provenance-value">
            {packageNames.length}
            {packageNames.length > 0 ? (
              <button
                className="provenance-toggle"
                onClick={() => setShowPackages((open) => !open)}
                aria-expanded={showPackages}
              >
                {showPackages ? 'Hide' : 'Show'}
              </button>
            ) : null}
          </span>
        </div>
        {showPackages ? (
          <ul className="provenance-packages">
            {packageNames.map((name) => (
              <li className="provenance-package" key={name}>
                <span className="provenance-mono">{name}</span>
                <span className="provenance-mono provenance-package-version">
                  {packages[name]}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {manifest.captured_at ? (
        <p className="provenance-captured">
          Captured {new Date(manifest.captured_at).toLocaleString()}
        </p>
      ) : null}
    </div>
  );
};

export default Provenance;
