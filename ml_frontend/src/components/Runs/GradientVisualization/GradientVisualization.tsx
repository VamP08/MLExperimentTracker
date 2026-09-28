import React, { useEffect, useState, useCallback } from 'react';
import './GradientVisualization.css';
import LineChart from '../MetricsChart/LineChart';
import { apiFetch } from '../../../lib/api';

type ViewMode = 'norm' | 'mean' | 'std';

interface GradientData {
  step: number;
  timestamp: number;
  layerName: string;
  // All optional. A missing stat stays missing; 0 would look like a vanished gradient.
  gradientMean?: number;
  gradientStd?: number;
  gradientMin?: number;
  gradientMax?: number;
  gradientNorm?: number;
}

interface Props {
  runId: string;
}

// metrics.jsonl rows are wide: one object per step, one key per metric.
// Gradient series use keys shaped `gradient/<layer>/<stat>`.
const RESERVED_KEYS = new Set([
  'timestamp',
  'absolute_timestamp',
  'step',
  'run_id',
  'run_status',
  'run_state',
  'start_timestamp',
]);

type StatField = 'gradientMean' | 'gradientStd' | 'gradientMin' | 'gradientMax' | 'gradientNorm';

const STAT_FIELD: Record<string, StatField> = {
  mean: 'gradientMean',
  std: 'gradientStd',
  min: 'gradientMin',
  max: 'gradientMax',
  norm: 'gradientNorm',
};

const VIEW_FIELD: Record<ViewMode, StatField> = {
  norm: 'gradientNorm',
  mean: 'gradientMean',
  std: 'gradientStd',
};

const VIEW_LABEL: Record<ViewMode, string> = {
  norm: 'Norm',
  mean: 'Mean (abs)',
  std: 'Std Dev',
};

const STATUS_TONE: Record<string, string> = {
  healthy: 'ok',
  vanishing: 'drift',
  exploding: 'fail',
};

const valueFor = (grad: GradientData, mode: ViewMode): number | undefined => {
  const value = grad[VIEW_FIELD[mode]];
  if (value === undefined) return undefined;
  return mode === 'mean' ? Math.abs(value) : value;
};

const GradientVisualization: React.FC<Props> = ({ runId }) => {
  const [gradients, setGradients] = useState<GradientData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedLayer, setSelectedLayer] = useState<string | 'all'>('all');
  const [viewMode, setViewMode] = useState<ViewMode>('norm');

  const fetchGradients = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      // Without a `metric` param this returns the raw wide rows, which is what we need here.
      const response = await apiFetch(`/api/run/${runId}/metrics/timeseries`);

      if (!response.ok) {
        throw new Error(`Failed to fetch gradients: ${response.statusText}`);
      }

      const rows = await response.json();
      if (!Array.isArray(rows)) {
        setGradients([]);
        return;
      }

      // Key by step + layer so all stats for a layer at one step end up in one record.
      const byStepAndLayer = new Map<string, GradientData>();

      for (const row of rows as Array<Record<string, unknown>>) {
        if (!row || typeof row !== 'object') continue;

        const step = typeof row.step === 'number' ? row.step : undefined;
        if (step === undefined) continue;
        const timestamp = typeof row.timestamp === 'number' ? row.timestamp : 0;

        for (const [key, value] of Object.entries(row)) {
          if (RESERVED_KEYS.has(key)) continue;
          if (typeof value !== 'number' || !Number.isFinite(value)) continue;

          const parts = key.split('/');
          // gradient / <layer, which may itself contain slashes> / <stat>
          if (parts.length < 3 || parts[0] !== 'gradient') continue;

          const field = STAT_FIELD[parts[parts.length - 1]];
          if (!field) continue;
          const layerName = parts.slice(1, -1).join('/');

          const mapKey = `${step}\u0000${layerName}`;
          let entry = byStepAndLayer.get(mapKey);
          if (!entry) {
            entry = { step, timestamp, layerName };
            byStepAndLayer.set(mapKey, entry);
          }
          entry[field] = value;
        }
      }

      const gradientMetrics = Array.from(byStepAndLayer.values()).sort((a, b) => a.step - b.step);
      setGradients(gradientMetrics);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchGradients();
  }, [fetchGradients]);

  const layers = Array.from(new Set(gradients.map(g => g.layerName)));

  // Only offer stats this run logged; fall back to the first available one.
  const availableModes = (['norm', 'mean', 'std'] as ViewMode[]).filter(mode =>
    gradients.some(g => valueFor(g, mode) !== undefined)
  );
  const activeMode: ViewMode = availableModes.includes(viewMode)
    ? viewMode
    : availableModes[0] ?? 'norm';

  const filteredGradients = selectedLayer === 'all'
    ? gradients
    : gradients.filter(g => g.layerName === selectedLayer);

  const getGradientValue = (grad: GradientData): number | undefined => valueFor(grad, activeMode);

  // Vanishing/exploding check uses norms only, so a run that logged just mean/std gets no verdict.
  const analyzeGradients = () => {
    const norms = gradients
      .map(g => g.gradientNorm)
      .filter((n): n is number => n !== undefined);
    if (norms.length === 0) return null;

    const meanNorm = norms.reduce((a, b) => a + b, 0) / norms.length;
    const maxNorm = Math.max(...norms);
    const minNorm = Math.min(...norms);

    const vanishing = meanNorm < 1e-7;
    const exploding = maxNorm > 100;

    return {
      meanNorm: meanNorm.toExponential(3),
      maxNorm: maxNorm.toExponential(3),
      minNorm: minNorm.toExponential(3),
      status: vanishing ? 'vanishing' : exploding ? 'exploding' : 'healthy',
    };
  };

  // No loading state: most runs have nothing to show here, so don't flash a panel.
  if (loading) {
    return null;
  }

  if (error) {
    return (
      <section className="panel" aria-labelledby="gv-head">
        <div className="panel-head">
          <h2 id="gv-head">Gradient flow</h2>
        </div>
        <div className="state error">Could not load gradient series: {error}</div>
      </section>
    );
  }

  // Most runs log no gradient stats; render nothing.
  if (gradients.length === 0 || availableModes.length === 0) {
    return null;
  }

  const analysis = analyzeGradients();
  const layersToShow = selectedLayer === 'all' ? layers.slice(0, 5) : [selectedLayer];
  // Index in the full layer list so a layer keeps its colour when filtered.
  const series = layersToShow.map((layer) => ({
    name: layer,
    index: layers.indexOf(layer),
    pts: filteredGradients
      .filter((g) => g.layerName === layer)
      .map((g) => ({ x: g.step, y: getGradientValue(g) }))
      .filter((p): p is { x: number; y: number } => p.y !== undefined)
      .sort((a, b) => a.x - b.x),
  }));
  const steps = new Set(series.flatMap((s) => s.pts.map((p) => p.x)));
  const exp = (v: number) => v.toExponential(3);

  return (
    <section className="panel" aria-labelledby="gv-head">
      <div className="panel-head">
        <h2 id="gv-head">Gradient flow</h2>
        {analysis && (
          <span className={`badge ${STATUS_TONE[analysis.status]}`}>{analysis.status}</span>
        )}
        {analysis && (
          <span className="sub num">
            norm mean <span className="mono">{analysis.meanNorm}</span> · max{' '}
            <span className="mono">{analysis.maxNorm}</span> · min <span className="mono">{analysis.minNorm}</span>
          </span>
        )}
        <span className="spacer" />
        {availableModes.length > 1 && (
          <label>
            <span className="sr-only">Statistic</span>
            <select className="select" value={activeMode} onChange={(e) => setViewMode(e.target.value as ViewMode)}>
              {availableModes.map((mode) => (
                <option key={mode} value={mode}>
                  Gradient {VIEW_LABEL[mode]}
                </option>
              ))}
            </select>
          </label>
        )}
        {layers.length > 1 && (
          <label>
            <span className="sr-only">Layer</span>
            <select className="select gv-layer" value={selectedLayer} onChange={(e) => setSelectedLayer(e.target.value)}>
              <option value="all">All layers (first 5)</option>
              {layers.map((layer) => (
                <option key={layer} value={layer}>
                  {layer}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="panel-body">
        {steps.size >= 2 ? (
          <LineChart
            series={series}
            height={300}
            format={exp}
            label={`Gradient ${VIEW_LABEL[activeMode]} by step for ${series.map((s) => s.name).join(', ')}`}
          />
        ) : (
          <p className="muted gv-note">Logged at fewer than two steps, so there is no curve to draw.</p>
        )}
      </div>

      <div className="table-wrap gv-table">
        <table className="table">
          <thead>
            <tr>
              <th>Layer — gradient {VIEW_LABEL[activeMode].toLowerCase()}</th>
              <th className="r">Mean</th>
              <th className="r">Max</th>
              <th className="r">Min</th>
            </tr>
          </thead>
          <tbody>
            {layers.map((layer) => {
              const values = gradients
                .filter((g) => g.layerName === layer)
                .map(getGradientValue)
                .filter((v): v is number => v !== undefined);
              if (values.length === 0) return null;
              const mean = values.reduce((sum, v) => sum + v, 0) / values.length;
              return (
                <tr key={layer}>
                  <td className="mono">{layer}</td>
                  <td className="r mono">{exp(mean)}</td>
                  <td className="r mono">{exp(Math.max(...values))}</td>
                  <td className="r mono">{exp(Math.min(...values))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
};

export default GradientVisualization;
