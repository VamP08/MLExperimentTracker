import { useEffect, useMemo, useRef, useState } from "react";
import RunComparisonModal from "../../../components/Experiment/RunComparison/RunComparisonModal";
import "./Runs.css";

interface RunsProps {
  experimentId: string;
  onRunSelect: (runId: string) => void;
}

/**
 * One row of `GET /api/experiment/:id/runs`. `metrics` and `parameters` are
 * built from whatever keys the run happened to log, so neither has a fixed
 * shape — the columns below are derived from the rows rather than declared
 * (GAPS N22). `status` is the mapped UI status, which includes `archived` for
 * an interrupted run.
 */
interface Run {
  _id: string;
  name: string;
  status: string;
  duration: string;
  startTime: string | null;
  metrics: Record<string, unknown>;
  parameters: Record<string, unknown>;
}

/**
 * Columns shown first when the data has them, so the ordinary training run —
 * loss and accuracy against learning rate, batch size and epochs — looks the
 * way it always did. Anything else the run logged follows, alphabetically.
 */
const LEADING_METRIC_COLUMNS = ["loss", "accuracy"];
const LEADING_PARAM_COLUMNS = ["learningRate", "batchSize", "epochs"];

const MIN_COMPARE = 2;
const MAX_COMPARE = 4;

const deriveColumns = (
  rows: Run[],
  pick: (run: Run) => Record<string, unknown>,
  leading: string[],
): string[] => {
  const present = new Set<string>();
  rows.forEach((row) => Object.keys(pick(row) || {}).forEach((key) => present.add(key)));

  const head = leading.filter((key) => present.has(key));
  const tail = Array.from(present)
    .filter((key) => !head.includes(key))
    .sort((a, b) => a.localeCompare(b));

  return [...head, ...tail];
};

/** `learningRate` reads as "Learning Rate"; `loss` stays "Loss". */
const columnLabel = (key: string): string =>
  key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/^./, (first) => first.toUpperCase());

const formatCell = (value: unknown): string => {
  if (value === undefined || value === null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const statusClass = (status: string): string => {
  switch (status) {
    case "completed":
      return "status-completed";
    case "running":
      return "status-running";
    case "failed":
      return "status-failed";
    default:
      return "status-archived";
  }
};

const Runs = ({ experimentId, onRunSelect }: RunsProps) => {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRuns, setSelectedRuns] = useState<Set<string>>(new Set());
  const [comparing, setComparing] = useState(false);
  const selectAllRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!experimentId) return;

    const fetchRuns = async () => {
      try {
        setLoading(true);
        setError(null);

        const res = await fetch(`/api/experiment/${experimentId}/runs`);
        if (!res.ok) {
          throw new Error(`Request failed with ${res.status}`);
        }

        const data = await res.json();
        setRuns(Array.isArray(data) ? data : []);
        setSelectedRuns(new Set());
      } catch {
        setError("Failed to fetch runs. Please try again.");
        setRuns([]);
      } finally {
        setLoading(false);
      }
    };

    fetchRuns();
  }, [experimentId]);

  const metricColumns = useMemo(
    () => deriveColumns(runs, (run) => run.metrics, LEADING_METRIC_COLUMNS),
    [runs],
  );
  const paramColumns = useMemo(
    () => deriveColumns(runs, (run) => run.parameters, LEADING_PARAM_COLUMNS),
    [runs],
  );

  const allSelected = runs.length > 0 && selectedRuns.size === runs.length;
  const someSelected = selectedRuns.size > 0 && !allSelected;

  // `indeterminate` has no HTML attribute; it is a DOM property only.
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someSelected;
  }, [someSelected]);

  const toggleRunSelection = (runId: string) => {
    setSelectedRuns((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedRuns(allSelected ? new Set() : new Set(runs.map((run) => run._id)));
  };

  const canCompare =
    selectedRuns.size >= MIN_COMPARE && selectedRuns.size <= MAX_COMPARE;

  if (loading) return <div className="runs-loading">Loading runs...</div>;
  if (error) return <div className="runs-error">{error}</div>;

  return (
    <div className="runs-container">
      <div className="runs-content">
        <div className="runs-header">
          <h2 className="runs-title">Experiment Runs</h2>
          {selectedRuns.size > 0 && (
            <div className="runs-actions">
              <span className="runs-selected-count">
                {selectedRuns.size} run{selectedRuns.size !== 1 ? "s" : ""} selected
              </span>
              <button
                className="runs-clear-button"
                onClick={() => setSelectedRuns(new Set())}
              >
                Clear Selection
              </button>
              {canCompare && (
                <button
                  className="runs-compare-button"
                  onClick={() => setComparing(true)}
                >
                  Compare Selected Runs
                </button>
              )}
            </div>
          )}
        </div>

        {runs.length === 0 ? (
          <p className="runs-empty">
            This experiment has no runs yet. A run appears here once its directory
            contains a <code>metadata.json</code>.
          </p>
        ) : (
          <div className="table-container">
            <table className="runs-table">
              <thead>
                <tr className="header-main">
                  <th className="header-cell header-cell-fixed border-right" rowSpan={2}>
                    <input
                      type="checkbox"
                      ref={selectAllRef}
                      checked={allSelected}
                      onChange={toggleSelectAll}
                      aria-label={allSelected ? "Clear selection" : "Select all runs"}
                    />
                  </th>
                  <th
                    className="header-cell header-cell-run border-right-thick"
                    rowSpan={2}
                  >
                    Run
                  </th>
                  <th className="header-cell status-group border-right-thick" colSpan={2}>
                    Status
                  </th>
                  {metricColumns.length > 0 && (
                    <th
                      className="header-cell metrics-group border-right-thick"
                      colSpan={metricColumns.length}
                    >
                      Metrics
                    </th>
                  )}
                  {paramColumns.length > 0 && (
                    <th
                      className="header-cell parameters-group border-right-thick"
                      colSpan={paramColumns.length}
                    >
                      Parameters
                    </th>
                  )}
                </tr>

                <tr className="header-sub">
                  <th className="header-cell border-right">Status</th>
                  <th className="header-cell border-right-thick">Duration</th>
                  {metricColumns.map((column, index) => (
                    <th
                      key={`metric-${column}`}
                      className={`header-cell ${
                        index === metricColumns.length - 1
                          ? "border-right-thick"
                          : "border-right"
                      }`}
                    >
                      {columnLabel(column)}
                    </th>
                  ))}
                  {paramColumns.map((column, index) => (
                    <th
                      key={`param-${column}`}
                      className={`header-cell ${
                        index === paramColumns.length - 1
                          ? "border-right-thick"
                          : "border-right"
                      }`}
                    >
                      {columnLabel(column)}
                    </th>
                  ))}
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
                        aria-label={`Select ${run.name}`}
                      />
                    </td>
                    <td
                      className="cell cell-run border-right-thick"
                      onClick={() => onRunSelect(run._id)}
                    >
                      {run.name}
                    </td>
                    <td className="cell border-right">
                      <span className={`status-badge ${statusClass(run.status)}`}>
                        {run.status}
                      </span>
                    </td>
                    <td className="cell border-right-thick">{run.duration}</td>
                    {metricColumns.map((column, index) => (
                      <td
                        key={`metric-${column}`}
                        className={`cell ${
                          index === metricColumns.length - 1
                            ? "border-right-thick"
                            : "border-right"
                        }`}
                      >
                        {formatCell(run.metrics?.[column])}
                      </td>
                    ))}
                    {paramColumns.map((column, index) => (
                      <td
                        key={`param-${column}`}
                        className={`cell ${
                          index === paramColumns.length - 1
                            ? "border-right-thick"
                            : "border-right"
                        }`}
                      >
                        {formatCell(run.parameters?.[column])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {comparing && (
        <RunComparisonModal
          experimentId={experimentId}
          selectedRunIds={Array.from(selectedRuns)}
          onClose={() => setComparing(false)}
        />
      )}
    </div>
  );
};

export default Runs;
