import React from 'react';
import './Metric.css';

interface MetricsProps {
  metrics: {
    name: string;
    value: string | number;
  }[];
}

const Metrics: React.FC<MetricsProps> = ({ metrics }) => {
  return (
    <div className="run-metrics">
      <table className="metrics-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric, index) => (
            <tr key={index}>
              <td className="metric-name">{metric.name}</td>
              <td className="metric-value">{metric.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default Metrics;
