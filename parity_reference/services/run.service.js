import path from 'path';
import fileSystemService from './fileSystem.service.js';

/**
 * Service to manage runs from local file system
 */
class RunService {
  /**
   * Get all runs across all projects
   */
  getAllRuns() {
    const allRuns = fileSystemService.getAllRuns();
    
    return allRuns.map(({ project, runId }) => {
      return this.getRunById(project, runId);
    }).filter(run => run !== null);
  }

  /**
   * Get latest run across all projects
   */
  getLatestRun() {
    const runs = this.getAllRuns();
    if (runs.length === 0) return null;

    return runs.reduce((latest, current) => {
      const latestDate = new Date(latest.createdAt);
      const currentDate = new Date(current.createdAt);
      return currentDate > latestDate ? current : latest;
    });
  }

  /**
   * Get a specific run by project and run ID
   */
  getRunById(project, runId) {
    const runPath = fileSystemService.getRunPath(project, runId);
    
    if (!fileSystemService.runExists(project, runId)) {
      return null;
    }

    // Read all relevant files
    const metadata = fileSystemService.readJSON(path.join(runPath, 'metadata.json'));
    const summary = fileSystemService.readJSON(path.join(runPath, 'summary.json'));
    const config = fileSystemService.readJSON(path.join(runPath, 'config.json'));
    const metrics = fileSystemService.readJSONL(path.join(runPath, 'metrics.jsonl'));
    const artifacts = fileSystemService.readJSONL(path.join(runPath, 'artifacts.jsonl'));
    const systemMetrics = fileSystemService.readJSON(path.join(runPath, 'system_metrics.json'));

    if (!metadata) return null;

    // Extract parameters from config
    const parameters = this.extractParameters(config);

    // Extract metrics from summary
    const metricsSummary = this.extractMetrics(summary);

    // Get artifacts with file listing
    const artifactsList = this.processArtifacts(runPath, artifacts);

    // Get checkpoints
    const checkpoints = this.getCheckpoints(runPath);

    // Calculate duration
    const duration = summary?.duration || 0;
    const startTime = metadata.created_at;
    const endTime = summary?.end_time;

    return {
      _id: runId,
      name: summary?.notes || metadata.notes || `Run ${runId}`,
      experimentId: project,
      experimentName: project,
      status: this.mapState(summary?.state || metadata.state),
      description: summary?.notes || metadata.notes || '',
      tags: metadata.tags || [],
      duration: duration,
      durationFormatted: this.formatDuration(duration),
      createdAt: startTime,
      startTime: startTime,
      endTime: endTime,
      state: summary?.state || metadata.state,
      platform: metadata.platform,
      pythonVersion: metadata.python_version,
      workingDirectory: metadata.working_directory,
      artifacts: artifactsList,
      artifactsCount: artifactsList.length,
      parameters,
      metrics: metricsSummary,
      metricsHistory: metrics,
      systemMetrics: systemMetrics,
      checkpoints: checkpoints,
      config: config,
      summary: summary
    };
  }

  /**
   * Find run by ID across all projects
   */
  findRunById(runId) {
    const projects = fileSystemService.getProjects();
    
    for (const project of projects) {
      if (fileSystemService.runExists(project, runId)) {
        return this.getRunById(project, runId);
      }
    }
    
    return null;
  }

  /**
   * Extract parameters from config
   */
  extractParameters(config) {
    if (!config) return {};

    const parameters = {};
    
    Object.entries(config).forEach(([key, value]) => {
      // Skip nested objects and storage/system config
      if (typeof value === 'object' && value !== null) {
        if (!['storage', 'system', 'logging'].includes(key)) {
          // Flatten one level
          Object.entries(value).forEach(([subKey, subValue]) => {
            if (typeof subValue !== 'object' || subValue === null) {
              parameters[this.camelCase(`${key}_${subKey}`)] = subValue;
            }
          });
        }
      } else {
        parameters[this.camelCase(key)] = value;
      }
    });

    return parameters;
  }

  /**
   * Extract metrics from summary
   */
  extractMetrics(summary) {
    if (!summary?.metrics_summary) return {};

    const metrics = {};
    
    Object.entries(summary.metrics_summary).forEach(([key, value]) => {
      if (value && typeof value === 'object') {
        // Store the latest value as the main metric
        if ('latest' in value) {
          metrics[this.camelCase(key)] = value.latest;
        }
        // Also store statistical values with prefixes
        ['mean', 'max', 'min', 'stddev'].forEach(stat => {
          if (stat in value) {
            metrics[this.camelCase(`${key}_${stat}`)] = value[stat];
          }
        });
      }
    });

    return metrics;
  }

  /**
   * Process artifacts
   */
  processArtifacts(runPath, artifactsData) {
    const artifactsDir = path.join(runPath, 'artifacts');
    const artifacts = [];

    if (artifactsData && Array.isArray(artifactsData)) {
      artifactsData.forEach(artifact => {
        artifacts.push({
          _id: `${artifact.name}_${artifact.version}`,
          name: artifact.name,
          type: artifact.type,
          version: artifact.version,
          createdAt: artifact.created_at,
          fileCount: artifact.file_count || 0,
          metadata: artifact.metadata || {}
        });
      });
    }

    // Also check for artifacts directory
    try {
      if (fileSystemService.getFileStats(artifactsDir)) {
        const artifactDirs = fileSystemService.listFiles(artifactsDir, false);
        artifactDirs.forEach(dir => {
          if (!artifacts.find(a => a.name === dir.name)) {
            artifacts.push({
              _id: dir.name,
              name: dir.name,
              type: 'unknown',
              version: 'latest',
              createdAt: new Date().toISOString(),
              fileCount: 0,
              metadata: {}
            });
          }
        });
      }
    } catch (error) {
      // Artifacts directory might not exist
    }

    return artifacts;
  }

  /**
   * Get checkpoints for a run
   */
  getCheckpoints(runPath) {
    const checkpointsDir = path.join(runPath, 'checkpoints');
    const checkpoints = [];

    try {
      const files = fileSystemService.listFiles(checkpointsDir, false);
      files.forEach(file => {
        if (file.name.endsWith('.json')) {
          const checkpointData = fileSystemService.readJSON(file.path);
          if (checkpointData) {
            checkpoints.push({
              name: checkpointData.checkpoint_name || file.name.replace('.json', ''),
              path: file.path,
              createdAt: checkpointData.created_at,
              step: checkpointData.step,
              size: file.size
            });
          }
        }
      });
    } catch (error) {
      // Checkpoints directory might not exist
    }

    return checkpoints.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  }

  /**
   * Map SDK state to UI status
   */
  mapState(state) {
    const stateMap = {
      'initialized': 'running',
      'running': 'running',
      'completed': 'completed',
      'failed': 'failed',
      'interrupted': 'archived'
    };
    return stateMap[state] || 'running';
  }

  /**
   * Convert snake_case or spaces to camelCase
   */
  camelCase(str) {
    return str
      .split(/[_\s-]+/)
      .map((word, index) => 
        index === 0 
          ? word.toLowerCase() 
          : word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
      )
      .join('');
  }

  /**
   * Format duration in seconds to human-readable format
   */
  formatDuration(seconds) {
    if (!seconds || typeof seconds !== 'number') return '0s';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.round(seconds % 60);
    
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  /**
   * Update run tags
   */
  updateRunTags(project, runId, tags) {
    const runPath = fileSystemService.getRunPath(project, runId);
    if (runPath === null) return false;

    const metadataPath = path.join(runPath, 'metadata.json');

    const metadata = fileSystemService.readJSON(metadataPath);
    if (!metadata) return false;

    metadata.tags = tags;
    metadata.updated_at = new Date().toISOString();
    
    return fileSystemService.writeJSON(metadataPath, metadata);
  }

  /**
   * Update run description
   */
  updateRunDescription(project, runId, description) {
    const runPath = fileSystemService.getRunPath(project, runId);
    if (runPath === null) return false;

    const summaryPath = path.join(runPath, 'summary.json');

    let summary = fileSystemService.readJSON(summaryPath) || {};
    summary.notes = description;
    summary.updated_at = new Date().toISOString();
    
    return fileSystemService.writeJSON(summaryPath, summary);
  }

  /**
   * Get metrics time series for a run
   */
  getMetricsTimeSeries(project, runId, metricName = null) {
    const runPath = fileSystemService.getRunPath(project, runId);
    if (runPath === null) return [];

    const metricsPath = path.join(runPath, 'metrics.jsonl');
    const metrics = fileSystemService.readJSONL(metricsPath);

    if (!metricName) {
      // Return all metrics
      return metrics;
    }

    // Filter for specific metric
    return metrics
      .filter(entry => metricName in entry)
      .map(entry => ({
        timestamp: entry.timestamp,
        absolute_timestamp: entry.absolute_timestamp,
        step: entry.step,
        value: entry[metricName]
      }));
  }

  /**
   * Export metrics to CSV
   */
  exportMetricsToCSV(project, runId) {
    const runPath = fileSystemService.getRunPath(project, runId);
    if (runPath === null) return '';

    const metricsPath = path.join(runPath, 'metrics.jsonl');
    const metrics = fileSystemService.readJSONL(metricsPath);

    if (metrics.length === 0) return '';

    // Get all unique keys
    const allKeys = new Set();
    metrics.forEach(entry => {
      Object.keys(entry).forEach(key => allKeys.add(key));
    });

    const keys = Array.from(allKeys).sort();

    // Build CSV
    let csv = keys.join(',') + '\n';
    metrics.forEach(entry => {
      const row = keys.map(key => {
        const value = entry[key];
        if (value === undefined || value === null) return '';
        if (typeof value === 'object') return JSON.stringify(value);
        return value;
      });
      csv += row.join(',') + '\n';
    });

    return csv;
  }
}

export default new RunService();
