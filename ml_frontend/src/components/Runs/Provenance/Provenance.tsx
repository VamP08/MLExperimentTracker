import React, { useCallback, useEffect, useState } from 'react';
import { FiDownload } from 'react-icons/fi';
import './Provenance.css';
import { apiFetch, IS_DEMO } from '../../../lib/api';
import { downloadFrom } from '../../../lib/download';

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

/** Packages shown before "Show all". */
const PACKAGE_PREVIEW = 12;

const DEMO_PATCH_NOTE =
  'Not available in the static demo: the patch is served from the run directory, and there is no server.';

const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

/**
 * The environment a run was recorded in: source, interpreter, packages, data, machine.
 * Shows the manifest as recorded; drift checks are in the summary above the tabs.
 */
const Provenance: React.FC<Props> = ({ runId }) => {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAllPackages, setShowAllPackages] = useState(false);
  const [packageQuery, setPackageQuery] = useState('');
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const fetchProvenance = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await apiFetch(`/api/run/${runId}/provenance`);

      // Most runs have no manifest (pre-1.1 format, or capture failed). Not an error.
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

  if (loading || error || !manifest) {
    return (
      <div className="stack">
        <section className="panel" aria-labelledby="prov-title">
          <div className="panel-head">
            <h2 id="prov-title">Provenance</h2>
          </div>
          {loading ? (
            <div className="state">Loading provenance…</div>
          ) : error ? (
            <div className="state error">Could not load provenance: {error}</div>
          ) : (
            <div className="state">
              <h3>No provenance recorded</h3>
              <p>
                Runs recorded before format 1.1, runs tracked with <code>provenance=False</code>, and runs whose
                capture failed have no manifest.
              </p>
            </div>
          )}
        </section>
      </div>
    );
  }

  const git = manifest.git ?? {};
  const packages = manifest.packages ?? {};
  const packageNames = Object.keys(packages);
  const gpus = manifest.hardware?.gpus ?? [];
  const cpuCount = manifest.hardware?.cpu_count;
  const datasets = manifest.datasets ?? [];
  const environment = Object.entries(manifest.environment ?? {});
  const argv = manifest.command?.argv ?? [];
  const hasPatch = Boolean(git.diff_file);
  const untracked = git.untracked ?? [];

  const query = packageQuery.trim().toLowerCase();
  const matchingPackages = query ? packageNames.filter((name) => name.toLowerCase().includes(query)) : packageNames;
  const visiblePackages =
    query || showAllPackages ? matchingPackages : matchingPackages.slice(0, PACKAGE_PREVIEW);

  const platform =
    [manifest.platform?.system, manifest.platform?.release, manifest.platform?.machine].filter(Boolean).join(' ') ||
    'unknown';

  const downloadPatch = async () => {
    // Served from the run directory as an attachment, so the demo can't provide it.
    if (IS_DEMO) return;
    setDownloadError(null);
    try {
      const safe = runId.replace(/[^A-Za-z0-9._-]/g, '_');
      await downloadFrom(`/api/run/${runId}/patch`, `${safe}_uncommitted.patch`);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'Download failed');
    }
  };

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="prov-source">
        <div className="panel-head">
          <h2 id="prov-source">Source</h2>
          {manifest.captured_at && (
            <span className="sub">Captured {new Date(manifest.captured_at).toLocaleString()}</span>
          )}
          <span className="spacer" />
          {hasPatch && (
            <button
              type="button"
              className="btn provenance-download"
              onClick={downloadPatch}
              aria-disabled={IS_DEMO || undefined}
              title={IS_DEMO ? DEMO_PATCH_NOTE : undefined}
            >
              <FiDownload />
              Download patch
            </button>
          )}
          {downloadError && (
            <span className="provenance-error" role="alert">
              Could not download the patch: {downloadError}
            </span>
          )}
        </div>
        {git.available ? (
          <table className="kv">
            <tbody>
              <tr>
                <td>Commit</td>
                <td>{git.commit ?? 'none yet'}</td>
              </tr>
              {git.branch && (
                <tr>
                  <td>Branch</td>
                  <td>{git.branch}</td>
                </tr>
              )}
              {git.remote && (
                <tr>
                  <td>Remote</td>
                  <td>{git.remote}</td>
                </tr>
              )}
              <tr>
                <td>Working tree</td>
                <td className="provenance-text">
                  {git.dirty ? (
                    <span className="badge drift">Uncommitted changes</span>
                  ) : (
                    <span className="badge ok">Clean</span>
                  )}
                  {hasPatch && (
                    <span className="muted num provenance-inline">
                      {formatBytes(git.diff_bytes ?? 0)} patch
                      {git.diff_truncated ? ' — truncated, will not apply cleanly' : ''}
                    </span>
                  )}
                </td>
              </tr>
              {untracked.length > 0 && (
                <tr>
                  <td>
                    Untracked files{' '}
                    <span className="num">
                      ({untracked.length}
                      {git.untracked_truncated ? '+, capped' : ''})
                    </span>
                  </td>
                  <td>
                    <ul className="provenance-files">
                      {untracked.map((file) => (
                        <li key={file}>{file}</li>
                      ))}
                    </ul>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        ) : (
          <div className="state">
            <h3>No repository</h3>
            <p>{git.reason ?? 'Git state was not recorded.'}</p>
          </div>
        )}
        {git.available && git.reason && <p className="provenance-reason">{git.reason}</p>}
      </section>

      <section className="panel" aria-labelledby="prov-env">
        <div className="panel-head">
          <h2 id="prov-env">Environment</h2>
        </div>
        <table className="kv">
          <tbody>
            <tr>
              <td>Python</td>
              <td>
                {manifest.python?.version ?? 'unknown'}
                {manifest.python?.implementation ? ` (${manifest.python.implementation})` : ''}
              </td>
            </tr>
            {manifest.python?.executable && (
              <tr>
                <td>Interpreter</td>
                <td>{manifest.python.executable}</td>
              </tr>
            )}
            <tr>
              <td>Platform</td>
              <td>{platform}</td>
            </tr>
            {manifest.platform?.processor && (
              <tr>
                <td>Processor</td>
                <td>{manifest.platform.processor}</td>
              </tr>
            )}
            {manifest.command?.cwd && (
              <tr>
                <td>Working directory</td>
                <td>{manifest.command.cwd}</td>
              </tr>
            )}
          </tbody>
        </table>
        {argv.length > 0 && (
          <div className="panel-body provenance-command">
            <h3>Command line</h3>
            <pre className="provenance-code">{argv.join(' ')}</pre>
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="prov-packages">
        <div className="panel-head">
          <h2 id="prov-packages">Packages</h2>
          <span className="sub num">{packageNames.length}</span>
          <span className="spacer" />
          {packageNames.length > PACKAGE_PREVIEW && (
            <>
              <label className="sr-only" htmlFor="prov-package-filter">
                Filter packages
              </label>
              <input
                id="prov-package-filter"
                className="input provenance-filter"
                type="search"
                placeholder="Filter packages"
                value={packageQuery}
                onChange={(event) => setPackageQuery(event.target.value)}
              />
            </>
          )}
        </div>
        {packageNames.length === 0 ? (
          <div className="state">No installed packages were recorded.</div>
        ) : matchingPackages.length === 0 ? (
          <div className="state">No package matches &ldquo;{packageQuery}&rdquo;.</div>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Version</th>
                  </tr>
                </thead>
                <tbody>
                  {visiblePackages.map((name) => (
                    <tr key={name}>
                      <td className="mono">{name}</td>
                      <td className="mono">{packages[name]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!query && matchingPackages.length > PACKAGE_PREVIEW && (
              <div className="provenance-more">
                <button
                  type="button"
                  className="btn btn-ghost"
                  aria-expanded={showAllPackages}
                  onClick={() => setShowAllPackages((open) => !open)}
                >
                  {showAllPackages ? `Show first ${PACKAGE_PREVIEW}` : `Show all ${matchingPackages.length}`}
                </button>
              </div>
            )}
          </>
        )}
      </section>

      <section className="panel" aria-labelledby="prov-datasets">
        <div className="panel-head">
          <h2 id="prov-datasets">Datasets</h2>
          <span className="sub num">{datasets.length}</span>
        </div>
        {datasets.length === 0 ? (
          <div className="state">
            No datasets were hashed. Pass <code>datasets=[...]</code> to <code>init()</code> to fingerprint them.
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Path</th>
                  <th>Digest</th>
                  <th className="r">Size</th>
                  <th className="r">Files</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map((dataset, index) => {
                  const digest = dataset.digest ?? dataset.sha256;
                  const algorithm = dataset.algorithm ?? 'sha256';
                  return (
                    <tr key={index}>
                      <td className="mono provenance-path">{dataset.path}</td>
                      {dataset.error ? (
                        <td colSpan={3} className="provenance-error">
                          {dataset.error}
                        </td>
                      ) : (
                        <>
                          <td className="mono provenance-digest" title={digest ? `${algorithm}:${digest}` : undefined}>
                            {digest ? (algorithm === 'sha256' ? digest : `${algorithm}:${digest}`) : '—'}
                          </td>
                          <td className="r">{typeof dataset.bytes === 'number' ? formatBytes(dataset.bytes) : '—'}</td>
                          <td className="r">{typeof dataset.files === 'number' ? dataset.files : '—'}</td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {(cpuCount || gpus.length > 0) && (
        <section className="panel" aria-labelledby="prov-hardware">
          <div className="panel-head">
            <h2 id="prov-hardware">Hardware</h2>
          </div>
          <table className="kv">
            <tbody>
              {cpuCount ? (
                <tr>
                  <td>CPUs</td>
                  <td>{cpuCount}</td>
                </tr>
              ) : null}
              {gpus.map((gpu, index) => (
                <tr key={index}>
                  <td>{gpus.length > 1 ? `GPU ${index}` : 'GPU'}</td>
                  <td>
                    {gpu.name ?? 'unknown'}
                    {gpu.memory_total_mb ? ` — ${gpu.memory_total_mb} MB` : ''}
                    {gpu.driver_version ? ` — driver ${gpu.driver_version}` : ''}
                    {gpu.cuda_version ? ` — CUDA ${gpu.cuda_version}` : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="panel" aria-labelledby="prov-vars">
        <div className="panel-head">
          <h2 id="prov-vars">Environment variables</h2>
          <span className="sub">Allowlisted names only</span>
        </div>
        {environment.length === 0 ? (
          <div className="state">None of the allowlisted variables were set when the run started.</div>
        ) : (
          <table className="kv">
            <tbody>
              {environment.map(([name, value]) => (
                <tr key={name}>
                  <td className="mono">{name}</td>
                  <td>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
};

export default Provenance;
