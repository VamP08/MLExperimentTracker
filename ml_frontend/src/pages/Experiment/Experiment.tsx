"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import "./Experiment.css";

// Components
import Overview from "./Overview/Overview";
import Runs from "./Runs/Runs";
import Settings from "./Settings/Settings";

type TabType = "overview" | "runs" | "settings";

interface ExperimentProps {
  experimentId: string | null;
  onRunSelect: (runId: string) => void;
}

interface ExperimentDetails {
  _id: string;
  name: string;
  createdAt: string;
}

const Experiment = ({ experimentId, onRunSelect }: ExperimentProps) => {
  const [activeTab, setActiveTab] = useState<TabType>("overview");
  const [experiment, setExperiment] = useState<ExperimentDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchExperiment = async () => {
      try {
        setLoading(true);
        setError(null);

        const res = experimentId
          ? await fetch(`/api/experiment/${experimentId}`)
          : await fetch("/api/experiment");

        // 404 on the parameterless route means the storage root holds no
        // experiments at all, which is a first-run state rather than a fault.
        if (res.status === 404) {
          setExperiment(null);
          return;
        }
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);

        const data = await res.json();
        setExperiment({
          _id: data._id,
          name: data.name,
          createdAt: data.createdAt,
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

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    if (Number.isNaN(date.getTime())) return "an unrecorded date";
    return date.toLocaleDateString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  };

  const renderTabContent = () => {
    if (loading) {
      return <div className="experiment-status">Loading experiment...</div>;
    }

    if (error) {
      return (
        <div className="experiment-status experiment-status-error">
          Could not load this experiment: {error}
        </div>
      );
    }

    if (!experiment) {
      return (
        <div className="experiment-status">
          No experiment found. Runs appear here once something has been logged to the
          storage root — <code>mlexp path</code> prints the directory being read.
        </div>
      );
    }

    switch (activeTab) {
      case "overview":
        return <Overview experimentId={experiment._id} />;
      case "runs":
        return <Runs experimentId={experiment._id} onRunSelect={onRunSelect} />;
      case "settings":
        return <Settings />;
      default:
        return null;
    }
  };

  return (
    <div className="experiment-page">
      <div className="experiment-content">
        <div className="experiment-header">
          <div className="experiment-info">
            <h1 className="experiment-name">
              {experiment?.name ??
                (loading ? "Loading..." : error ? "Experiment unavailable" : "No experiment")}
            </h1>
            <p className="experiment-date">
              {experiment?.createdAt
                ? `Created on ${formatDate(experiment.createdAt)}`
                : null}
            </p>
          </div>
        </div>

        <div className="tabs-container">
          <div className="tabs">
            {(["overview", "runs", "settings"] as TabType[]).map((tab) => (
              <button
                key={tab}
                className={`tab ${activeTab === tab ? "active" : ""}`}
                onClick={() => setActiveTab(tab)}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
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

export default Experiment;
