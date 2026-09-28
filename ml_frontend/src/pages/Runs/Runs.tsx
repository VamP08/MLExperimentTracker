import { useCallback, useEffect, useState } from "react";
import "./Runs.css";
import { apiFetch } from "../../lib/api";
import { refreshDashboard, useDashboard } from "../../lib/dashboard";
import { useRememberVisited } from "../../lib/navigationMemory";

import TopBar from "../../components/TopBar/TopBar";
import type { Crumb } from "../../components/Breadcrumbs/Breadcrumbs";
import RunHeader from "../../components/Runs/RunHeader/RunHeader";
import type { GitFacts, RunHeaderData } from "../../components/Runs/RunHeader/RunHeader";
import ReproSummary from "../../components/Runs/Repro/ReproSummary";
import type { VerifyReport } from "../../components/Runs/Repro/ReproSummary";
import RunActions from "../../components/Runs/RunActions/RunActions";
import Overview from "./Overview/Overview";
import type { LogRecord } from "./Overview/Overview";
import RunParams from "./RunParams/RunParams";
import Logs from "./Logs/Logs";
import Metrics from "./Metrics/Metrics";
import Evaluation from "./Evaluation/Evaluation";
import SystemMetrics from "../../components/Runs/SystemMetrics/SystemMetrics";
import Checkpoints from "../../components/Runs/Checkpoints/Checkpoints";
import Artifacts from "../../components/Runs/Artifacts/Artifacts";
import Provenance from "../../components/Runs/Provenance/Provenance";

type TabType =
  | "overview"
  | "run-params"
  | "metrics"
  | "evaluation"
  | "system-metrics"
  | "checkpoints"
  | "artifacts"
  | "logs"
  | "provenance";

interface RunData extends RunHeaderData {
  experimentId: string;
  experimentName: string;
  parameters: Record<string, unknown>;
  metrics: Record<string, unknown>;
  metricsHistory: Record<string, unknown>[];
  checkpoints: unknown[];
  artifacts: unknown[];
}

interface RunsProps {
  runId: string | null;
}

/** The Logs tab asks for this many lines; the overview reuses the same page. */
const LOG_PAGE = 500;

const Runs = ({ runId }: RunsProps) => {
  const [activeTab, setActiveTab] = useState<TabType>("overview");
  const [run, setRun] = useState<RunData | null>(null);
  const [git, setGit] = useState<GitFacts | null>(null);
  const [logs, setLogs] = useState<LogRecord[] | null>(null);
  const [report, setReport] = useState<VerifyReport | null>(null);
  const [verifyKey, setVerifyKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { data: archive } = useDashboard();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setGit(null);
    setLogs(null);
    setReport(null);
    setActiveTab("overview");

    (async () => {
      try {
        const res = runId ? await apiFetch(`/api/run/${runId}`) : await apiFetch("/api/run");
        // A 404 on the unparameterised route means the storage tree holds no runs yet —
        // an empty archive, not a failure worth an error message.
        if (res.status === 404) {
          if (!cancelled) setRun(null);
          return;
        }
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        setRun({
          _id: data._id,
          name: data.name,
          description: data.description ?? "",
          status: data.status,
          state: typeof data.state === "string" ? data.state : null,
          tags: Array.isArray(data.tags) ? data.tags : [],
          duration: typeof data.duration === "number" ? data.duration : 0,
          durationFormatted: data.durationFormatted ?? "0s",
          createdAt: data.createdAt ?? null,
          experimentId: data.experimentId,
          experimentName: data.experimentName,
          parameters: data.parameters ?? {},
          metrics: data.metrics ?? {},
          metricsHistory: Array.isArray(data.metricsHistory) ? data.metricsHistory : [],
          checkpoints: Array.isArray(data.checkpoints) ? data.checkpoints : [],
          artifacts: Array.isArray(data.artifacts) ? data.artifacts : [],
        });

        // Facts for the header and the overview's log tail, fetched once the run is known.
        const id = data._id as string;
        apiFetch(`/api/run/${id}/provenance`)
          .then(async (r) => {
            if (!r.ok) return;
            const manifest = await r.json();
            const g = manifest?.git;
            if (!cancelled && g && g.available !== false) {
              setGit({ commit: g.commit ?? null, branch: g.branch ?? null, dirty: Boolean(g.dirty) });
            }
          })
          .catch(() => undefined);
        apiFetch(`/api/run/${id}/logs?limit=${LOG_PAGE}&offset=0`)
          .then(async (r) => {
            const body = r.ok ? await r.json() : [];
            if (!cancelled) setLogs(Array.isArray(body) ? body : []);
          })
          .catch(() => !cancelled && setLogs([]));
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unknown error");
          setRun(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [runId]);

  // Recorded from the loaded run rather than from the click that started the navigation, so a
  // run that 404s leaves the sidebar pointing at the last one that really rendered.
  useRememberVisited("run", run?._id);
  useRememberVisited("experiment", run?.experimentId);

  const openTab = useCallback((tab: TabType) => {
    setActiveTab(tab);
    // Coming from further down the page, bring the section's top back under the pinned bars
    // rather than leaving the reader mid-way through a different section. From the top, stay.
    requestAnimationFrame(() => {
      const panel = document.getElementById("run-panel");
      if (panel && panel.getBoundingClientRect().top < 112) panel.scrollIntoView({ block: "start" });
    });
  }, []);

  const saveDescription = async (text: string) => {
    if (!run) return;
    const res = await apiFetch(`/api/run/${run._id}/description`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: text }),
    });
    if (!res.ok) return;
    setRun({ ...run, description: text, name: text || `Run ${run._id}` });
    void refreshDashboard();
  };

  const saveTags = async (tags: string[]) => {
    if (!run) return;
    const previous = run.tags;
    setRun({ ...run, tags });
    const res = await apiFetch(`/api/run/${run._id}/tags`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags }),
    }).catch(() => null);
    if (!res || !res.ok) setRun((current) => (current ? { ...current, tags: previous } : current));
  };

  const experimentLabel = run?.experimentName || run?.experimentId;
  const crumbs: Crumb[] = [
    { label: "Dashboard", to: "/" },
    ...(experimentLabel
      ? [{ label: experimentLabel, to: run?.experimentId ? `/experiment/${encodeURIComponent(run.experimentId)}` : undefined }]
      : []),
    // The run's short name from metadata.json (what the sidebar lists), not its description,
    // which is the page title already. The run payload does not carry it; the shared
    // dashboard payload does, so this costs no request.
    ...(run
      ? [{ label: archive?.flatMap((e) => e.runs).find((r) => r._id === run._id)?.name || run._id }]
      : [{ label: runId ?? "Runs" }]),
  ];

  if (loading || error || !run) {
    return (
      <>
        <TopBar crumbs={crumbs} />
        <div className="page">
          {loading ? (
            <div className="state">Loading run…</div>
          ) : error ? (
            <div className="state error">Could not load this run: {error}</div>
          ) : runId ? (
            <div className="state">
              <h3>Run not found</h3>
              <p>
                No run named <code>{runId}</code> is in the archive.
              </p>
            </div>
          ) : (
            <div className="state">
              <h3>No runs yet</h3>
              <p>
                Track one with the SDK, or run <code>mlexp demo</code> to populate the archive.
              </p>
            </div>
          )}
        </div>
      </>
    );
  }

  const seriesCount = new Set(
    run.metricsHistory.flatMap((row) =>
      Object.keys(row).filter(
        (k) => !["timestamp", "absolute_timestamp", "step", "run_id", "run_status", "run_state", "start_timestamp"].includes(k),
      ),
    ),
  ).size;
  const logsCapped = logs !== null && logs.length >= LOG_PAGE;

  const tabs: { id: TabType; label: string; count?: string; tone?: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "run-params", label: "Params", count: String(Object.keys(run.parameters).length) },
    { id: "metrics", label: "Metrics", count: String(seriesCount) },
    { id: "evaluation", label: "Evaluation" },
    { id: "system-metrics", label: "System" },
    { id: "checkpoints", label: "Checkpoints", count: String(run.checkpoints.length) },
    { id: "artifacts", label: "Artifacts", count: String(run.artifacts.length) },
    { id: "logs", label: "Logs", count: logs ? (logsCapped ? `${logs.length}+` : String(logs.length)) : undefined },
    {
      id: "provenance",
      label: "Provenance",
      count: report ? `${report.summary.ok}/${report.checks.length}` : undefined,
      tone: report ? (report.verdict === "reproducible" ? "ok" : report.verdict === "drifted" ? "drift" : undefined) : undefined,
    },
  ];

  const renderTab = () => {
    switch (activeTab) {
      case "overview":
        return <Overview runId={run._id} parameters={run.parameters} logs={logs} logsCapped={logsCapped} onOpenTab={openTab} />;
      case "run-params":
        return <RunParams runId={run._id} />;
      case "metrics":
        return <Metrics runId={run._id} />;
      case "evaluation":
        return <Evaluation runId={run._id} />;
      case "system-metrics":
        return <SystemMetrics runId={run._id} />;
      case "checkpoints":
        return <Checkpoints runId={run._id} />;
      case "artifacts":
        return <Artifacts runId={run._id} />;
      case "logs":
        return <Logs runId={run._id} />;
      case "provenance":
        return <Provenance runId={run._id} />;
    }
  };

  return (
    <>
      <TopBar
        crumbs={crumbs}
        actions={
          <RunActions
            runId={run._id}
            canVerify={report !== null}
            hasPatch={Boolean(git?.dirty)}
            onVerify={() => setVerifyKey((k) => k + 1)}
          />
        }
      />
      <div className="page">
        <RunHeader run={run} git={git} onSaveDescription={saveDescription} onSaveTags={saveTags} />

        <ReproSummary runId={run._id} refreshKey={verifyKey} onOpenProvenance={() => openTab("provenance")} onReport={setReport} />

        <nav className="tabs" role="tablist" aria-label="Run sections">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`tab-${tab.id}`}
              aria-selected={activeTab === tab.id}
              aria-controls="run-panel"
              className="tab"
              onClick={() => openTab(tab.id)}
            >
              {tab.label}
              {tab.count !== undefined && <span className={`count ${tab.tone ?? ""}`}>{tab.count}</span>}
            </button>
          ))}
        </nav>

        <div id="run-panel" role="tabpanel" aria-labelledby={`tab-${activeTab}`} className="run-panel">
          {renderTab()}
        </div>
      </div>
    </>
  );
};

export default Runs;
