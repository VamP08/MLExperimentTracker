import runService from "../services/run.service.js";

// GET /api/run/:id/metrics - Get metrics data
export const getMetrics = async (req, res) => {
  try {
    const { id } = req.params;

    const run = runService.findRunById(id);
    if (!run) {
      return res.status(404).json({ message: "Run not found" });
    }

    // Get raw metrics history
    const metricsHistory = run.metricsHistory || [];
    
    // Transform metrics history into time series format
    // Group by metric name and create separate time series for each
    const metricsByName = {};
    
    metricsHistory.forEach(entry => {
      Object.keys(entry).forEach(key => {
        // Skip metadata fields
        if (['timestamp', 'absolute_timestamp', 'step', 'run_id', 'run_status', 'run_state', 'start_timestamp'].includes(key)) {
          return;
        }
        
        // Initialize metric array if not exists
        if (!metricsByName[key]) {
          metricsByName[key] = [];
        }
        
        // Add data point
        metricsByName[key].push({
          step: entry.step,
          value: entry[key],
          timestamp: entry.timestamp
        });
      });
    });
    
    // Convert to array format expected by frontend
    const timeSeries = Object.keys(metricsByName).map(name => ({
      name,
      data: metricsByName[name]
    }));

    res.json(timeSeries);
  } catch (err) {
    console.error("Error fetching metrics:", err);
    res.status(500).json({ message: "Server error" });
  }
};

// GET /api/run/:id/metrics/timeseries - Get metrics time series
export const getMetricsTimeSeries = async (req, res) => {
  try {
    const { id } = req.params;
    const { metric } = req.query;

    const run = runService.findRunById(id);
    if (!run) {
      return res.status(404).json({ message: "Run not found" });
    }

    const timeSeries = runService.getMetricsTimeSeries(
      run.experimentId,
      id,
      metric
    );

    res.json(timeSeries);
  } catch (err) {
    console.error("Error fetching metrics time series:", err);
    res.status(500).json({ message: "Server error" });
  }
};

// GET /api/run/:id/metrics/export - Export metrics to CSV
export const exportMetrics = async (req, res) => {
  try {
    const { id } = req.params;
    const { format = 'csv' } = req.query;

    const run = runService.findRunById(id);
    if (!run) {
      return res.status(404).json({ message: "Run not found" });
    }

    if (format === 'csv') {
      const csv = runService.exportMetricsToCSV(run.experimentId, id);
      res.setHeader('Content-Type', 'text/csv');
      res.setHeader('Content-Disposition', `attachment; filename="metrics_${id}.csv"`);
      res.send(csv);
    } else if (format === 'json') {
      const metrics = runService.getMetricsTimeSeries(run.experimentId, id);
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Content-Disposition', `attachment; filename="metrics_${id}.json"`);
      res.json(metrics);
    } else {
      res.status(400).json({ message: "Unsupported format. Use 'csv' or 'json'" });
    }
  } catch (err) {
    console.error("Error exporting metrics:", err);
    res.status(500).json({ message: "Server error" });
  }
};

// GET /api/run/:id/system-metrics - Get system metrics
export const getSystemMetrics = async (req, res) => {
  try {
    const { id } = req.params;

    const run = runService.findRunById(id);
    if (!run) {
      return res.status(404).json({ message: "Run not found" });
    }

    res.json(run.systemMetrics || {});
  } catch (err) {
    console.error("Error fetching system metrics:", err);
    res.status(500).json({ message: "Server error" });
  }
};

// GET /api/run/:id/checkpoints - Get checkpoints
export const getCheckpoints = async (req, res) => {
  try {
    const { id } = req.params;

    const run = runService.findRunById(id);
    if (!run) {
      return res.status(404).json({ message: "Run not found" });
    }

    res.json(run.checkpoints || []);
  } catch (err) {
    console.error("Error fetching checkpoints:", err);
    res.status(500).json({ message: "Server error" });
  }
};

// GET /api/run/:id/artifacts - Get artifacts
export const getArtifacts = async (req, res) => {
  try {
    const { id } = req.params;

    const run = runService.findRunById(id);
    if (!run) {
      return res.status(404).json({ message: "Run not found" });
    }

    res.json(run.artifacts || []);
  } catch (err) {
    console.error("Error fetching artifacts:", err);
    res.status(500).json({ message: "Server error" });
  }
};

export default {
  getMetrics,
  getMetricsTimeSeries,
  exportMetrics,
  getSystemMetrics,
  getCheckpoints,
  getArtifacts
};
