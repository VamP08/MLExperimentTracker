import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import "./Runs.css";

// Import components
import Overview from "./Overview/Overview";
import RunParams from "./RunParams/RunParams";
import Logs from "./Logs/Logs";
import Metrics from "./Metrics/Metrics";
import Evaluation from "./Evaluation/Evaluation";
import SystemMetrics from "../../components/Runs/SystemMetrics/SystemMetrics";
import Checkpoints from "../../components/Runs/Checkpoints/Checkpoints";
import Artifacts from "../../components/Runs/Artifacts/Artifacts";

type TabType = "overview" | "run-params" | "logs" | "metrics" | "evaluation" | "system-metrics" | "checkpoints" | "artifacts";

const TABS: TabType[] = [
  "overview",
  "run-params",
  "metrics",
  "evaluation",
  "system-metrics",
  "checkpoints",
  "artifacts",
  "logs",
];

interface RunsProps {
  runId: string | null;
}

interface RunDetails {
  _id: string;
  name: string;
  experimentName: string;
  createdAt: string;
}

const Runs = ({ runId }: RunsProps) => {
  const [activeTab, setActiveTab] = useState<TabType>("overview");
  const [run, setRun] = useState<RunDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchRun = async () => {
      try {
        setLoading(true);
        setError(null);

        const res = runId
          ? await fetch(`/api/run/${runId}`)
          : await fetch("/api/run");

        // A 404 on the unparameterised route means the storage tree holds no runs
        // yet — an empty archive, not a failure worth an error message.
        if (res.status === 404) {
          setRun(null);
          return;
        }
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);

        const data = await res.json();
        setRun({
          _id: data._id,
          name: data.name,
          // GAPS M12: the API sends `experimentName`; this read was lowercase.
          experimentName: data.experimentName,
          createdAt: data.createdAt,
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
        setRun(null);
      } finally {
        setLoading(false);
      }
    };

    fetchRun();
  }, [runId]);

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  };

  const renderTabContent = () => {
    if (!run) return null;

    switch (activeTab) {
      case "overview":
        return <Overview runId={run._id} />;
      case "run-params":
        return <RunParams runId={run._id} />;
      case "logs":
        return <Logs runId={run._id} />;
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
      default:
        return null;
    }
  };

  // GAPS M24: a failed fetch used to leave "Loading..." in the header forever.
  // The three terminal states are now distinguishable from one another.
  if (loading) {
    return (
      <div className="run-page">
        <div className="run-content">
          <div className="run-shell-message">Loading run...</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="run-page">
        <div className="run-content">
          <div className="run-shell-message run-shell-error">
            Could not load this run: {error}
          </div>
        </div>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="run-page">
        <div className="run-content">
          <div className="run-shell-message">
            {runId ? (
              <>
                No run named <code>{runId}</code> was found.
              </>
            ) : (
              <>
                No runs found. Track one with the SDK, or run <code>mlexp demo</code> to
                populate the archive.
              </>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="run-page">
      <div className="run-content">
        <div className="run-header">
          <div className="run-info">
            <h1 className="run-name">{run.name}</h1>
            <p className="run-experiment">
              Experiment: {run.experimentName || "—"}
            </p>
            <p className="run-date">
              Created on {run.createdAt ? formatDate(run.createdAt) : "—"}
            </p>
          </div>
        </div>

        <div className="tabs-container">
          <div className="tabs">
            {TABS.map((tab) => (
              <button
                key={tab}
                className={`tab ${activeTab === tab ? "active" : ""}`}
                onClick={() => setActiveTab(tab)}
              >
                {tab
                  .split("-")
                  .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
                  .join(" ")}
                {activeTab === tab && (
                  <motion.div
                    className="tab-indicator"
                    layoutId="tab-indicator"
                    transition={{ type: "spring", duration: 0.5 }}
                  />
                )}
              </button>
            ))}
          </div>

          <div className="tab-content-container">{renderTabContent()}</div>
        </div>
      </div>
    </div>
  );
};

export default Runs;
