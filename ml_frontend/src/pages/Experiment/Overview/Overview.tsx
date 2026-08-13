"use client";

import { useEffect, useState } from "react";
import StatsOverview from "../../../components/Dashboard/StatsOverview/StatsOverview";
import ActivityTimeline from "../../../components/Dashboard/ActivityTimeline/ActivityTimeline";
import Description from "../../../components/Experiment/Overview/Description/Description";
import "./Overview.css";

interface StatsData {
  title: string;
  value: string;
}

interface Activity {
  date: string;
  event: string;
}

interface OverviewProps {
  experimentId: string;
}

const Overview = ({ experimentId }: OverviewProps) => {
  const [statsData, setStatsData] = useState<StatsData[]>([]);
  const [activitiesData, setActivitiesData] = useState<Activity[]>([]);
  const [description, setDescription] = useState<string>("");

  useEffect(() => {
    const fetchOverview = async () => {
      if (!experimentId) return;

      try {
        const res = await fetch(`/api/experiment/${experimentId}`);
        const data = await res.json();

        setStatsData([
          { title: "Total Runs", value: data.stats.totalRuns.toString() },
          { title: "Success Rate", value: data.stats.successRate },
          { title: "Average Duration", value: data.stats.avgDuration },
          { title: "Last Run", value: data.stats.lastRun },
        ]);

        setActivitiesData(
          data.activityTimeline.map((act: any) => ({
            date: new Date(act.date).toLocaleDateString(),
            event: act.event,
          }))
        );

        setDescription(data.description || "");
      } catch (err) {
        console.error("Failed to fetch experiment overview:", err);
      }
    };

    fetchOverview();
  }, [experimentId]);

  return (
    <div className="overview-container">
      <div className="left-column">
        <div className="card stats-card">
          <StatsOverview stats={statsData} />
        </div>
        <div className="card timeline-card">
          <ActivityTimeline activities={activitiesData} />
        </div>
      </div>
      <div className="right-column">
        <div className="card description-card">
          <Description
            description={description}
            experimentId={experimentId}
            onEdit={(newDesc) => setDescription(newDesc)}
          />
        </div>
      </div>
    </div>
  );
};

export default Overview;
