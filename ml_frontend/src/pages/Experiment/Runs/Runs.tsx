import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { FiColumns } from "react-icons/fi";
import RunComparisonModal from "../../../components/Experiment/RunComparison/RunComparisonModal";
import { formatWhen, runBadge } from "../../../components/Experiment/experiment";
import "./Runs.css";
import { apiFetch } from "../../../lib/api";

interface RunsProps {
  experimentId: string;
  /** Run names from the experiment payload; the runs endpoint only synthesizes `Run <id>`. */
  runNames?: Record<string, string>;
}

/**
 * One row of `GET /api/experiment/:id/runs`. `metrics` and `parameters` hold whatever the run
 * logged, so columns are derived from the rows. `status` is already mapped (interrupted -> archived).
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

/** Columns shown first when present; anything else follows alphabetically. */
const LEADING_METRIC_COLUMNS = ["loss", "accuracy"];
const LEADING_PARAM_COLUMNS = ["learningRate", "batchSize", "epochs"];

const MIN_COMPARE = 2;
const MAX_COMPARE = 4;

interface Column {
  key: string;
  /** Every value present in the column is a number, so it reads right-aligned. */
  numeric: boolean;
}

const deriveColumns = (
  rows: Run[],
  pick: (run: Run) => Record<string, unknown>,
  leading: string[],
): Column[] => {
  const present = new Set<string>();
  rows.forEach((row) => Object.keys(pick(row) || {}).forEach((key) => present.add(key)));

  const head = leading.filter((key) => present.has(key));
  const tail = Array.from(present)
    .filter((key) => !head.includes(key))
    .sort((a, b) => a.localeCompare(b));

  return [...head, ...tail].map((key) => ({
    key,
    numeric: rows.every((row) => {
      const value = pick(row)?.[key];
      return value === undefined || value === null || typeof value === "number";
    }),
  }));
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

const Runs = ({ experimentId, runNames = {} }: RunsProps) => {
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

        const res = await apiFetch(`/api/experiment/${experimentId}/runs`);
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

  const renderBody = () => {
    if (loading) return <div className="state">Loading runs…</div>;
    if (error) return <div className="state error">{error}</div>;
    if (runs.length === 0) {
      return (
        <div className="state">
          <h3>No runs yet</h3>
          <p>
            A run appears here once its directory contains a <code>metadata.json</code>.
          </p>
        </div>
      );
    }

    return (
      <div className="table-wrap">
        <table className="table exp-runs-table">
          <thead>
            <tr className="exp-group-row">
              <th colSpan={5} aria-hidden="true" />
              {paramColumns.length > 0 && (
                <th colSpan={paramColumns.length} className="exp-group" scope="colgroup">
                  Parameters
                </th>
              )}
              {metricColumns.length > 0 && (
                <th colSpan={metricColumns.length} className="exp-group" scope="colgroup">
                  Latest metrics
                </th>
              )}
            </tr>
            <tr>
              <th className="exp-check">
                <input
                  type="checkbox"
                  ref={selectAllRef}
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  aria-label={allSelected ? "Clear selection" : "Select all runs"}
                />
              </th>
              <th>Run</th>
              <th>Status</th>
              <th className="r">Duration</th>
              <th>Started</th>
              {paramColumns.map((column, index) => (
                <th
                  key={`param-${column.key}`}
                  className={`${column.numeric ? "r" : ""} ${index === 0 ? "exp-group-start" : ""}`}
                >
                  {columnLabel(column.key)}
                </th>
              ))}
              {metricColumns.map((column, index) => (
                <th
                  key={`metric-${column.key}`}
                  className={`${column.numeric ? "r" : ""} ${index === 0 ? "exp-group-start" : ""}`}
                >
                  {columnLabel(column.key)}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {runs.map((run) => {
              const badge = runBadge(run.status);
              const selected = selectedRuns.has(run._id);
              return (
                <tr key={run._id} className={selected ? "is-selected" : undefined}>
                  <td className="exp-check">
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => toggleRunSelection(run._id)}
                      aria-label={`Select ${runNames[run._id] ?? run._id}`}
                    />
                  </td>
                  <td className="exp-run-cell">
                    <Link to={`/runs/${encodeURIComponent(run._id)}`}>{runNames[run._id] ?? run._id}</Link>
                    {runNames[run._id] && runNames[run._id] !== run._id && <span className="exp-run-id mono">{run._id}</span>}
                  </td>
                  <td>
                    <span className={`badge ${badge.tone}`}>{badge.label}</span>
                  </td>
                  <td className="r num">{run.duration}</td>
                  <td className="num muted">{formatWhen(run.startTime) ?? "—"}</td>
                  {paramColumns.map((column, index) => (
                    <td
                      key={`param-${column.key}`}
                      className={`${column.numeric ? "r num" : ""} ${index === 0 ? "exp-group-start" : ""}`}
                    >
                      {formatCell(run.parameters?.[column.key])}
                    </td>
                  ))}
                  {metricColumns.map((column, index) => (
                    <td
                      key={`metric-${column.key}`}
                      className={`${column.numeric ? "r num" : ""} ${index === 0 ? "exp-group-start" : ""}`}
                    >
                      {formatCell(run.metrics?.[column.key])}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  return (
    <section className="panel" aria-labelledby="exp-runs-title">
      <div className="panel-head">
        <h2 id="exp-runs-title">Runs</h2>
        {!loading && !error && (
          <span className="sub num">
            {runs.length} {runs.length === 1 ? "run" : "runs"}
          </span>
        )}
        <span className="spacer" />
        {selectedRuns.size > 0 && (
          <>
            <span className="sub num" aria-live="polite">
              {selectedRuns.size} selected
            </span>
            <button type="button" className="btn btn-ghost" onClick={() => setSelectedRuns(new Set())}>
              Clear
            </button>
          </>
        )}
        <button
          type="button"
          className="btn btn-primary"
          disabled={!canCompare}
          title={canCompare ? undefined : `Select ${MIN_COMPARE} to ${MAX_COMPARE} runs to compare`}
          onClick={() => setComparing(true)}
        >
          <FiColumns />
          Compare selected
        </button>
      </div>

      {renderBody()}

      {comparing && (
        <RunComparisonModal
          experimentId={experimentId}
          selectedRunIds={Array.from(selectedRuns)}
          onClose={() => setComparing(false)}
        />
      )}
    </section>
  );
};

export default Runs;
