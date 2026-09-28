import { useCallback, useEffect, useState } from "react";
import { FiCalendar, FiCheckCircle, FiClock, FiLayers } from "react-icons/fi";
import "./Experiment.css";
import { apiFetch } from "../../lib/api";
import { useRememberVisited } from "../../lib/navigationMemory";

import TopBar from "../../components/TopBar/TopBar";
import Description from "../../components/Experiment/Overview/Description/Description";
import { formatWhen } from "../../components/Experiment/experiment";
import type { ExperimentData } from "../../components/Experiment/experiment";
import Overview from "./Overview/Overview";
import Runs from "./Runs/Runs";

type TabType = "overview" | "runs";

interface ExperimentProps {
  experimentId: string | null;
  /** Kept for the router; run names on this page are links to `/runs/:id`. */
  onRunSelect: (runId: string) => void;
}

const Experiment = ({ experimentId }: ExperimentProps) => {
  const [activeTab, setActiveTab] = useState<TabType>("overview");
  const [experiment, setExperiment] = useState<ExperimentData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchExperiment = async () => {
      try {
        setLoading(true);
        setError(null);

        const res = experimentId
          ? await apiFetch(`/api/experiment/${experimentId}`)
          : await apiFetch("/api/experiment");

        // 404 on the parameterless route means no experiments yet, not an error.
        if (res.status === 404) {
          setExperiment(null);
          return;
        }
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);

        const data = await res.json();
        setExperiment({
          _id: data._id,
          name: data.name,
          description: data.description || "",
          tags: Array.isArray(data.tags) ? data.tags.map(String) : [],
          runs: Array.isArray(data.runs) ? data.runs : [],
          activityTimeline: Array.isArray(data.activityTimeline) ? data.activityTimeline : [],
          stats: data.stats ?? {},
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load experiment");
        setExperiment(null);
      } finally {
        setLoading(false);
      }
    };

    fetchExperiment();
  }, [experimentId]);

  // Record the id from the loaded experiment, not the click, so the sidebar never points
  // at an id that doesn't resolve.
  useRememberVisited("experiment", experiment?._id);

  const openTab = useCallback((tab: TabType) => {
    setActiveTab(tab);
    // If scrolled down, bring the section top back under the pinned bars.
    requestAnimationFrame(() => {
      const panel = document.getElementById("experiment-panel");
      if (panel && panel.getBoundingClientRect().top < 112) panel.scrollIntoView({ block: "start" });
    });
  }, []);

  const crumbs = [
    { label: "Dashboard", to: "/" },
    { label: experiment?.name ?? experimentId ?? "Experiment" },
  ];

  if (loading || error || !experiment) {
    return (
      <>
        <TopBar crumbs={crumbs} />
        <div className="page">
          {loading ? (
            <div className="state">Loading experiment…</div>
          ) : error ? (
            <div className="state error">Could not load this experiment: {error}</div>
          ) : experimentId ? (
            <div className="state">
              <h3>Experiment not found</h3>
              <p>
                No experiment named <code>{experimentId}</code> is in the storage root.
              </p>
            </div>
          ) : (
            <div className="state">
              <h3>No experiments yet</h3>
              <p>
                Runs appear here once something has been logged to the storage root —{" "}
                <code>mlexp path</code> prints the directory being read.
              </p>
            </div>
          )}
        </div>
      </>
    );
  }

  const { stats } = experiment;
  const total = stats.totalRuns ?? 0;
  const lastRun = formatWhen(stats.lastRun);

  const tabs: { id: TabType; label: string; count?: number }[] = [
    { id: "overview", label: "Overview" },
    { id: "runs", label: "Runs", count: total },
  ];

  return (
    <>
      <TopBar crumbs={crumbs} />
      <div className="page">
        <header className="page-head exp-head">
          <h1>{experiment.name}</h1>

          <div className="exp-facts">
            <span>
              <FiLayers />
              <b className="num">{total}</b> {total === 1 ? "run" : "runs"}
            </span>
            <span className="exp-states">
              <span>
                <span className="exp-dot ok" aria-hidden="true" />
                <b className="num">{stats.completedRuns ?? 0}</b> completed
              </span>
              <span>
                <span className="exp-dot fail" aria-hidden="true" />
                <b className="num">{stats.failedRuns ?? 0}</b> failed
              </span>
              <span>
                <span className="exp-dot running" aria-hidden="true" />
                <b className="num">{stats.runningRuns ?? 0}</b> running
              </span>
            </span>
            <span>
              <FiCheckCircle />
              Success rate <b className="num">{stats.successRate ?? "—"}</b>
            </span>
            <span>
              <FiClock />
              Avg duration <b className="num">{stats.avgDuration ?? "—"}</b>
            </span>
            {lastRun && (
              <span title={stats.lastRun}>
                <FiCalendar />
                Last run <b className="num">{lastRun}</b>
              </span>
            )}
            {experiment.tags.length > 0 && (
              <span className="exp-tags">
                {experiment.tags.map((tag) => (
                  <span className="tag" key={tag}>
                    {tag}
                  </span>
                ))}
              </span>
            )}
          </div>

          <Description
            key={experiment._id}
            description={experiment.description}
            experimentId={experiment._id}
            onEdit={(description) => setExperiment({ ...experiment, description })}
          />
        </header>

        <nav className="tabs" role="tablist" aria-label="Experiment sections">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`tab-${tab.id}`}
              aria-selected={activeTab === tab.id}
              aria-controls="experiment-panel"
              className="tab"
              onClick={() => openTab(tab.id)}
            >
              {tab.label}
              {tab.count !== undefined && <span className="count">{tab.count}</span>}
            </button>
          ))}
        </nav>

        <div id="experiment-panel" role="tabpanel" aria-labelledby={`tab-${activeTab}`} className="exp-panel">
          {activeTab === "overview" ? (
            <Overview experiment={experiment} onOpenRuns={() => openTab("runs")} />
          ) : (
            <Runs
              experimentId={experiment._id}
              runNames={Object.fromEntries((experiment.runs ?? []).map((r) => [r._id, r.name]))}
            />
          )}
        </div>
      </div>
    </>
  );
};

export default Experiment;
