"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import "./Runs.css";

// Import components
import Overview from "./Overview/Overview";
import RunParams from "./RunParams/RunParams";
import Logs from "./Logs/Logs";
import Metrics from "./Metrics/Metrics";
import Settings from "./Settings/Settings";
import SystemMetrics from "../../components/Runs/SystemMetrics/SystemMetrics";
import Checkpoints from "../../components/Runs/Checkpoints/Checkpoints";
import Artifacts from "../../components/Runs/Artifacts/Artifacts";

type TabType = "overview" | "run-params" | "logs" | "metrics" | "system-metrics" | "checkpoints" | "artifacts" | "settings";

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

  useEffect(() => {
    const fetchRun = async () => {
      try {
        const res = runId
          ? await fetch(`/api/run/${runId}`)
          : await fetch("/api/run");
        if (!res.ok) throw new Error("Failed to fetch run");

        const data = await res.json();
        setRun({
          _id: data._id,
          name: data.name,
          experimentName: data.experimentname,
          createdAt: data.createdAt,
        });
      } catch (err) {
        console.error("Error fetching run:", err);
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
        return <Logs />;
      case "metrics":
        return <Metrics runId={run._id} />;
      case "system-metrics":
        return <SystemMetrics runId={run._id} />;
      case "checkpoints":
        return <Checkpoints runId={run._id} />;
      case "artifacts":
        return <Artifacts runId={run._id} />;
      case "settings":
        return <Settings />;
      default:
        return null;
    }
  };

  return (
    <div className="run-page">
      <div className="run-content">
        <div className="run-header">
          <div className="run-info">
            <h1 className="run-name">{run?.name || "Loading..."}</h1>
            <p className="run-experiment">
              Experiment: {run?.experimentName || "—"}
            </p>
            <p className="run-date">
              Created on {run?.createdAt ? formatDate(run.createdAt) : "—"}
            </p>
          </div>
        </div>

        <div className="tabs-container">
          <div className="tabs">
            {(["overview", "run-params", "metrics", "system-metrics", "checkpoints", "artifacts", "logs", "settings"] as TabType[]).map((tab) => (
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
