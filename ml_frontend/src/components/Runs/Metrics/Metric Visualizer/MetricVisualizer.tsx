import React, { useState } from 'react';
import './MetricVisualizer.css';
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar, ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar,
  PieChart, Pie, Cell, Brush
} from 'recharts';

interface MetricData {
  name: string;
  value: string;
  description: string;
}

interface MetricVisualizerProps {
  metrics: MetricData[];
  onClose: () => void;
}

const MetricVisualizer: React.FC<MetricVisualizerProps> = ({ metrics, onClose }) => {
  // State for chart customization
  const [chartTypes, setChartTypes] = useState<Record<string, string>>({});
  
  // Change chart type for a specific metric
  const changeChartType = (metricName: string, chartType: string) => {
    setChartTypes(prev => ({
      ...prev,
      [metricName]: chartType
    }));
  };
  
  // Get current chart type for a metric
  const getChartType = (metric: MetricData) => {
    if (chartTypes[metric.name]) {
      return chartTypes[metric.name];
    }
    
    // Default chart types based on metric
    if (['accuracy', 'precision', 'recall', 'f1_score'].includes(metric.name)) {
      return 'line';
    }
    if (metric.name === 'loss') {
      return 'area';
    }
    if (metric.name === 'auc') {
      return 'radar';
    }
    if (metric.name === 'training_time') {
      return 'bar';
    }
    if (metric.name === 'inference_time') {
      return 'pie';
    }
    return 'scatter';
  };

  return (
    <div className="metric-visualizer">
      <div className="visualizer-header">
        <h2>Metrics Visualization</h2>
        <button className="close-button" onClick={onClose}>Close</button>
      </div>
      
      <div className="visualizer-grid">
        {metrics.map((metric, index) => (
          <div key={index} className="visualizer-card">
            <div className="visualizer-card-header">
              <h3 className="visualizer-title">{metric.name}</h3>
              <div className="chart-type-selector">
                <select 
                  value={getChartType(metric)}
                  onChange={(e) => changeChartType(metric.name, e.target.value)}
                >
                  <option value="line">Line Chart</option>
                  <option value="area">Area Chart</option>
                  <option value="bar">Bar Chart</option>
                  <option value="scatter">Scatter Plot</option>
                  <option value="pie">Pie Chart</option>
                  <option value="radar">Radar Chart</option>
                </select>
              </div>
            </div>
            <div className="visualizer-chart">
              {renderChart(metric, getChartType(metric))}
            </div>
            <div className="visualizer-description">{metric.description}</div>
          </div>
        ))}
      </div>
    </div>
  );
};

// Generate mock historical data for each metric
const generateHistoricalData = (metric: MetricData) => {
  const numericValue = parseFloat(metric.value);
  const isPercentage = ['accuracy', 'precision', 'recall', 'f1_score', 'auc'].includes(metric.name);
  const isTime = ['training_time', 'inference_time'].includes(metric.name);
  
  // Generate 20 data points
  return Array.from({ length: 20 }, (_, i) => {
    const epoch = i + 1;
    
    if (isTime) {
      // For time metrics, we'll just use random values
      return {
        epoch,
        value: Math.floor(Math.random() * 100) + 50
      };
    }
    
    if (metric.name === 'loss') {
      // Loss typically decreases over time
      const startValue = numericValue * 3;
      const decreaseFactor = (startValue - numericValue) / 19;
      return {
        epoch,
        value: startValue - (decreaseFactor * i) + (Math.random() * 0.05)
      };
    }
    
    if (isPercentage) {
      // Accuracy, precision, etc. typically increase over time
      const startValue = Math.max(0.5, numericValue - 0.3);
      const increaseFactor = (numericValue - startValue) / 19;
      return {
        epoch,
        value: startValue + (increaseFactor * i) + (Math.random() * 0.03 - 0.015)
      };
    }
    
    // Default case
    return {
      epoch,
      value: numericValue * (0.85 + (Math.random() * 0.3))
    };
  });
};

// Generate confusion matrix data for classification metrics
const generateConfusionMatrix = () => {
  return [
    { name: 'True Positive', value: 85 },
    { name: 'False Positive', value: 10 },
    { name: 'False Negative', value: 5 },
    { name: 'True Negative', value: 90 }
  ];
};

// Generate scatter plot data for distribution visualization
const generateScatterData = (metric: MetricData) => {
  const numericValue = parseFloat(metric.value);
  const data = [];
  
  for (let i = 0; i < 50; i++) {
    data.push({
      x: Math.random() * 100,
      y: numericValue * 100 + (Math.random() * 20 - 10),
      z: Math.random() * 10
    });
  }
  
  return data;
};

// Generate comparison data for multiple runs
const generateComparisonData = (metric: MetricData) => {
  const numericValue = parseFloat(metric.value);
  const data = [];
  
  for (let i = 1; i <= 10; i++) {
    data.push({
      epoch: i,
      current: numericValue * (0.9 + (i / 100)),
      previous: numericValue * (0.85 + (i / 120)),
      baseline: numericValue * (0.8 + (i / 150))
    });
  }
  
  return data;
};

// Helper function to render different chart types based on metric and selected chart type
const renderChart = (metric: MetricData, chartType: string) => {
  const historicalData = generateHistoricalData(metric);
  const comparisonData = generateComparisonData(metric);
  const scatterData = generateScatterData(metric);
  const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8'];
  
  switch (chartType) {
    case 'line':
      return (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={comparisonData}
            margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
          >
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="epoch" label={{ value: 'Epoch', position: 'insideBottomRight', offset: -5 }} />
            <YAxis domain={['auto', 'auto']} label={{ value: 'Score', angle: -90, position: 'insideLeft' }} />
            <Tooltip formatter={(value) => typeof value === 'number' ? value.toFixed(4) : value} />
            <Legend />
            <Line type="monotone" dataKey="current" name="Current Run" stroke="#8884d8" activeDot={{ r: 8 }} />
            <Line type="monotone" dataKey="previous" name="Previous Run" stroke="#82ca9d" />
            <Line type="monotone" dataKey="baseline" name="Baseline" stroke="#ffc658" />
            <Brush dataKey="epoch" height={30} stroke="#8884d8" />
          </LineChart>
        </ResponsiveContainer>
      );
      
    case 'area':
      return (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={historicalData}
            margin={{ top: 10, right: 30, left: 0, bottom: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="epoch" />
            <YAxis />
            <Tooltip formatter={(value) => typeof value === 'number' ? value.toFixed(4) : value} />
            <Legend />
            <Area type="monotone" dataKey="value" stroke="#8884d8" fill="#8884d8" />
            <Brush dataKey="epoch" height={30} stroke="#8884d8" />
          </AreaChart>
        </ResponsiveContainer>
      );
      
    case 'bar':
      if (metric.name === 'training_time') {
        const timeData = [
          { name: 'Data Loading', time: 15 },
          { name: 'Preprocessing', time: 25 },
          { name: 'Training', time: 85 },
          { name: 'Validation', time: 20 },
          { name: 'Post-processing', time: 10 }
        ];
        
        return (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={timeData}
              margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
            >
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis label={{ value: 'Time (min)', angle: -90, position: 'insideLeft' }} />
              <Tooltip />
              <Legend />
              <Bar dataKey="time" fill="#8884d8" />
            </BarChart>
          </ResponsiveContainer>
        );
      }
      
      return (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={historicalData}
            margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
          >
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="epoch" />
            <YAxis />
            <Tooltip formatter={(value) => typeof value === 'number' ? value.toFixed(4) : value} />
            <Legend />
            <Bar dataKey="value" fill="#8884d8" />
          </BarChart>
        </ResponsiveContainer>
      );
      
    case 'radar':
      const radarData = [
        { subject: 'ROC AUC', A: parseFloat(metric.value), fullMark: 1 },
        { subject: 'PR AUC', A: parseFloat(metric.value) * 0.95, fullMark: 1 },
        { subject: 'Specificity', A: parseFloat(metric.value) * 0.98, fullMark: 1 },
        { subject: 'Sensitivity', A: parseFloat(metric.value) * 0.92, fullMark: 1 },
        { subject: 'Accuracy', A: parseFloat(metric.value) * 0.97, fullMark: 1 },
      ];
      
      return (
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart outerRadius={90} data={radarData}>
            <PolarGrid />
            <PolarAngleAxis dataKey="subject" />
            <PolarRadiusAxis angle={30} domain={[0, 1]} />
            <Radar name="Model" dataKey="A" stroke="#8884d8" fill="#8884d8" fillOpacity={0.6} />
            <Legend />
          </RadarChart>
        </ResponsiveContainer>
      );
      
    case 'pie':
      let pieData;
      
      if (metric.name === 'inference_time') {
        pieData = [
          { name: 'Model Inference', value: 70 },
          { name: 'Data Preprocessing', value: 15 },
          { name: 'Post-processing', value: 15 }
        ];
      } else {
        pieData = generateConfusionMatrix();
      }
      
      return (
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={pieData}
              cx="50%"
              cy="50%"
              labelLine={false}
              label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
              outerRadius={80}
              fill="#8884d8"
              dataKey="value"
            >
              {pieData.map((_entry, index) => (
                <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      );
      
    case 'scatter':
    default:
      return (
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart
            margin={{ top: 20, right: 20, bottom: 20, left: 20 }}
          >
            <CartesianGrid />
            <XAxis type="number" dataKey="x" name="samples" />
            <YAxis type="number" dataKey="y" name="value" />
            <Tooltip cursor={{ strokeDasharray: '3 3' }} />
            <Legend />
            <Scatter name={metric.name} data={scatterData} fill="#8884d8" />
          </ScatterChart>
        </ResponsiveContainer>
      );
  }
};

export default MetricVisualizer;

