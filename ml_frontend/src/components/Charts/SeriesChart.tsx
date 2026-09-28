import { useId } from "react";
import { fmt, niceAxis, tickLabel, ticks } from "../../lib/chartScale";
import "./SeriesChart.css";

export interface SeriesPoint {
  step: number | null;
  value: unknown;
  timestamp?: number | null;
}

interface SeriesChartProps {
  name: string;
  data: SeriesPoint[];
  /** Index in the run's series list; picks the series colour. */
  index: number;
}

const W = 240;
const H = 110;
const PAD = { l: 34, r: 6, t: 6, b: 18 };

/**
 * One metric series as a small line chart: latest value, curve and extremes.
 * Non-numeric points are skipped, since metric values aren't type-checked.
 */
const SeriesChart = ({ name, data, index }: SeriesChartProps) => {
  const clip = useId();
  const pts = data
    .map((d) => ({ step: typeof d.step === "number" ? d.step : null, value: Number(d.value), t: d.timestamp }))
    .filter((d): d is { step: number; value: number; t: number | null | undefined } => d.step !== null && Number.isFinite(d.value));
  const slot = (index % 5) + 1;

  if (pts.length === 0) {
    return (
      <div className={`series series-${slot}`}>
        <div className="series-head">
          <span className="series-name">{name}</span>
        </div>
        <p className="series-empty">No numeric values logged.</p>
      </div>
    );
  }

  const last = pts[pts.length - 1];
  const values = pts.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const minPt = pts[values.indexOf(min)];
  const maxPt = pts[values.indexOf(max)];
  const x0 = pts[0].step;
  const x1 = last.step === x0 ? x0 + 1 : last.step;
  const yAxis = niceAxis(min, max);
  const yTicks = yAxis.values;
  const { lo, hi } = yAxis;
  const sx = (s: number) => PAD.l + ((s - x0) / (x1 - x0)) * (W - PAD.l - PAD.r);
  const sy = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  // Always label where the series starts, then round steps after it.
  const xRound = ticks(x0, x1, 4).values.filter((t) => t > x0 && t <= x1 && (t - x0) / (x1 - x0) > 0.12);
  const xTicks = [x0, ...xRound];
  const path = pts.map((p, i) => `${i ? "L" : "M"}${sx(p.step).toFixed(1)},${sy(p.value).toFixed(1)}`).join("");

  return (
    <figure className={`series series-${slot}`}>
      <figcaption className="series-head">
        <span className="series-name">{name}</span>
        <span className="series-value num">{fmt(last.value)}</span>
      </figcaption>
      <div className="series-at num">
        latest · step {last.step}
        {typeof last.t === "number" ? ` · +${last.t.toFixed(3)} s` : ""}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${name}: ${pts.length} points, latest ${fmt(last.value)}, min ${fmt(min)}, max ${fmt(max)}`}>
        <defs>
          <clipPath id={clip}>
            <rect x={PAD.l} y={0} width={W - PAD.l} height={H - PAD.b + 1} />
          </clipPath>
        </defs>
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line className="series-grid" x1={PAD.l} x2={W - PAD.r} y1={sy(t)} y2={sy(t)} />
            <text className="series-tick" x={PAD.l - 6} y={sy(t) + 3} textAnchor="end">
              {tickLabel(t, yAxis.step)}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={`x${t}`} className="series-tick" x={sx(t)} y={H - 4} textAnchor="middle">
            {t}
          </text>
        ))}
        {pts.length > 1 && <path className="series-line" d={path} clipPath={`url(#${clip})`} />}
        <circle className="series-dot" cx={sx(last.step)} cy={sy(last.value)} r={2.6} />
      </svg>
      <div className="series-foot num">
        <span title={`step ${minPt.step}`}>
          min <b>{fmt(min)}</b>
        </span>
        <span title={`step ${maxPt.step}`}>
          max <b>{fmt(max)}</b>
        </span>
      </div>
    </figure>
  );
};

export default SeriesChart;
