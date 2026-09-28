/**
 * Regroups the API's flat metric scalars (`loss`, `lossMean`, `lossMax`, ...) into per-metric
 * stat groups. `/api/experiment/:id/runs` sends only the latest value.
 */

/** The stat siblings the backend appends to a metric's camelCased name. */
export const STAT_SUFFIXES = ['Mean', 'Max', 'Min', 'Stddev'] as const;

/** One metric, reassembled from its scalar siblings. */
export interface MetricGroup {
  /** The camelCased metric name as the API sends it. */
  name: string;
  /** Always present; a group only exists if `latest` does. */
  latest: number;
  mean?: number;
  min?: number;
  max?: number;
  stddev?: number;
}

/**
 * Group a flat metric map by metric, sorted by name. Non-finite values (sent as null) are skipped.
 * A metric actually named `loss_mean` arrives as `lossMean` and gets merged into `loss`.
 */
export function groupFlatMetrics(flat: Record<string, unknown>): MetricGroup[] {
  const numeric = new Map<string, number>();
  for (const [key, value] of Object.entries(flat)) {
    if (typeof value === 'number' && Number.isFinite(value)) numeric.set(key, value);
  }

  const isStatOf = (key: string): string | null => {
    for (const suffix of STAT_SUFFIXES) {
      if (key.endsWith(suffix)) {
        const base = key.slice(0, -suffix.length);
        if (base.length > 0 && numeric.has(base)) return base;
      }
    }
    return null;
  };

  const groups: MetricGroup[] = [];
  for (const [key, latest] of numeric) {
    if (isStatOf(key) !== null) continue;

    groups.push({
      name: key,
      latest,
      mean: numeric.get(`${key}Mean`),
      min: numeric.get(`${key}Min`),
      max: numeric.get(`${key}Max`),
      stddev: numeric.get(`${key}Stddev`),
    });
  }

  return groups.sort((a, b) => a.name.localeCompare(b.name));
}

/** Groups keyed by name, for lookups. */
export function groupFlatMetricsByName(
  flat: Record<string, unknown>,
): Record<string, MetricGroup> {
  const byName: Record<string, MetricGroup> = {};
  for (const group of groupFlatMetrics(flat)) byName[group.name] = group;
  return byName;
}

/** One-line summary of a group's stats for a caption, or a short note when there are none. */
export function describeMetricStats(group: MetricGroup): string {
  const stats = [
    group.mean !== undefined ? `Mean: ${group.mean.toFixed(4)}` : null,
    group.min !== undefined ? `Min: ${group.min.toFixed(4)}` : null,
    group.max !== undefined ? `Max: ${group.max.toFixed(4)}` : null,
  ].filter(Boolean);

  return stats.length > 0
    ? stats.join(', ')
    : 'Latest value only — no summary statistics logged.';
}

/** The server's key transform, ported: `val_loss` → `valLoss`, `learning-rate` → `learningRate`. */
export function camelCase(key: string): string {
  const parts = key.split(/[_\s-]+/);
  return parts[0].toLowerCase() + parts.slice(1).map((p) => p.charAt(0).toUpperCase() + p.slice(1).toLowerCase()).join('');
}

const RESERVED = new Set(['timestamp', 'absolute_timestamp', 'step', 'run_id', 'run_status', 'run_state', 'start_timestamp']);

/**
 * Map camelCased metric keys back to their logged names. `metrics` is camelCased by the server
 * but `metricsHistory` keeps the raw names, so `val_loss` can be shown the same on every tab.
 */
export function recordedNames(history: unknown): Map<string, string> {
  const names = new Map<string, string>();
  if (!Array.isArray(history)) return names;
  for (const row of history) {
    if (!row || typeof row !== 'object') continue;
    for (const key of Object.keys(row)) {
      if (!RESERVED.has(key) && !names.has(camelCase(key))) names.set(camelCase(key), key);
    }
  }
  return names;
}

/** Headline metric: val accuracy, then any accuracy, then any loss, plus which way is better. */
export function headlineMetric(keys: string[]): { key: string; better: 'max' | 'min' } | null {
  const pick =
    keys.find((k) => /^val_?acc/i.test(k)) ??
    keys.find((k) => /acc/i.test(k)) ??
    keys.find((k) => /loss/i.test(k)) ??
    keys[0];
  if (!pick) return null;
  return { key: pick, better: /loss|err/i.test(pick) ? 'min' : 'max' };
}
