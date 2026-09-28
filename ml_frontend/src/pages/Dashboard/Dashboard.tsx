import { useEffect, useMemo, useState } from "react";
import TopBar from "../../components/TopBar/TopBar";
import ArchiveCharts from "../../components/Dashboard/ArchiveCharts/ArchiveCharts";
import LatestRuns from "../../components/Dashboard/LatestRuns/LatestRuns";
import type { FeedRun } from "../../components/Dashboard/LatestRuns/LatestRuns";
import ExperimentList from "../../components/Dashboard/ExperimentList/ExperimentList";
import ExperimentSearch from "../../components/Dashboard/ExperimentSearch/ExperimentSearch";
import EmptyState from "../../components/Dashboard/EmptyState/EmptyState";
import { toTimestamp } from "../../components/Dashboard/format";
import { apiFetch } from "../../lib/api";
import { headlineMetric } from "../../lib/metrics";
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

/** Seconds from the comparison table's formatted duration ("7m 55s", "2s"). */
function seconds(text: unknown): number | null {
  if (typeof text !== "string") return null;
  const h = /(\d+)h/.exec(text), m = /(\d+)m/.exec(text), sec = /(\d+)s/.exec(text);
  if (!h && !m && !sec) return null;
  return (h ? +h[1] * 3600 : 0) + (m ? +m[1] * 60 : 0) + (sec ? +sec[1] : 0);
}

/** How many runs the feed shows: enough to read the archive's recent history at a glance. */
const FEED = 7;

interface ComparisonRow {
  _id: string;
  duration: unknown;
  metrics?: Record<string, unknown>;
}

const crumbs = [{ label: "Dashboard" }];

const Dashboard = () => {
  const { data, error } = useDashboard();
  // The comparison endpoint carries each run's duration and final metrics, which the dashboard
  // payload does not; one request per experiment, fetched once the list is known.
  const [rows, setRows] = useState<Map<string, ComparisonRow[]> | null>(null);
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

  // Colour follows an experiment's place in the full list, so filtering never repaints it.
  const colorIndex = useMemo(() => new Map(experiments.map((e, i) => [e._id, i])), [experiments]);
  const ids = experiments.map((e) => e._id).join("\u0000");

  useEffect(() => {
    if (!ids) return;
    let cancelled = false;
    Promise.all(
      ids.split("\u0000").map((id) =>
        apiFetch(`/api/experiment/${id}/runs`)
          .then((res) => (res.ok ? res.json() : []))
          .then((body): [string, ComparisonRow[]] => [id, Array.isArray(body) ? body : []])
          .catch((): [string, ComparisonRow[]] => [id, []]),
      ),
    ).then((entries) => !cancelled && setRows(new Map(entries)));
    return () => {
      cancelled = true;
    };
  }, [ids]);

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

  const durations = rows
    ? new Map(visible.map((e) => [e._id, (rows.get(e._id) ?? []).map((r) => seconds(r.duration)).filter((v): v is number => v !== null)]))
    : null;
  const headlines = new Map(
    experiments.map((e) => {
      const first = rows?.get(e._id)?.find((r) => r.metrics && Object.keys(r.metrics).length);
      return [e._id, first ? (headlineMetric(Object.keys(first.metrics ?? {}))?.key ?? null) : null];
    }),
  );

  const feed: FeedRun[] = visible
    .flatMap((exp) =>
      exp.runs.map((run) => ({
        id: run._id,
        name: run.name || run._id,
        status: uiStatus(run.status),
        startedAt: run.startedAt,
        experimentId: exp._id,
        experimentName: exp.name || exp._id,
        colorIndex: colorIndex.get(exp._id) ?? 0,
        headline: headlines.get(exp._id) ?? null,
      })),
    )
    // Sort on the timestamp, not the formatted date, which sorts 9/1 above 10/1.
    .sort((a, b) => toTimestamp(b.startedAt) - toTimestamp(a.startedAt))
    .slice(0, FEED);

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
          <ArchiveCharts experiments={visible} colorIndex={colorIndex} durations={durations} uiStatus={uiStatus} />

          <div className="dash-cols">
            <LatestRuns runs={feed} />

            <section className="panel" aria-labelledby="dash-experiments">
              <div className="panel-head">
                <h2 id="dash-experiments">Experiments</h2>
                <span className="count">{visible.length}</span>
              </div>
              <ExperimentSearch experiments={experiments} onFilterChange={setFiltered} />
              {visible.length > 0 ? (
                <ExperimentList experiments={visible} colorIndex={colorIndex} />
              ) : (
                <div className="state" role="status">
                  <p>No experiments match the current search or tag filter.</p>
                </div>
              )}
            </section>
          </div>
        </div>
      </div>
    </>
  );
};

export default Dashboard;
