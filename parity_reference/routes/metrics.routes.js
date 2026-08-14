import express from "express";
import {
  getMetrics,
  getMetricsTimeSeries,
  exportMetrics,
  getSystemMetrics,
  getCheckpoints,
  getArtifacts
} from "../controllers/metrics.controller.js";

const router = express.Router();

// Metrics time series (must come before /:id/metrics to match first)
router.get("/:id/metrics/timeseries", getMetricsTimeSeries);

// Export metrics
router.get("/:id/metrics/export", exportMetrics);

// Metrics data
router.get("/:id/metrics", getMetrics);

// System metrics
router.get("/:id/system-metrics", getSystemMetrics);

// Checkpoints
router.get("/:id/checkpoints", getCheckpoints);

// Artifacts
router.get("/:id/artifacts", getArtifacts);

export default router;
