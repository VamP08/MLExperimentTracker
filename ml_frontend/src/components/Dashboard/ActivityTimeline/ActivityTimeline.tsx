import { useState } from "react";
import "./ActivityTimeline.css";

interface Activity {
  date: string;
  event: string;
}

interface ActivityTimelineProps {
  activities: Activity[];
}

const COLLAPSED = 3;

const ActivityTimeline = ({ activities }: ActivityTimelineProps) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const shown = isExpanded ? activities : activities.slice(0, COLLAPSED);

  return (
    <div className="activity">
      <div className="activity-head">
        <h2>Activity</h2>
        {activities.length > COLLAPSED && (
          <button type="button" className="btn btn-ghost" onClick={() => setIsExpanded((v) => !v)}>
            {isExpanded ? "Show less" : `Show all ${activities.length}`}
          </button>
        )}
      </div>
      {shown.length > 0 ? (
        <ol className="activity-list">
          {shown.map((activity, i) => (
            <li key={i} className="activity-item">
              <span className="activity-date num">{activity.date}</span>
              <span className="activity-event">{activity.event}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="activity-empty muted">No activity recorded.</p>
      )}
    </div>
  );
};

export default ActivityTimeline;
