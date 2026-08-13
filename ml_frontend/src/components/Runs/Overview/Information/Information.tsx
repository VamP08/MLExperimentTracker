"use client";

import type React from "react";
import "./Information.css";

interface RunInfoProps {
  runInfo: {
    _id: string;
    Starttime: string;
    Endtime: string;
    duration: string;
    status: string;
  };
}

const Information: React.FC<RunInfoProps> = ({ runInfo }) => {
  const handleCopyId = () => {
    navigator.clipboard
      .writeText(runInfo._id)
      .then(() => alert("Run ID copied to clipboard"))
      .catch((err) => console.error("Failed to copy ID:", err));
  };

  const formatDuration = (secondsString: string): string => {
    const totalSeconds = parseFloat(secondsString);
    if (isNaN(totalSeconds)) return "N/A";

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
            <button className="copy-button" onClick={handleCopyId} title="Copy ID">
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
          <span className="item-value">{runInfo.Starttime}</span>
        </div>

        <div className="information-item">
          <span className="item-label">End Time</span>
          <span className="item-value">{runInfo.Endtime}</span>
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
