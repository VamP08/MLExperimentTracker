import path from 'path';
import fileSystemService from './fileSystem.service.js';

/**
 * Service to manage experiments from local file system
 */
class ExperimentService {
  /**
   * Get all experiments (unique project names)
   */
  getAllExperiments() {
    const projects = fileSystemService.getProjects();
    
    return projects.map(projectName => {
      const runs = fileSystemService.getRunsForProject(projectName);
      
      // Get latest run to determine last activity
      let lastActivity = null;
      let totalRuns = runs.length;
      let completedRuns = 0;
      let failedRuns = 0;
      let runningRuns = 0;
      let totalDuration = 0;
      let tags = new Set();
      let description = '';
      let runsData = [];

      runs.forEach(runId => {
        const runPath = fileSystemService.getRunPath(projectName, runId);
        const metadata = fileSystemService.readJSON(path.join(runPath, 'metadata.json'));
        const summary = fileSystemService.readJSON(path.join(runPath, 'summary.json'));

        if (metadata) {
          // Create run object for frontend
          const runObj = {
            _id: runId,
            name: metadata.name || runId,
            status: summary?.state || 'unknown',
            startedAt: metadata.created_at || new Date().toISOString()
          };
          runsData.push(runObj);

          // Track run states
          if (summary?.state === 'completed') completedRuns++;
          else if (summary?.state === 'failed') failedRuns++;
          else if (summary?.state === 'running') runningRuns++;

          // Get latest activity
          const createdAt = new Date(metadata.created_at);
          if (!lastActivity || createdAt > lastActivity) {
            lastActivity = createdAt;
          }

          // Aggregate tags
          if (metadata.tags) {
            metadata.tags.forEach(tag => tags.add(tag));
          }

          // Get description from first run or summary
          if (!description && (metadata.notes || summary?.notes)) {
            description = metadata.notes || summary?.notes;
          }
        }

        if (summary?.duration) {
          totalDuration += summary.duration;
        }
      });

      const successRate = totalRuns > 0 ? Math.round((completedRuns / totalRuns) * 100) : 0;
      const avgDuration = totalRuns > 0 ? totalDuration / totalRuns : 0;

      return {
        _id: projectName,
        name: projectName,
        description: description || `Experiment: ${projectName}`,
        tags: Array.from(tags),
        runs: runsData,
        activityTimeline: [],
        createdAt: lastActivity,
        stats: {
          totalRuns,
          completedRuns,
          failedRuns,
          runningRuns,
          successRate: `${successRate}%`,
          avgDuration: this.formatDuration(avgDuration),
          lastRun: lastActivity ? lastActivity.toISOString() : 'N/A'
        }
      };
    });
  }

  /**
   * Get a specific experiment by name (project name)
   */
  getExperimentByName(projectName) {
    const experiments = this.getAllExperiments();
    return experiments.find(exp => exp.name === projectName) || null;
  }

  /**
   * Get the latest experiment (by last activity)
   */
  getLatestExperiment() {
    const experiments = this.getAllExperiments();
    if (experiments.length === 0) return null;

    return experiments.reduce((latest, current) => {
      const latestDate = latest.createdAt ? new Date(latest.createdAt) : new Date(0);
      const currentDate = current.createdAt ? new Date(current.createdAt) : new Date(0);
      return currentDate > latestDate ? current : latest;
    });
  }

  /**
   * Get runs for a specific experiment
   */
  getRunsForExperiment(projectName) {
    const runIds = fileSystemService.getRunsForProject(projectName);
    
    return runIds.map(runId => {
      const runPath = fileSystemService.getRunPath(projectName, runId);
      const metadata = fileSystemService.readJSON(path.join(runPath, 'metadata.json'));
      const summary = fileSystemService.readJSON(path.join(runPath, 'summary.json'));
      const config = fileSystemService.readJSON(path.join(runPath, 'config.json'));

      if (!metadata) return null;

      // Extract parameters from config
      const parameters = {};
      if (config) {
        Object.entries(config).forEach(([key, value]) => {
          if (typeof value !== 'object' || value === null) {
            parameters[this.camelCase(key)] = value;
          }
        });
      }

      // Extract latest metrics from summary
      const metrics = {};
      if (summary?.metrics_summary) {
        Object.entries(summary.metrics_summary).forEach(([key, value]) => {
          if (value && typeof value === 'object' && 'latest' in value) {
            metrics[this.camelCase(key)] = value.latest;
          }
        });
      }

      const duration = summary?.duration || 0;

      return {
        _id: runId,
        name: `Run ${runId}`,
        status: this.mapState(summary?.state || metadata.state),
        duration: this.formatDuration(duration),
        startTime: metadata.created_at,
        parameters,
        metrics
      };
    }).filter(run => run !== null);
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
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    if (m === 0) return `${s}s`;
    return `${m}m ${s}s`;
  }

  /**
   * Update experiment description
   */
  updateDescription(projectName, description) {
    // Store description in a separate metadata file for the project.
    // Refuse names that resolve outside the storage root: writeJSON creates the
    // directory chain before writing, so an unchecked name here is an arbitrary
    // file write rather than a failed lookup.
    const projectPath = fileSystemService.getProjectPath(projectName);
    if (projectPath === null) return false;

    const metadataPath = path.join(projectPath, 'project_metadata.json');

    const metadata = fileSystemService.readJSON(metadataPath) || {};
    metadata.description = description;
    metadata.updated_at = new Date().toISOString();
    
    return fileSystemService.writeJSON(metadataPath, metadata);
  }
}

export default new ExperimentService();
