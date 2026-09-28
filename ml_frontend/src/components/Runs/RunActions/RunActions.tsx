import { useEffect, useRef, useState } from "react";
import { FiDownload, FiFileText, FiGitCommit, FiMoreHorizontal, FiRefreshCw } from "react-icons/fi";
import { IS_DEMO } from "../../../lib/api";
import { downloadFrom } from "../../../lib/download";
import "./RunActions.css";

interface RunActionsProps {
  runId: string;
  canVerify: boolean;
  hasPatch: boolean;
  onVerify: () => void;
}

/**
 * Run actions: re-run verification and the three exports. Log and patch downloads stream
 * from disk, so the static demo disables them.
 */
const RunActions = ({ runId, canVerify, hasPatch, onVerify }: RunActionsProps) => {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const run = (path: string, name: string) => async () => {
    setOpen(false);
    setError(null);
    try {
      await downloadFrom(path, name);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
    }
  };

  const safe = runId.replace(/[^A-Za-z0-9._-]/g, "_");

  return (
    <div className="run-actions" ref={root}>
      <button type="button" className="btn" onClick={onVerify} disabled={!canVerify} title={canVerify ? "Re-check the recorded world" : "This run has no provenance manifest"}>
        <FiRefreshCw />
        Verify again
      </button>
      <button type="button" className="btn btn-icon" aria-label="More actions" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <FiMoreHorizontal />
      </button>
      {open && (
        <div className="run-menu" role="menu">
          <button type="button" role="menuitem" onClick={run(`/api/run/${runId}/metrics/export?format=csv`, `metrics_${safe}.csv`)}>
            <FiDownload />
            Export metrics as CSV
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={IS_DEMO}
            title={IS_DEMO ? "Not available in the static demo" : undefined}
            onClick={run(`/api/run/${runId}/logs/download`, `${safe}_logs.txt`)}
          >
            <FiFileText />
            Download logs
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={IS_DEMO || !hasPatch}
            title={IS_DEMO ? "Not available in the static demo" : !hasPatch ? "No uncommitted patch was recorded" : undefined}
            onClick={run(`/api/run/${runId}/patch`, `${safe}_uncommitted.patch`)}
          >
            <FiGitCommit />
            Download uncommitted patch
          </button>
        </div>
      )}
      {error && (
        <span className="run-actions-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
};

export default RunActions;
