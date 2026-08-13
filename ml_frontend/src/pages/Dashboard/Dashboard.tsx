"use client";

import { useState, useEffect } from "react";
import StatsOverview from "../../components/Dashboard/StatsOverview/StatsOverview";
import Experiments from "../../components/Dashboard/Experiments/Experiments";
import ActivityTimeline from "../../components/Dashboard/ActivityTimeline/ActivityTimeline";
import RecentSearches from "../../components/Dashboard/RecentSearches/RecentSearches";
import Tags from "../../components/Dashboard/Tags/Tags";
import "./Dashboard.css";

interface Run {
  _id: string;
  name: string;
  status: string;
  startedAt: string;
}

interface Experiment {
  _id: string;
  name: string;
  tags: string[];
  runs: Run[];
}

interface DashboardProps {
  onExperimentSelect: (experimentId: string) => void;
}

const Dashboard = ({ onExperimentSelect }: DashboardProps) => {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await fetch("/api/dashboard");
        
        if (!res.ok) {
          throw new Error(`Server responded ${res.status}`);
        }

        const json = await res.json();
        if (!json.success) {
          throw new Error("API error: " + json.message);
        }

        setExperiments(json.data);
      } catch (err: unknown) {
        const errorMessage = err instanceof Error ? err.message : 'Unknown error';
        console.error("Dashboard fetch failed:", err);
        setError(errorMessage);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const handleTagSelect = (tag: string) => {
    setSelectedTags(prev =>
      prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]
    );
  };

  const handleExperimentClick = (idx: number) => {
    const experiment = filteredExperiments[idx];
    if (experiment) {
      onExperimentSelect(experiment._id);
    }
  };

  const filteredExperiments = selectedTags.length === 0
  ? experiments
  : experiments.filter(exp => {
      const tagsArray = Array.isArray(exp.tags) ? exp.tags : [exp.tags];
      return tagsArray.some(tag => selectedTags.includes(tag));
    });

  if (loading) {
    return <div className="dashboard-loading">Loading…</div>;
  }

  if (error) {
    return (
      <div className="dashboard-error">
        <p>Failed to load dashboard:</p>
        <pre>{error}</pre>
      </div>
    );
  }

  const totalRuns = filteredExperiments.reduce((sum, e) => sum + e.runs.length, 0);
  const completedRuns = filteredExperiments.reduce(
    (sum, e) => sum + e.runs.filter(r => r.status === "completed").length,
    0
  );
  const archivedRuns = filteredExperiments.reduce(
    (sum, e) => sum + e.runs.filter(r => r.status === "archived").length,
    0
  );
  const activeRuns = totalRuns - completedRuns - archivedRuns;

  const stats = [
    { title: "Total Runs", value: totalRuns.toString() },
    { title: "Completed", value: completedRuns.toString() },
    { title: "Archived", value: archivedRuns.toString() },
    { title: "Active", value: activeRuns.toString() },
  ];

  const activities = filteredExperiments
    .flatMap(exp =>
      exp.runs.map(r => ({
        date: r.startedAt
          ? new Date(r.startedAt).toLocaleDateString()
          : "N/A",
        event: `Run ${r._id.slice(-6)} (${exp.name}) — ${r.status}`,
      }))
    )
    .sort((a, b) => (a.date < b.date ? 1 : -1))
    .slice(0, 10);

  const recentSearches = experiments
    .sort((a, b) => (a._id < b._id ? 1 : -1))
    .slice(0, 5)
    .map(e => e.name);

  const allTags = Array.from(new Set(experiments.flatMap(e => e.tags)));

  return (
    <div className="dashboard-container">
      <h1 className="dashboard-title">Dashboard</h1>
      <StatsOverview stats={stats} />
      <div className="dashboard-content">
        <div className="dashboard-left">
          <Experiments
            experiments={filteredExperiments.map(e => e.name)}
            onExperimentClick={handleExperimentClick}
          />
          <Tags
            tags={allTags}
            selectedTags={selectedTags}
            onTagSelect={handleTagSelect}
          />
        </div>
        <div className="dashboard-right">
          <ActivityTimeline activities={activities} />
          <RecentSearches searches={recentSearches} />
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
