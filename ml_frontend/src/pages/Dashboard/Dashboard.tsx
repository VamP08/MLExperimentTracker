import { useCallback, useEffect, useState } from "react";
import StatsOverview from "../../components/Dashboard/StatsOverview/StatsOverview";
import Experiments from "../../components/Dashboard/Experiments/Experiments";
import ActivityTimeline from "../../components/Dashboard/ActivityTimeline/ActivityTimeline";
import ExperimentSearch from "../../components/Dashboard/ExperimentSearch/ExperimentSearch";
import EmptyState from "../../components/Dashboard/EmptyState/EmptyState";
import "./Dashboard.css";
import { apiFetch } from '../../lib/api';

interface Run {
  _id: string;
  name: string;
  /** The RAW on-disk state, not the UI status — see uiStatus below. */
  status: string;
  startedAt: string;
}

interface Experiment {
  _id: string;
  name: string;
  description?: string;
  tags: string[];
  createdAt?: string;
  runs: Run[];
  stats?: { totalRuns?: number };
}

/**
 * The state vocabulary from DATA-CONTRACT §4.2. `/api/dashboard` emits the raw on-disk
 * state in each experiment's `runs[]` and never applies this mapping, so an interrupted
 * run arrives as "interrupted" and the Archived tile counted zero forever (GAPS N12).
 * The mapping has to happen here: that endpoint is pinned field-by-field by the parity
 * suite against the retained Express implementation and must not change.
 */
const STATE_TO_STATUS: Record<string, string> = {
  initialized: "running",
  running: "running",
  completed: "completed",
  failed: "failed",
  interrupted: "archived",
};

/**
 * Anything unrecognised reads as running — including the literal "unknown" the endpoint
 * substitutes when a run has no state at all. There is deliberately no "unknown" status;
 * that is the reader's behaviour and the reason a crashed run looks alive.
 */
const uiStatus = (state: string | undefined): string =>
  (state && STATE_TO_STATUS[state]) || "running";

const toTimestamp = (value: string | undefined): number => {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
};

interface DashboardProps {
  onExperimentSelect: (experimentId: string) => void;
}

const Dashboard = ({ onExperimentSelect }: DashboardProps) => {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  // null until ExperimentSearch reports its first result. Falling back to the unfiltered
  // list keeps the stat tiles correct on the first paint instead of flashing zeros: the
  // search reports through an effect, which runs after the browser has already painted.
  const [filtered, setFiltered] = useState<Experiment[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/api/dashboard");

      if (!res.ok) {
        throw new Error(`Server responded ${res.status}`);
      }

      const json = await res.json();
      if (!json.success) {
        throw new Error("API error: " + json.message);
      }

      // Tags are only as well-formed as whoever wrote metadata.json; normalise once here
      // so nothing downstream has to defend itself.
      const data: Experiment[] = (Array.isArray(json.data) ? json.data : []).map(
        (exp: Experiment) => ({
          ...exp,
          tags: Array.isArray(exp.tags) ? exp.tags : [],
          runs: Array.isArray(exp.runs) ? exp.runs : [],
        }),
      );

      setFiltered(null);
      setExperiments(data);
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : "Unknown error";
      console.error("Dashboard fetch failed:", err);
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const visibleExperiments = filtered ?? experiments;

  const handleExperimentClick = (idx: number) => {
    const experiment = visibleExperiments[idx];
    if (experiment) {
      onExperimentSelect(experiment._id);
    }
  };

  if (loading) {
    return <div className="dashboard-loading">Loading…</div>;
  }

  if (error) {
    return (
      <div className="dashboard-error">
        <p>Failed to load dashboard:</p>
        <pre>{error}</pre>
      </div>
    );
  }

  // A fresh install has no runs at all. That is not an error and not a slow load, so it
  // gets its own screen explaining what to do (GAPS M24).
  if (experiments.length === 0) {
    return (
      <div className="dashboard-container">
        <h1 className="dashboard-title">Dashboard</h1>
        <EmptyState onRetry={fetchData} />
      </div>
    );
  }

  const counts: Record<string, number> = {
    completed: 0,
    failed: 0,
    archived: 0,
    running: 0,
  };
  let totalRuns = 0;

  for (const experiment of visibleExperiments) {
    for (const run of experiment.runs) {
      totalRuns += 1;
      counts[uiStatus(run.status)] += 1;
    }
  }

  // Every run lands in exactly one bucket, so the four sum to the total. "Active" used to
  // be total − completed − archived, which quietly absorbed failed runs as well.
  const stats = [
    { title: "Total Runs", value: totalRuns.toString() },
    { title: "Active", value: counts.running.toString() },
    { title: "Completed", value: counts.completed.toString() },
    { title: "Failed", value: counts.failed.toString() },
    { title: "Archived", value: counts.archived.toString() },
  ];

  const activities = visibleExperiments
    .flatMap((exp) =>
      exp.runs.map((r) => ({
        at: toTimestamp(r.startedAt),
        date: r.startedAt ? new Date(r.startedAt).toLocaleString() : "Unknown date",
        event: `${r.name || r._id} · ${exp.name} · ${uiStatus(r.status)}`,
      })),
    )
    // Sort on the timestamp, not on the formatted date — a locale date string sorts
    // lexicographically and puts 9/1 above 10/1.
    .sort((a, b) => b.at - a.at)
    .slice(0, 10)
    .map(({ date, event }) => ({ date, event }));

  return (
    <div className="dashboard-container">
      <h1 className="dashboard-title">Dashboard</h1>
      <StatsOverview stats={stats} />
      <div className="dashboard-content">
        <div className="dashboard-left">
          <ExperimentSearch experiments={experiments} onFilterChange={setFiltered} />
          {visibleExperiments.length > 0 ? (
            <Experiments
              experiments={visibleExperiments.map((e) => e.name)}
              onExperimentClick={handleExperimentClick}
            />
          ) : (
            <p className="dashboard-no-matches" role="status">
              No experiments match the current search or tag filter.
            </p>
          )}
        </div>
        <div className="dashboard-right">
          <ActivityTimeline activities={activities} />
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
