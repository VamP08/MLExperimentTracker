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

  useEffect(() => {
    const fetchExperiment = async () => {
      try {
        const res = experimentId
          ? await fetch(`/api/experiment/${experimentId}`)
          : await fetch("/api/experiment");

        if (!res.ok) throw new Error(`Failed to fetch experiment`);

        const data = await res.json();
        setExperiment({
          _id: data._id,
          name: data.name,
          createdAt: data.createdAt,
        });
      } catch (err) {
        console.error("Error fetching experiment:", err);
      }
    };

    fetchExperiment();
  }, [experimentId]);

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  };

  const renderTabContent = () => {
    if (!experiment) return null;

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
              {experiment?.name || "Loading..."}
            </h1>
            <p className="experiment-date">
              Created on{" "}
              {experiment?.createdAt
                ? formatDate(experiment.createdAt)
                : "—"}
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
