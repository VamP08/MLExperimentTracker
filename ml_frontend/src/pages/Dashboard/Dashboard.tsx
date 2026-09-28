import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import TopBar from "../../components/TopBar/TopBar";
import Experiments from "../../components/Dashboard/Experiments/Experiments";
import ExperimentSearch from "../../components/Dashboard/ExperimentSearch/ExperimentSearch";
import EmptyState from "../../components/Dashboard/EmptyState/EmptyState";
import { shortDate, toTimestamp } from "../../components/Dashboard/format";
import { refreshDashboard, useDashboard } from "../../lib/dashboard";
import type { DashboardExperiment } from "../../lib/dashboard";
import "./Dashboard.css";

/**
 * The state vocabulary from DATA-CONTRACT §4.2. `/api/dashboard` emits the raw on-disk
 * state in each experiment's `runs[]` and never applies this mapping, so an interrupted
 * run arrives as "interrupted" and the Archived count read zero forever (GAPS N12).
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

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

const crumbs = [{ label: "Dashboard" }];

interface DashboardProps {
  onExperimentSelect: (experimentId: string) => void;
}

const Dashboard = ({ onExperimentSelect }: DashboardProps) => {
  const { data, error } = useDashboard();
  // null until ExperimentSearch reports its first result. Falling back to the unfiltered
  // list keeps the table filled on the first paint: the search reports through an effect,
  // which runs after the browser has already painted.
  const [filtered, setFiltered] = useState<DashboardExperiment[] | null>(null);

  // Tags and runs are only as well-formed as whoever wrote metadata.json; normalise once
  // here so nothing downstream has to defend itself.
  const experiments = useMemo(
    () =>
      (data ?? []).map((exp) => ({
        ...exp,
        tags: Array.isArray(exp.tags) ? exp.tags : [],
        runs: Array.isArray(exp.runs) ? exp.runs : [],
      })),
    [data],
  );

  if (!data) {
    return (
      <>
        <TopBar crumbs={crumbs} />
        <div className="page">
          <div className="page-head">
            <h1>Dashboard</h1>
          </div>
          <section className="panel">
            {error ? (
              <div className="state error" role="alert">
                <h3>Could not load the dashboard</h3>
                <p>{error}</p>
              </div>
            ) : (
              <div className="state" role="status">
                Loading experiments…
              </div>
            )}
          </section>
        </div>
      </>
    );
  }

  // A fresh install has no runs at all. That is not an error and not a slow load, so it
  // gets its own screen explaining what to do (GAPS M24).
  if (experiments.length === 0) {
    return (
      <>
        <TopBar crumbs={crumbs} />
        <div className="page">
          <div className="page-head">
            <h1>Dashboard</h1>
          </div>
          <EmptyState onRetry={() => void refreshDashboard()} />
        </div>
      </>
    );
  }

  // Every run lands in exactly one bucket, so the four sum to the total.
  const counts: Record<string, number> = { completed: 0, failed: 0, running: 0, archived: 0 };
  let totalRuns = 0;
  for (const exp of experiments) {
    for (const run of exp.runs) {
      totalRuns += 1;
      counts[uiStatus(run.status)] += 1;
    }
  }
  const outcome = [
    `${counts.completed} completed`,
    `${counts.failed} failed`,
    ...(counts.running ? [`${counts.running} running`] : []),
    ...(counts.archived ? [`${counts.archived} archived`] : []),
  ].join(", ");

  const visible = filtered ?? experiments;

  const recent = visible
    .flatMap((exp) => exp.runs.map((run) => ({ run, exp, at: toTimestamp(run.startedAt) })))
    // Sort on the timestamp, not on the formatted date — a locale date string sorts
    // lexicographically and puts 9/1 above 10/1.
    .sort((a, b) => b.at - a.at)
    .slice(0, 10);

  return (
    <>
      <TopBar crumbs={crumbs} />
      <div className="page">
        <div className="page-head">
          <h1>Dashboard</h1>
          <p className="lede num">
            {plural(experiments.length, "experiment")} · {plural(totalRuns, "run")} · {outcome}
          </p>
        </div>

        {error && (
          <p className="dash-stale" role="status">
            Showing the last loaded data; the latest refresh failed: {error}
          </p>
        )}

        <div className="stack">
          <section className="panel" aria-labelledby="dash-experiments">
            <div className="panel-head">
              <h2 id="dash-experiments">Experiments</h2>
              <span className="count">{visible.length}</span>
            </div>
            <ExperimentSearch experiments={experiments} onFilterChange={setFiltered} />
            {visible.length > 0 ? (
              <Experiments experiments={visible} onExperimentClick={onExperimentSelect} />
            ) : (
              <div className="state" role="status">
                <p>No experiments match the current search or tag filter.</p>
              </div>
            )}
          </section>

          <section className="panel" aria-labelledby="dash-recent">
            <div className="panel-head">
              <h2 id="dash-recent">Recent runs</h2>
              <span className="sub">Newest first</span>
            </div>
            {recent.length > 0 ? (
              <div className="table-wrap">
                <table className="table dash-recent">
                  <thead>
                    <tr>
                      <th scope="col">Status</th>
                      <th scope="col">Run</th>
                      <th scope="col">Experiment</th>
                      <th scope="col">Started</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map(({ run, exp }) => {
                      const status = uiStatus(run.status);
                      return (
                        <tr key={`${exp._id}/${run._id}`}>
                          <td>
                            <span className={`badge ${status}`}>{status[0].toUpperCase() + status.slice(1)}</span>
                          </td>
                          <td className="dash-run">
                            <Link to={`/runs/${encodeURIComponent(run._id)}`}>{run.name || run._id}</Link>
                          </td>
                          <td>
                            <Link className="dash-exp" to={`/experiment/${encodeURIComponent(exp._id)}`}>
                              {exp.name || exp._id}
                            </Link>
                          </td>
                          <td className="num dash-when">{shortDate(run.startedAt)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="state" role="status">
                <p>No runs in the experiments shown.</p>
              </div>
            )}
          </section>
        </div>
      </div>
    </>
  );
};

export default Dashboard;
