import { useState } from "react";
import { Link } from "react-router-dom";
import "./Overview.css";
import { formatWhen, runBadge } from "../../../components/Experiment/experiment";
import type { ExperimentData } from "../../../components/Experiment/experiment";
import { headlineMetric } from "../../../lib/metrics";
import { CurvesPanel, Leaderboard, ParamScatter } from "./ExperimentCharts";
import { CURVE_LIMIT, useChartRuns } from "./chartRuns";
import type { Ranked } from "./ExperimentCharts";

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

  // One metric drives every chart below, so the curves, the ranking and the scatter agree.
  const charted = useChartRuns(experiment._id, runs);
  const [chosen, setChosen] = useState<string | null>(null);
  const metrics = [...new Set((charted ?? []).flatMap((r) => r.series.filter((s) => s.pts.length > 0).map((s) => s.name)))];
  const metric = chosen && metrics.includes(chosen) ? chosen : headlineMetric(metrics)?.key;
  const better = metric && /loss|err/i.test(metric) ? "min" : "max";
  const ranked: Ranked[] = (charted ?? [])
    .map((run) => {
      const pts = run.series.find((s) => s.name === metric)?.pts ?? [];
      return { run, pts, final: pts.length ? pts[pts.length - 1].y : NaN };
    })
    .filter((r) => Number.isFinite(r.final))
    .sort((a, b) => (better === "max" ? b.final - a.final : a.final - b.final));

  const timeline = experiment.activityTimeline.map((activity) => ({
    date: formatWhen(activity.date) ?? "—",
    event: activity.event ?? "",
  }));

  return (
    <div className="stack">
      {charted === null && runs.length > 0 ? (
        <section className="panel">
          <div className="state" role="status">
            Loading run curves…
          </div>
        </section>
      ) : (
        metric && (
          <>
            {runs.length > CURVE_LIMIT && <p className="exp-note">Charts show the newest {CURVE_LIMIT} of {runs.length} runs.</p>}
            <div className="exp-cols">
              <CurvesPanel metric={metric} metrics={metrics} onMetric={setChosen} ranked={ranked} better={better} />
              <Leaderboard metric={metric} ranked={ranked} better={better} />
            </div>
          </>
        )
      )}

      <div className="exp-cols">
        {metric && ranked.length > 1 && <ParamScatter metric={metric} ranked={ranked} better={better} />}
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
      </div>

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

      {/* Always empty from the server for now, so skip the card. */}
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
