import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { groupFlatMetricsByName, type MetricGroup } from '../../../lib/metrics';
import './RunComparison.css';

/**
 * The rows `GET /api/experiment/:id/runs` actually sends — seven keys, no more.
 * `metrics` and `parameters` are flat maps built from whatever the run logged,
 * so neither has a fixed key set; `duration` is a pre-formatted string on this
 * endpoint and a number on the run-detail one; `startTime` is the run's
 * `created_at` and may be absent.
 */
interface RunRow {
  _id: string;
  name: string;
  status: string;
  duration: string;
  startTime: string | null;
  parameters: Record<string, unknown>;
  metrics: Record<string, unknown>;
}

/** A row with its flat metrics regrouped into per-metric stat objects. */
interface ComparedRun extends Omit<RunRow, 'metrics'> {
  metrics: Record<string, MetricGroup>;
}

interface Props {
  experimentId: string;
  /**
   * Runs to select on open. Used when the comparison is launched from a
   * selection the user already made; without it the first two runs are
   * selected so the table is not empty on arrival.
   */
  initialSelectedRunIds?: string[];
}

const MAX_SELECTED = 4;

const formatDate = (value: string | null): string => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
};

const formatParameter = (value: unknown): string => {
  if (value === undefined || value === null) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
};

const RunComparison: React.FC<Props> = ({ experimentId, initialSelectedRunIds }) => {
  const [runs, setRuns] = useState<ComparedRun[]>([]);
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // A stable dependency for the selection effect below. The caller builds a new
  // array on every render; keying on its contents keeps the effect from looping.
  const initialKey = JSON.stringify(initialSelectedRunIds || []);

  const fetchRuns = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      // Relative URL. An absolute http://localhost:5000 origin is cross-origin
      // from both the dev server and the bundle the API itself serves, and gets
      // blocked before the response is read.
      const response = await fetch(`/api/experiment/${experimentId}/runs`);

      if (!response.ok) {
        throw new Error(`Request failed with ${response.status}`);
      }

      const data: RunRow[] = await response.json();

      // The API flattens `metrics_summary` into sibling scalars; regroup them
      // into the nested shape this table reads. See lib/metrics.ts.
      const compared: ComparedRun[] = (Array.isArray(data) ? data : []).map((run) => ({
        ...run,
        metrics: groupFlatMetricsByName(run.metrics || {}),
        parameters: run.parameters || {},
      }));

      setRuns(compared);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [experimentId]);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  // Seed the selection once the rows are in hand: the runs the caller asked for
  // if they exist, otherwise the first two so the table is not empty on arrival.
  useEffect(() => {
    const requested = JSON.parse(initialKey) as string[];
    const present = requested.filter((id) => runs.some((run) => run._id === id));
    setSelectedRuns(
      present.length > 0
        ? present.slice(0, MAX_SELECTED)
        : runs.slice(0, 2).map((run) => run._id),
    );
  }, [runs, initialKey]);

  const toggleRunSelection = (runId: string) => {
    setSelectedRuns((prev) => {
      if (prev.includes(runId)) {
        return prev.filter((id) => id !== runId);
      }
      if (prev.length < MAX_SELECTED) {
        return [...prev, runId];
      }
      return prev;
    });
  };

  const selectedRunsData = useMemo(
    () =>
      selectedRuns
        .map((id) => runs.find((run) => run._id === id))
        .filter((run): run is ComparedRun => run !== undefined),
    [selectedRuns, runs],
  );

  const { allMetrics, allParams } = useMemo(() => {
    const metrics = new Set<string>();
    const params = new Set<string>();
    selectedRunsData.forEach((run) => {
      Object.keys(run.metrics).forEach((name) => metrics.add(name));
      Object.keys(run.parameters).forEach((name) => params.add(name));
    });
    return {
      allMetrics: Array.from(metrics).sort((a, b) => a.localeCompare(b)),
      allParams: Array.from(params).sort((a, b) => a.localeCompare(b)),
    };
  }, [selectedRunsData]);

  /**
   * The spread of one metric across the selected runs. It deliberately does not
   * decide which end is good: nothing in the storage format records whether a
   * metric should be maximised, and calling the largest loss the best value
   * would be an invented claim.
   */
  const getMetricSpread = (metricName: string) => {
    const values = selectedRunsData
      .map((run) => run.metrics[metricName]?.latest)
      .filter((value): value is number => typeof value === 'number');

    if (values.length < 2) return null;

    const max = Math.max(...values);
    const min = Math.min(...values);
    return max > min ? { max, min } : null;
  };

  const getParameterDiff = (paramName: string) =>
    new Set(
      selectedRunsData
        .filter((run) => run.parameters[paramName] !== undefined)
        .map((run) => formatParameter(run.parameters[paramName])),
    ).size > 1;

  if (loading) {
    return <div className="runcomparison-placeholder">Loading runs...</div>;
  }

  if (error) {
    return (
      <div className="runcomparison-placeholder runcomparison-error">Error: {error}</div>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="runcomparison-placeholder">No runs available for comparison</div>
    );
  }

  const runHeaders = selectedRunsData.map((run) => (
    <th key={run._id} className="runcomparison-th">
      <span className="runcomparison-run-title">{run.name}</span>
    </th>
  ));

  return (
    <div className="runcomparison">
      <div className="runcomparison-heading">
        <h3 className="runcomparison-title">Compare Runs</h3>
        <p className="runcomparison-subtitle">
          Select up to {MAX_SELECTED} runs to compare (selected: {selectedRuns.length})
        </p>
      </div>

      <div className="runcomparison-selector">
        {runs.map((run) => {
          const isSelected = selectedRuns.includes(run._id);
          return (
            <button
              key={run._id}
              type="button"
              aria-pressed={isSelected}
              className={`runcomparison-run-item${
                isSelected ? ' runcomparison-run-item-selected' : ''
              }`}
              onClick={() => toggleRunSelection(run._id)}
            >
              <span className="runcomparison-run-check">{isSelected ? '✓' : ''}</span>
              <span className="runcomparison-run-info">
                <span className="runcomparison-run-name">{run.name}</span>
                <span className="runcomparison-run-meta">
                  {formatDate(run.startTime)} • {run.duration}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      {selectedRunsData.length === 0 ? (
        <p className="runcomparison-placeholder">
          Select at least one run above to compare.
        </p>
      ) : (
        <>
          <div className="runcomparison-section">
            <h4 className="runcomparison-section-title">Metrics</h4>
            {allMetrics.length === 0 ? (
              <p className="runcomparison-note">
                None of the selected runs recorded summary metrics.
              </p>
            ) : (
              <>
                <div className="runcomparison-table-wrap">
                  <table className="runcomparison-table">
                    <thead>
                      <tr>
                        <th className="runcomparison-th">Metric</th>
                        {runHeaders}
                      </tr>
                    </thead>
                    <tbody>
                      {allMetrics.map((metricName) => {
                        const spread = getMetricSpread(metricName);
                        return (
                          <tr key={metricName}>
                            <td className="runcomparison-row-label">{metricName}</td>
                            {selectedRunsData.map((run) => {
                              const value = run.metrics[metricName]?.latest;
                              const extreme =
                                spread && value === spread.max
                                  ? ' runcomparison-metric-high'
                                  : spread && value === spread.min
                                    ? ' runcomparison-metric-low'
                                    : '';
                              return (
                                <td
                                  key={run._id}
                                  className={`runcomparison-metric-value${extreme}`}
                                >
                                  {value !== undefined ? value.toFixed(4) : '—'}
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="runcomparison-note">
                  Highest and lowest values are highlighted. Which end is better depends
                  on the metric, and the run format does not record that.
                </p>
              </>
            )}
          </div>

          <div className="runcomparison-section">
            <h4 className="runcomparison-section-title">Parameters</h4>
            {allParams.length === 0 ? (
              <p className="runcomparison-note">
                None of the selected runs recorded top-level parameters.
              </p>
            ) : (
              <div className="runcomparison-table-wrap">
                <table className="runcomparison-table">
                  <thead>
                    <tr>
                      <th className="runcomparison-th">Parameter</th>
                      {runHeaders}
                    </tr>
                  </thead>
                  <tbody>
                    {allParams.map((paramName) => {
                      const differs = getParameterDiff(paramName);
                      return (
                        <tr
                          key={paramName}
                          className={differs ? 'runcomparison-row-differs' : undefined}
                        >
                          <td className="runcomparison-row-label">
                            {paramName}
                            {differs && (
                              <span
                                className="runcomparison-diff-dot"
                                title="Values differ between the selected runs"
                              >
                                •
                              </span>
                            )}
                          </td>
                          {selectedRunsData.map((run) => (
                            <td key={run._id} className="runcomparison-param-value">
                              {formatParameter(run.parameters[paramName])}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="runcomparison-section">
            <h4 className="runcomparison-section-title">Run metadata</h4>
            <div className="runcomparison-table-wrap">
              <table className="runcomparison-table">
                <thead>
                  <tr>
                    <th className="runcomparison-th">Property</th>
                    {runHeaders}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="runcomparison-row-label">Status</td>
                    {selectedRunsData.map((run) => (
                      <td key={run._id}>{run.status}</td>
                    ))}
                  </tr>
                  <tr>
                    <td className="runcomparison-row-label">Started</td>
                    {selectedRunsData.map((run) => (
                      <td key={run._id}>{formatDate(run.startTime)}</td>
                    ))}
                  </tr>
                  <tr>
                    <td className="runcomparison-row-label">Duration</td>
                    {selectedRunsData.map((run) => (
                      <td key={run._id}>{run.duration}</td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default RunComparison;
