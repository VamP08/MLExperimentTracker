import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Default storage path - can be overridden by environment variable
const STORAGE_PATH = process.env.EXPERIMENT_STORAGE_PATH || 
  path.join(process.env.USERPROFILE || process.env.HOME, '.experiment_tracker');

/**
 * Service to interact with the local file system for experiment tracking
 */
class FileSystemService {
  constructor(storagePath = STORAGE_PATH) {
    this.storagePath = storagePath;
  }

  /**
   * Get all projects (directories in storage path)
   */
  getProjects() {
    try {
      if (!fs.existsSync(this.storagePath)) {
        return [];
      }

      return fs.readdirSync(this.storagePath, { withFileTypes: true })
        .filter(dirent => dirent.isDirectory())
        .map(dirent => dirent.name);
    } catch (error) {
      console.error('Error reading projects:', error);
      return [];
    }
  }

  /**
   * Get all runs for a specific project
   */
  getRunsForProject(projectName) {
    const projectPath = this.getProjectPath(projectName);

    try {
      if (projectPath === null || !fs.existsSync(projectPath)) {
        return [];
      }

      return fs.readdirSync(projectPath, { withFileTypes: true })
        .filter(dirent => dirent.isDirectory())
        .map(dirent => dirent.name);
    } catch (error) {
      console.error(`Error reading runs for project ${projectName}:`, error);
      return [];
    }
  }

  /**
   * Get all runs across all projects
   */
  getAllRuns() {
    const projects = this.getProjects();
    const allRuns = [];

    for (const project of projects) {
      const runs = this.getRunsForProject(project);
      runs.forEach(runId => {
        allRuns.push({ project, runId });
      });
    }

    return allRuns;
  }

  /**
   * Read a JSON file safely
   */
  readJSON(filePath) {
    try {
      if (!fs.existsSync(filePath)) {
        return null;
      }
      const data = fs.readFileSync(filePath, 'utf8');
      return JSON.parse(data);
    } catch (error) {
      console.error(`Error reading JSON file ${filePath}:`, error);
      return null;
    }
  }

  /**
   * Read a JSONL file (one JSON object per line)
   */
  readJSONL(filePath) {
    try {
      if (!fs.existsSync(filePath)) {
        return [];
      }
      const data = fs.readFileSync(filePath, 'utf8');
      return data
        .split('\n')
        .filter(line => line.trim())
        .map(line => {
          try {
            return JSON.parse(line);
          } catch (e) {
            console.error('Error parsing JSONL line:', e);
            return null;
          }
        })
        .filter(item => item !== null);
    } catch (error) {
      console.error(`Error reading JSONL file ${filePath}:`, error);
      return [];
    }
  }

  /**
   * Write JSON file
   */
  writeJSON(filePath, data) {
    try {
      const dir = path.dirname(filePath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf8');
      return true;
    } catch (error) {
      console.error(`Error writing JSON file ${filePath}:`, error);
      return false;
    }
  }

  /**
   * Resolve a path inside the storage root, refusing anything that escapes it.
   *
   * Project names and run IDs arrive straight from URL path parameters, which
   * Express has already URL-decoded — so a `%2f` in the request is a real
   * separator by the time it gets here. Each segment must therefore be a single
   * path component. The resolved-prefix check at the end is defence in depth in
   * case a platform quirk gets past the per-segment rules.
   *
   * Returns null when the input is not addressable; callers must treat that as
   * "not found" rather than joining it.
   */
  resolveWithin(...segments) {
    for (const segment of segments) {
      if (typeof segment !== 'string' || segment.length === 0) return null;
      if (segment === '.' || segment === '..') return null;
      if (segment.includes('\0')) return null;
      if (path.isAbsolute(segment)) return null;
      // Rejects every segment containing a separator, on both path flavours.
      if (segment !== path.basename(segment)) return null;
    }

    const root = path.resolve(this.storagePath);
    const target = path.resolve(root, ...segments);
    if (target !== root && !target.startsWith(root + path.sep)) return null;

    return target;
  }

  /**
   * Get project directory path, or null if the name is not addressable
   */
  getProjectPath(project) {
    return this.resolveWithin(project);
  }

  /**
   * Get run directory path, or null if either name is not addressable
   */
  getRunPath(project, runId) {
    return this.resolveWithin(project, runId);
  }

  /**
   * Check if run exists
   */
  runExists(project, runId) {
    const runPath = this.getRunPath(project, runId);
    return runPath !== null && fs.existsSync(runPath);
  }

  /**
   * Get file stats
   */
  getFileStats(filePath) {
    try {
      if (!fs.existsSync(filePath)) {
        return null;
      }
      return fs.statSync(filePath);
    } catch (error) {
      console.error(`Error getting file stats for ${filePath}:`, error);
      return null;
    }
  }

  /**
   * List files in directory
   */
  listFiles(directoryPath, recursive = false) {
    try {
      if (!fs.existsSync(directoryPath)) {
        return [];
      }

      const files = [];
      const items = fs.readdirSync(directoryPath, { withFileTypes: true });

      for (const item of items) {
        const fullPath = path.join(directoryPath, item.name);
        if (item.isFile()) {
          files.push({
            name: item.name,
            path: fullPath,
            size: fs.statSync(fullPath).size
          });
        } else if (item.isDirectory() && recursive) {
          files.push(...this.listFiles(fullPath, recursive));
        }
      }

      return files;
    } catch (error) {
      console.error(`Error listing files in ${directoryPath}:`, error);
      return [];
    }
  }
}

export default new FileSystemService();
