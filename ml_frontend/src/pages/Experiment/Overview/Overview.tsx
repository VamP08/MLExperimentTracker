import { useEffect, useState } from "react";
import StatsOverview from "../../../components/Dashboard/StatsOverview/StatsOverview";
import ActivityTimeline from "../../../components/Dashboard/ActivityTimeline/ActivityTimeline";
import Description from "../../../components/Experiment/Overview/Description/Description";
import "./Overview.css";
import { apiFetch } from '../../../lib/api';

interface StatsData {
  title: string;
  value: string;
}

interface Activity {
  date: string;
  event: string;
}

/** The slice of `GET /api/experiment/:id` this page reads. */
interface ExperimentOverview {
  description?: string | null;
  activityTimeline?: { date?: string; event?: string }[];
  stats?: {
    totalRuns?: number;
    successRate?: string;
    avgDuration?: string;
    lastRun?: string;
  };
}

interface OverviewProps {
  experimentId: string;
}

const Overview = ({ experimentId }: OverviewProps) => {
  const [statsData, setStatsData] = useState<StatsData[]>([]);
  const [activitiesData, setActivitiesData] = useState<Activity[]>([]);
  const [description, setDescription] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchOverview = async () => {
      if (!experimentId) {
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setError(null);

        const res = await apiFetch(`/api/experiment/${experimentId}`);
        if (!res.ok) {
          throw new Error(`Request failed with ${res.status}`);
        }

        const data: ExperimentOverview = await res.json();
        const stats = data.stats ?? {};

        setStatsData([
          { title: "Total Runs", value: String(stats.totalRuns ?? 0) },
          { title: "Success Rate", value: stats.successRate ?? "—" },
          { title: "Average Duration", value: stats.avgDuration ?? "—" },
          { title: "Last Run", value: stats.lastRun ?? "—" },
        ]);

        // `activityTimeline` is a hardcoded empty array on the server side
        // (GAPS M14), so this maps nothing today. It is read defensively rather
        // than skipped, because the field is part of the response contract.
        const timeline = Array.isArray(data.activityTimeline)
          ? data.activityTimeline
          : [];
        setActivitiesData(
          timeline.map((activity) => {
            const date = activity.date ? new Date(activity.date) : null;
            return {
              date:
                date && !Number.isNaN(date.getTime())
                  ? date.toLocaleDateString()
                  : "—",
              event: activity.event ?? "",
            };
          }),
        );

        setDescription(data.description || "");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load overview");
        setStatsData([]);
        setActivitiesData([]);
        setDescription("");
      } finally {
        setLoading(false);
      }
    };

    fetchOverview();
  }, [experimentId]);

  if (loading) {
    return <div className="overview-status">Loading overview...</div>;
  }

  if (error) {
    return (
      <div className="overview-status overview-status-error">
        Could not load this experiment&rsquo;s overview: {error}
      </div>
    );
  }

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
