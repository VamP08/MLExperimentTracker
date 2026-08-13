// src/components/dashboard/StatsOverview.tsx
import React from 'react';
import './StatsOverview.css';

interface StatItem {
  title: string;
  value: string;
}

interface StatsOverviewProps {
  stats: StatItem[];
}

const StatsOverview: React.FC<StatsOverviewProps> = ({ stats }) => {
  return (
    <div className="stats-overview">
      {stats.map((stat, index) => (
        <div key={index} className="stat-card">
          <h3 className="stat-title">{stat.title}</h3>
          <p className="stat-value">{stat.value}</p>
        </div>
      ))}
    </div>
  );
};

export default StatsOverview;
