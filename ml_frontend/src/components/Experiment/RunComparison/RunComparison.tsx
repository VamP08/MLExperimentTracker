import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { FiCheck } from 'react-icons/fi';
import { groupFlatMetricsByName, type MetricGroup } from '../../../lib/metrics';
import { formatWhen, runBadge } from '../experiment';
import './RunComparison.css';
import { apiFetch } from '../../../lib/api';

/**
 * Row shape from `GET /api/experiment/:id/runs`. `metrics` and `parameters` are flat maps of
 * whatever the run logged. `duration` is a formatted string here (a number on the run detail
 * endpoint) and `startTime` may be missing.
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
  /** Runs to preselect. Defaults to the first two. */
  initialSelectedRunIds?: string[];
}

const MAX_SELECTED = 4;

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

  // Stable dependency for the selection effect; the caller passes a new array every render.
  const initialKey = JSON.stringify(initialSelectedRunIds || []);

  const fetchRuns = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      // Keep this relative. An absolute localhost:5000 URL is cross-origin and gets blocked.
      const response = await apiFetch(`/api/experiment/${experimentId}/runs`);

      if (!response.ok) {
        throw new Error(`Request failed with ${response.status}`);
      }

      const data: RunRow[] = await response.json();

      // The API flattens metrics_summary into sibling scalars; regroup them (see lib/metrics.ts).
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

  // Preselect the requested runs if they exist, otherwise the first two.
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
   * Spread of one metric across the selected runs. Doesn't mark a best value, since the
   * format doesn't record whether a metric should go up or down.
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
    return <div className="state">Loading runs…</div>;
  }

  if (error) {
    return <div className="state error">Could not load the runs: {error}</div>;
  }

  if (runs.length === 0) {
    return (
      <div className="state">
        <h3>Nothing to compare</h3>
        <p>This experiment has no runs yet.</p>
      </div>
    );
  }

  const runHeaders = selectedRunsData.map((run) => (
    <th key={run._id} scope="col">
      {run.name}
    </th>
  ));

  return (
    <div className="cmp">
      <p className="cmp-hint muted">
        Select up to {MAX_SELECTED} runs to compare{' '}
        <span className="num">({selectedRuns.length} selected)</span>
      </p>

      <div className="cmp-picker" role="group" aria-label="Runs to compare">
        {runs.map((run) => {
          const isSelected = selectedRuns.includes(run._id);
          return (
            <button
              key={run._id}
              type="button"
              aria-pressed={isSelected}
              className="cmp-pick"
              onClick={() => toggleRunSelection(run._id)}
            >
              <span className="cmp-box" aria-hidden="true">
                {isSelected && <FiCheck />}
              </span>
              <span className="cmp-pick-text">
                <span className="cmp-pick-name">{run.name}</span>
                <span className="cmp-pick-meta num">
                  {formatWhen(run.startTime) ?? '—'} · {run.duration}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      {selectedRunsData.length === 0 ? (
        <div className="state">Select at least one run above to compare.</div>
      ) : (
        <>
          <section className="cmp-section" aria-labelledby="cmp-metrics">
            <h3 id="cmp-metrics">Metrics</h3>
            {allMetrics.length === 0 ? (
              <p className="cmp-note">None of the selected runs recorded summary metrics.</p>
            ) : (
              <>
                <div className="table-wrap cmp-table">
                  <table className="table">
                    <thead>
                      <tr>
                        <th scope="col">Metric</th>
                        {runHeaders}
                      </tr>
                    </thead>
                    <tbody>
                      {allMetrics.map((metricName) => {
                        const spread = getMetricSpread(metricName);
                        return (
                          <tr key={metricName}>
                            <th scope="row">{metricName}</th>
                            {selectedRunsData.map((run) => {
                              const value = run.metrics[metricName]?.latest;
                              const extreme =
                                spread && value === spread.max
                                  ? 'max'
                                  : spread && value === spread.min
                                    ? 'min'
                                    : null;
                              return (
                                <td key={run._id} className={`num${extreme ? ' cmp-extreme' : ''}`}>
                                  {value !== undefined ? value.toFixed(4) : '—'}
                                  {extreme && <span className="cmp-mark">{extreme}</span>}
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="cmp-note">
                  Highest and lowest values are marked. Which end is better depends on the
                  metric, and the run format does not record that.
                </p>
              </>
            )}
          </section>

          <section className="cmp-section" aria-labelledby="cmp-params">
            <h3 id="cmp-params">Parameters</h3>
            {allParams.length === 0 ? (
              <p className="cmp-note">None of the selected runs recorded top-level parameters.</p>
            ) : (
              <div className="table-wrap cmp-table">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Parameter</th>
                      {runHeaders}
                    </tr>
                  </thead>
                  <tbody>
                    {allParams.map((paramName) => {
                      const differs = getParameterDiff(paramName);
                      return (
                        <tr key={paramName} className={differs ? 'cmp-differs' : undefined}>
                          <th scope="row">
                            {paramName}
                            {differs && <span className="cmp-mark">differs</span>}
                          </th>
                          {selectedRunsData.map((run) => (
                            <td key={run._id} className="num">
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
          </section>

          <section className="cmp-section" aria-labelledby="cmp-meta">
            <h3 id="cmp-meta">Run metadata</h3>
            <div className="table-wrap cmp-table">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Property</th>
                    {runHeaders}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">Status</th>
                    {selectedRunsData.map((run) => {
                      const badge = runBadge(run.status);
                      return (
                        <td key={run._id}>
                          <span className={`badge ${badge.tone}`}>{badge.label}</span>
                        </td>
                      );
                    })}
                  </tr>
                  <tr>
                    <th scope="row">Started</th>
                    {selectedRunsData.map((run) => (
                      <td key={run._id} className="num">
                        {formatWhen(run.startTime) ?? '—'}
                      </td>
                    ))}
                  </tr>
                  <tr>
                    <th scope="row">Duration</th>
                    {selectedRunsData.map((run) => (
                      <td key={run._id} className="num">
                        {run.duration}
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
};

export default RunComparison;
