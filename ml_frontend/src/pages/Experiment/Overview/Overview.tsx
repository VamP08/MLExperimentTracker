import { Link } from "react-router-dom";
import "./Overview.css";
import { formatWhen, runBadge } from "../../../components/Experiment/experiment";
import type { ExperimentData } from "../../../components/Experiment/experiment";

interface OverviewProps {
  experiment: ExperimentData;
  onOpenRuns: () => void;
}

/** The overview lists the newest runs; the Runs tab has all of them. */
const RECENT_LIMIT = 10;

const startedMs = (iso: string | null) => {
  const ms = iso ? new Date(iso).getTime() : NaN;
  return Number.isFinite(ms) ? ms : 0;
};

const Overview = ({ experiment, onOpenRuns }: OverviewProps) => {
  const { stats, runs } = experiment;
  const total = stats.totalRuns ?? 0;
  const completed = stats.completedRuns ?? 0;
  const failed = stats.failedRuns ?? 0;
  const running = stats.runningRuns ?? 0;
  // Initialized, interrupted and unreadable runs: counted in the total, in none of the three.
  const other = Math.max(0, total - completed - failed - running);

  const states = [
    { key: "ok", label: "Completed", count: completed },
    { key: "fail", label: "Failed", count: failed },
    { key: "running", label: "Running", count: running },
    { key: "other", label: "Other", count: other },
  ];

  const recent = [...runs]
    .sort((a, b) => startedMs(b.startedAt) - startedMs(a.startedAt))
    .slice(0, RECENT_LIMIT);

  const timeline = experiment.activityTimeline.map((activity) => ({
    date: formatWhen(activity.date) ?? "—",
    event: activity.event ?? "",
  }));

  return (
    <div className="stack">
      <div className="exp-overview">
        <section className="panel" aria-labelledby="exp-states-title">
          <div className="panel-head">
            <h2 id="exp-states-title">Runs by state</h2>
            <span className="sub num">{total} total</span>
          </div>
          <div className="panel-body">
            {total > 0 && (
              <div
                className="exp-bar"
                role="img"
                aria-label={states.map((s) => `${s.count} ${s.label.toLowerCase()}`).join(", ")}
              >
                {states
                  .filter((s) => s.count > 0)
                  .map((s) => (
                    <span key={s.key} className={`exp-bar-seg ${s.key}`} style={{ flexGrow: s.count }} />
                  ))}
              </div>
            )}
            <ul className="exp-state-list">
              {states.map((s) => (
                <li key={s.key}>
                  <span className={`exp-dot ${s.key}`} aria-hidden="true" />
                  <span>{s.label}</span>
                  <span className="num">{s.count}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="panel" aria-labelledby="exp-recent-title">
          <div className="panel-head">
            <h2 id="exp-recent-title">Recent runs</h2>
            <span className="spacer" />
            {runs.length > 0 && (
              <button type="button" className="exp-link-btn" onClick={onOpenRuns}>
                All runs
              </button>
            )}
          </div>
          {recent.length === 0 ? (
            <div className="state">
              <h3>No runs yet</h3>
              <p>A run appears here once its directory contains a <code>metadata.json</code>.</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Run</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((run) => {
                    const badge = runBadge(run.status);
                    return (
                      <tr key={run._id}>
                        <td>
                          <span className={`badge ${badge.tone}`}>{badge.label}</span>
                        </td>
                        <td className="exp-run-name">
                          <Link to={`/runs/${encodeURIComponent(run._id)}`}>{run.name}</Link>
                        </td>
                        <td className="num muted">{formatWhen(run.startedAt) ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      {/* Empty on every server today (GAPS M14): no card at all rather than an empty one. */}
      {timeline.length > 0 && (
        <section className="panel" aria-labelledby="exp-activity-title">
          <div className="panel-head">
            <h2 id="exp-activity-title">Activity</h2>
          </div>
          <ul className="exp-activity">
            {timeline.map((item, index) => (
              <li key={index}>
                <span className="num muted">{item.date}</span>
                <span>{item.event}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
};

export default Overview;
