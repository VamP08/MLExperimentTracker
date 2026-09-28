import React, { useEffect, useState, useCallback } from 'react';
import './ROCCurve.css';
import { apiFetch } from '../../../lib/api';

interface ROCData {
  fpr: number[];
  tpr: number[];
  thresholds: number[];
  auc: number;
  className?: string;
}

interface Props {
  runId: string;
}

const ROCCurve: React.FC<Props> = ({ runId }) => {
  const [curves, setCurves] = useState<ROCData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCurve, setSelectedCurve] = useState<number>(0);

  const isCurve = (value: unknown): value is ROCData => {
    const candidate = value as ROCData | null;
    return (
      !!candidate &&
      Array.isArray(candidate.fpr) &&
      Array.isArray(candidate.tpr) &&
      Array.isArray(candidate.thresholds) &&
      typeof candidate.auc === 'number' &&
      candidate.fpr.length > 0
    );
  };

  const fetchROCData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      // GAPS B9: the mounted prefix is singular. `/api/runs/...` was never a route.
      const response = await apiFetch(`/api/run/${runId}/artifacts`);

      if (!response.ok) {
        throw new Error(`Failed to fetch artifacts: ${response.statusText}`);
      }

      const artifacts = await response.json();
      const records: Array<{ type?: string; metadata?: unknown }> = Array.isArray(artifacts)
        ? artifacts
        : [];

      // One artifact line per class; `className` labels each curve.
      // A run with no ROC artifact is the ordinary case, not a failure.
      setCurves(
        records
          .filter((art) => art.type === 'roc_curve' || art.type === 'roc_auc')
          .map((art) => art.metadata)
          .filter(isCurve)
      );
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    fetchROCData();
  }, [fetchROCData]);

  const shell = (body: React.ReactNode, sub?: React.ReactNode) => (
    <section className="panel" aria-labelledby="roc-head">
      <div className="panel-head">
        <h2 id="roc-head">ROC curve</h2>
        {sub}
      </div>
      {body}
    </section>
  );

  if (loading) {
    return shell(<div className="state">Loading ROC curve data…</div>);
  }

  if (error) {
    return shell(<div className="state error">Could not load ROC curves: {error}</div>);
  }

  // Nothing to draw is not an error and not worth a card of its own; the
  // Evaluation tab says once, quietly, when a run logged no evaluation artifacts.
  if (curves.length === 0) {
    return null;
  }

  const S = 400;
  const PAD = { l: 44, r: 12, t: 12, b: 40 };
  const sx = (v: number) => PAD.l + v * (S - PAD.l - PAD.r);
  const sy = (v: number) => S - PAD.b - v * (S - PAD.t - PAD.b);
  const grid = [0, 0.2, 0.4, 0.6, 0.8, 1];
  const label = (curve: ROCData, idx: number) => curve.className || `Class ${idx + 1}`;

  // The threshold that maximises TPR - FPR (Youden's J).
  const optimalThreshold = (curve: ROCData) => {
    let maxDiff = -Infinity;
    let optimalIdx = 0;
    curve.tpr.forEach((tpr, i) => {
      const diff = tpr - curve.fpr[i];
      if (diff > maxDiff) {
        maxDiff = diff;
        optimalIdx = i;
      }
    });
    const threshold = curve.thresholds[optimalIdx];
    return typeof threshold === 'number' ? threshold.toFixed(3) : '—';
  };

  return shell(
    <div className="panel-body roc-layout">
      <svg viewBox={`0 0 ${S} ${S}`} className="roc-svg" role="img" aria-label={`ROC curves: ${curves.map((c, i) => `${label(c, i)} AUC ${c.auc.toFixed(3)}`).join(', ')}`}>
        {grid.map((v) => (
          <g key={v}>
            <line className="roc-grid" x1={sx(0)} x2={sx(1)} y1={sy(v)} y2={sy(v)} />
            <line className="roc-grid" x1={sx(v)} x2={sx(v)} y1={sy(0)} y2={sy(1)} />
            <text className="roc-tick" x={sx(v)} y={sy(0) + 16} textAnchor="middle">
              {v.toFixed(1)}
            </text>
            <text className="roc-tick" x={sx(0) - 8} y={sy(v) + 4} textAnchor="end">
              {v.toFixed(1)}
            </text>
          </g>
        ))}
        <line className="roc-chance" x1={sx(0)} y1={sy(0)} x2={sx(1)} y2={sy(1)} />
        {/* The selected class is drawn last so it sits above the others. */}
        {curves
          .map((curve, idx) => ({ curve, idx }))
          .sort((a, b) => Number(a.idx === selectedCurve) - Number(b.idx === selectedCurve))
          .map(({ curve, idx }) => (
          <path
            key={idx}
            className={`roc-line roc-s${(idx % 5) + 1}${idx === selectedCurve ? ' is-selected' : ''}`}
            d={curve.fpr.map((f, i) => `${i ? 'L' : 'M'}${sx(f).toFixed(1)},${sy(curve.tpr[i] ?? 0).toFixed(1)}`).join('')}
          />
        ))}
        <text className="roc-axis" x={(sx(0) + sx(1)) / 2} y={S - 6} textAnchor="middle">
          False positive rate
        </text>
        <text className="roc-axis" x={12} y={(sy(0) + sy(1)) / 2} textAnchor="middle" transform={`rotate(-90 12 ${(sy(0) + sy(1)) / 2})`}>
          True positive rate
        </text>
      </svg>

      <div className="table-wrap roc-legend">
        <table className="table">
          <thead>
            <tr>
              <th>Class</th>
              <th className="r">AUC</th>
              <th className="r">Optimal threshold</th>
              <th className="r">Points</th>
            </tr>
          </thead>
          <tbody>
            {curves.map((curve, idx) => (
              <tr key={idx} className={idx === selectedCurve ? 'is-selected' : undefined}>
                <td>
                  <button
                    type="button"
                    className={`roc-key roc-s${(idx % 5) + 1}`}
                    aria-pressed={idx === selectedCurve}
                    onClick={() => setSelectedCurve(idx)}
                  >
                    <i className="roc-swatch" aria-hidden="true" />
                    {label(curve, idx)}
                  </button>
                </td>
                <td className="r mono">{curve.auc.toFixed(4)}</td>
                <td className="r mono">{optimalThreshold(curve)}</td>
                <td className="r mono">{curve.fpr.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="roc-note">
          Dashed diagonal: a random classifier. Optimal threshold maximises TPR − FPR.
          {curves.length > 1 && ' Select a class to bring its curve forward.'}
        </p>
      </div>
    </div>,
    <span className="sub num">
      {curves.length} {curves.length === 1 ? 'class' : 'classes'}
    </span>,
  );
};

export default ROCCurve;
