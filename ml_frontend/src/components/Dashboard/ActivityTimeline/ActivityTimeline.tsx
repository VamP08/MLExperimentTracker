// src/components/dashboard/ActivityTimeline.tsx
import React, { useState } from 'react';
import './ActivityTimeline.css';

interface Activity {
  date: string;
  event: string;
}

interface ActivityTimelineProps {
  activities: Activity[];
}

const ActivityTimeline: React.FC<ActivityTimelineProps> = ({ activities }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const displayActivities = isExpanded ? activities : activities.slice(0, 3);
  
  const toggleExpand = () => {
    setIsExpanded(!isExpanded);
  };

  return (
    <div className="activity-timeline">
      <div className="timeline-header">
        <h2 className="timeline-title">Activity Timeline</h2>
        {activities.length > 3 && (
          <button 
            className="timeline-expand-button" 
            onClick={toggleExpand}
          >
            {isExpanded ? 'Show Less' : 'Show More'}
          </button>
        )}
      </div>
      <div className="timeline-items">
        {displayActivities.map((activity, i) => (
          <div key={i} className="timeline-item">
            <div className="timeline-line" />
            <div className="timeline-icon">
              <span>✓</span>
            </div>
            <div className="timeline-content">
              <p className="timeline-date">{activity.date}</p>
              <p className="timeline-event">{activity.event}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ActivityTimeline;
