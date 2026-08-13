/**
 * Regrouping the API's flat metric scalars back into per-metric stat groups.
 *
 * The backend flattens `summary.json`'s `metrics_summary` before it leaves the
 * server, so no endpoint emits the nested shape. `GET /api/run/:id` sends
 * `loss`, `lossMean`, `lossMax`, `lossMin`, `lossStddev` as five sibling
 * scalars, and `GET /api/experiment/:id/runs` sends the narrower view — the
 * `latest` value only, with no stat siblings at all.
 *
 * Two consumers independently assumed the nested shape and rendered undefined
 * everywhere (GAPS B8 and M23). The decision was to regroup on the client
 * rather than to change the API: `GET /api/experiment/:id/runs` is one of the
 * routes `tests/test_parity.py` diffs field by field against the retained
 * Express implementation, and reshaping a response to suit one component would
 * spend that evidence for nothing.
 *
 * This module is the single copy of that regrouping.
 */

/** The stat siblings the backend appends to a metric's camelCased name. */
export const STAT_SUFFIXES = ['Mean', 'Max', 'Min', 'Stddev'] as const;

/** One metric, reassembled from its scalar siblings. */
export interface MetricGroup {
  /** The camelCased metric name as the API sends it. */
  name: string;
  /** Always present — a group exists only because its `latest` value does. */
  latest: number;
  mean?: number;
  min?: number;
  max?: number;
  stddev?: number;
}

/**
 * Reassemble a flat metric map into per-metric groups, sorted by name.
 *
 * Only finite numbers participate: a metric whose value arrived as `null`
 * (which is how the server encodes a non-finite float) contributes no group
 * rather than a group reading NaN.
 *
 * Known ambiguity: a metric genuinely named `loss_mean` arrives as `lossMean`
 * and is indistinguishable from the derived mean of `loss`, so it is absorbed
 * into the `loss` group instead of appearing on its own. Avoid metric names
 * ending in _mean/_max/_min/_stddev — see DATA-CONTRACT.md, and GAPS N8 for the
 * camelCase collisions this shares a cause with.
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

/**
 * Index groups by name, for consumers that look a metric up rather than
 * iterate. Callers that need the nested `{latest, mean, min, max}` shape read
 * this instead of the flat response.
 */
export function groupFlatMetricsByName(
  flat: Record<string, unknown>,
): Record<string, MetricGroup> {
  const byName: Record<string, MetricGroup> = {};
  for (const group of groupFlatMetrics(flat)) byName[group.name] = group;
  return byName;
}

/**
 * The one-line summary of a group's stat siblings, for a caption under the
 * latest value. Returns an explanatory sentence rather than an empty string
 * when a metric has no summary statistics, because a blank caption reads as a
 * rendering failure and a missing `metrics_summary` entry is not one.
 */
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
