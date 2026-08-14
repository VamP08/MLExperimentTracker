import React, { useEffect, useRef, useState } from "react";
import "./Information.css";

interface RunInfoProps {
  // GAPS M13: `startTime` and `endTime` are the keys the API sends. The previous
  // `Starttime`/`Endtime` matched nothing and both fields were permanently blank.
  runInfo: {
    _id: string;
    startTime: string | null;
    endTime: string | null;
    duration: number;
    status: string;
  };
}

const Information: React.FC<RunInfoProps> = ({ runInfo }) => {
  const [copied, setCopied] = useState(false);
  const copyTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(copyTimer.current), []);

  const handleCopyId = () => {
    navigator.clipboard
      .writeText(runInfo._id)
      .then(() => {
        setCopied(true);
        window.clearTimeout(copyTimer.current);
        copyTimer.current = window.setTimeout(() => setCopied(false), 2000);
      })
      .catch((err) => console.error("Failed to copy ID:", err));
  };

  // An absent end time is the normal state of a running run, so it reads as a
  // dash rather than as a missing value.
  const formatTimestamp = (value: string | null): string => {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  };

  const formatDuration = (seconds: number): string => {
    const totalSeconds = Number(seconds);
    if (!Number.isFinite(totalSeconds)) return "N/A";

    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);

    const hLabel = hours > 0 ? `${hours} hr${hours !== 1 ? "s" : ""}` : "";
    const mLabel = minutes > 0 ? `${minutes} min${minutes !== 1 ? "s" : ""}` : "";

    return hLabel || mLabel ? `${hLabel} ${mLabel}`.trim() : "Less than a minute";
  };

  return (
    <div className="run-information">
      <h3 className="information-title">Run Information</h3>

      <div className="information-grid">
        <div className="information-item">
          <span className="item-label">Run ID</span>
          <div className="id-container">
            <span className="item-value id-value" title={runInfo._id}>
              {runInfo._id}
            </span>
            <button
              className="copy-button"
              onClick={handleCopyId}
              title="Copy ID"
              aria-label="Copy run ID"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
            </button>
            <span
              className={`information-copied ${copied ? "is-visible" : ""}`}
              role="status"
              aria-live="polite"
            >
              {copied ? "Copied" : ""}
            </span>
          </div>
        </div>

        <div className="information-item">
          <span className="item-label">Status</span>
          <span className={`status-badge ${runInfo.status.toLowerCase()}`}>
            {runInfo.status}
          </span>
        </div>

        <div className="information-item">
          <span className="item-label">Start Time</span>
          <span className="item-value">{formatTimestamp(runInfo.startTime)}</span>
        </div>

        <div className="information-item">
          <span className="item-label">End Time</span>
          <span className="item-value">{formatTimestamp(runInfo.endTime)}</span>
        </div>

        <div className="information-item">
          <span className="item-label">Duration</span>
          <span className="item-value">{formatDuration(runInfo.duration)}</span>
        </div>
      </div>
    </div>
  );
};

export default Information;
