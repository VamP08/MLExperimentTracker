"use client";

import { useEffect, useState, useRef } from "react";
import "./Runs.css";

interface RunsProps {
  experimentId: string;
  onRunSelect: (runId: string) => void;
}

interface RunMetric {
  loss: number;
  accuracy: number;
}

interface Run {
  _id: string;
  name: string;
  status: "completed" | "running" | "failed";
  duration: string;
  startTime: string;
  metrics: RunMetric;
  parameters: {
    learningRate: number;
    batchSize: number;
    epochs: number;
  };
}

const Runs = ({ experimentId,onRunSelect }: RunsProps) => {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRuns, setSelectedRuns] = useState<Set<string>>(new Set());
  const tableContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!experimentId) return;

    const fetchRuns = async () => {
      try {
        const res = await fetch(`/api/experiment/${experimentId}/runs`);
        const data = await res.json();
        setRuns(data);
      } catch {
        setError("Failed to fetch runs. Please try again.");
      } finally {
        setLoading(false);
      }
    };

    fetchRuns();
  }, [experimentId]);

  const toggleRunSelection = (runId: string) => {
    const newSelectedRuns = new Set(selectedRuns);
    if (newSelectedRuns.has(runId)) {
      newSelectedRuns.delete(runId);
    } else {
      newSelectedRuns.add(runId);
    }
    setSelectedRuns(newSelectedRuns);
  };

  const handleRunClick = (runId: string) => {
    onRunSelect(runId);
  };

  if (loading) return <div className="runs-loading">Loading runs...</div>;
  if (error) return <div className="runs-error">{error}</div>;

  return (
    <div className="runs-container">
      <div className="runs-content">
        <div className="runs-header">
          <h2 className="runs-title">Experiment Runs</h2>
          {selectedRuns.size > 0 && (
            <div className="runs-actions">
              <span className="selected-count">{selectedRuns.size} run{selectedRuns.size !== 1 ? 's' : ''} selected</span>
              <button 
                className="clear-selection-button"
                onClick={() => setSelectedRuns(new Set())}
              >
                Clear Selection
              </button>
              {selectedRuns.size >= 2 && selectedRuns.size <= 4 && (
                <button 
                  className="compare-button"
                  onClick={() => {/* TODO: Open comparison modal */}}
                >
                  Compare Selected Runs
                </button>
              )}
            </div>
          )}
        </div>

        <div ref={tableContainerRef} className="table-container">
          <table className="runs-table">
            <thead>
              <tr className="header-main">
                <th className="header-cell header-cell-fixed border-right" rowSpan={2}>
                  <input type="checkbox" className="h-4 w-4" onChange={() => {}} />
                </th>
                <th className="header-cell header-cell-run border-right-thick" rowSpan={2}>
                  Run
                </th>
                <th className="header-cell status-group border-right-thick" colSpan={2}>
                  Status
                </th>
                <th className="header-cell metrics-group border-right-thick" colSpan={2}>
                  Metrics
                </th>
                <th className="header-cell parameters-group border-right-thick" colSpan={3}>
                  Parameters
                </th>
              </tr>

              <tr className="header-sub">
                <th className="header-cell border-right">Status</th>
                <th className="header-cell border-right-thick">Duration</th>
                <th className="header-cell border-right">Loss</th>
                <th className="header-cell border-right-thick">Accuracy</th>
                <th className="header-cell border-right">Learning Rate</th>
                <th className="header-cell border-right">Batch Size</th>
                <th className="header-cell border-right-thick">Epochs</th>
              </tr>
            </thead>

            <tbody>
              {runs.map((run) => (
                <tr key={run._id}>
                  <td className="cell border-right">
                    <input
                      type="checkbox"
                      checked={selectedRuns.has(run._id)}
                      onChange={() => toggleRunSelection(run._id)}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </td>
                  <td className="cell cell-run border-right-thick" onClick={() => handleRunClick(run._id)}>
                    {run.name}
                  </td>
                  <td className="cell border-right">
                    <span
                      className={`status-badge ${
                        run.status === "completed"
                          ? "status-completed"
                          : run.status === "running"
                          ? "status-running"
                          : "status-failed"
                      }`}
                    >
                      {run.status}
                    </span>
                  </td>
                  <td className="cell border-right-thick">{run.duration}</td>
                  <td className="cell border-right">{run.metrics.loss}</td>
                  <td className="cell border-right-thick">{run.metrics.accuracy}</td>
                  <td className="cell border-right">{run.parameters.learningRate}</td>
                  <td className="cell border-right">{run.parameters.batchSize}</td>
                  <td className="cell border-right-thick">{run.parameters.epochs}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default Runs;
