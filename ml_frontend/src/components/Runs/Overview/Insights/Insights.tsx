import React from 'react';
import './Insight.css';

interface InsightsProps {
  insights: {
    parameters: number;
    metrics: number;
    topMetric: {
      name: string;
      value: string;
    };
  };
}

const Insights: React.FC<InsightsProps> = ({ insights }) => {
  return (
    <div className="run-insights">
      <h3 className="insights-title">Run Insights</h3>
      
      <div className="insights-grid">
        <div className="insight-card">
          <div className="insight-value">{insights.parameters}</div>
          <div className="insight-label">Parameters</div>
        </div>
        
        <div className="insight-card">
          <div className="insight-value">{insights.metrics}</div>
          <div className="insight-label">Metrics</div>
        </div>
        
        <div className="insight-card">
          <div className="insight-value">{insights.topMetric.value}</div>
          <div className="insight-label">{insights.topMetric.name}</div>
        </div>
      </div>
    </div>
  );
};

export default Insights;
