import React from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Brush
} from 'recharts';
import './MetricComparison.css';

interface MetricComparisonProps {
  metrics: {
    name: string;
    runs: {
      runId: string;
      values: { epoch: number; value: number }[];
    }[];
  }[];
  onClose?: () => void;
}

const MetricComparison: React.FC<MetricComparisonProps> = ({ metrics, onClose }) => {
  // Colors for different runs
  const runColors = ['#8884d8', '#82ca9d', '#ffc658', '#ff8042', '#0088FE'];

  return (
    <div className="metric-comparison">
      <div className="comparison-header">
        <h2>Metrics Comparison Across Runs</h2>
        {onClose && (
          <button className="close-button" onClick={onClose}>Close</button>
        )}
      </div>
      
      <div className="comparison-grid">
        {metrics.map((metric, index) => (
          <div key={index} className="comparison-card">
            <h3>{metric.name}</h3>
            <div className="comparison-chart">
              <ResponsiveContainer width="100%" height={300}>
                <LineChart
                  margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
                >
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="epoch" label={{ value: 'Epoch', position: 'insideBottomRight', offset: -5 }} />
                  <YAxis domain={['auto', 'auto']} label={{ value: 'Value', angle: -90, position: 'insideLeft' }} />
                  <Tooltip formatter={(value) => typeof value === 'number' ? value.toFixed(4) : value} />
                  <Legend />
                  
                  {metric.runs.map((run, runIndex) => (
                    <Line 
                      key={run.runId}
                      data={run.values}
                      type="monotone" 
                      dataKey="value" 
                      name={`Run ${run.runId}`}
                      stroke={runColors[runIndex % runColors.length]}
                      activeDot={{ r: 8 }}
                    />
                  ))}
                  <Brush dataKey="epoch" height={30} stroke="#8884d8" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default MetricComparison;

