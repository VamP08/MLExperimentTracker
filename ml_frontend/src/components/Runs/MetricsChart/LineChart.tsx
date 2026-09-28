import { useEffect, useId, useRef, useState } from "react";
import type { PointerEvent } from "react";
import { fmt, tickLabel, ticks } from "../../../lib/chartScale";
import "./MetricsChart.css";

export interface LinePoint {
  x: number;
  y: number;
}

export interface LineSeries {
  name: string;
  /** Position in the run's series list; picks one of the five spectral colours. */
  index: number;
  pts: LinePoint[];
}

interface LineChartProps {
  series: LineSeries[];
  /** Accessible description of what the chart plots. */
  label: string;
  height?: number;
  format?: (value: number) => string;
  /** Series drawn on top at full strength while the rest recede. */
  highlight?: string;
  /**
   * Read each series at its last point at or before the hovered step, rather than only at an
   * exact match. For series logged on different step grids, such as runs with different batch sizes.
   */
  carry?: boolean;
}

const PAD = { l: 60, r: 16, t: 12, b: 28 };

/**
 * Step-indexed line chart, drawn at the container's real pixel width so axis text stays
 * legible from a phone to a wide monitor. Hovering pins a crosshair to the nearest logged
 * step and reads every series at it; otherwise the readout shows each series' last point.
 */
const LineChart = ({ series, label, height = 320, format = fmt, highlight, carry = false }: LineChartProps) => {
  const wrap = useRef<HTMLDivElement>(null);
  const clip = useId();
  const [width, setWidth] = useState(720);
  const [hoverX, setHoverX] = useState<number | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(240, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const drawn = series.filter((s) => s.pts.length > 0);
  const xs = Array.from(new Set(drawn.flatMap((s) => s.pts.map((p) => p.x)))).sort((a, b) => a - b);
  const ys = drawn.flatMap((s) => s.pts.map((p) => p.y));
  if (xs.length === 0) return null;

  const x0 = xs[0];
  const x1 = xs[xs.length - 1] === x0 ? x0 + 1 : xs[xs.length - 1];
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const pad = (max - min) * 0.08 || Math.abs(max) * 0.05 || 1;
  const yAxis = ticks(min - pad, max + pad, 5);
  const lo = Math.min(min - pad, yAxis.values[0]);
  const hi = Math.max(max + pad, yAxis.values[yAxis.values.length - 1]);
  const sx = (x: number) => PAD.l + ((x - x0) / (x1 - x0)) * (width - PAD.l - PAD.r);
  const sy = (y: number) => PAD.t + (1 - (y - lo) / (hi - lo)) * (height - PAD.t - PAD.b);
  const xRound = ticks(x0, x1, Math.max(2, Math.floor(width / 110))).values.filter(
    (t) => t > x0 && t <= x1 && (t - x0) / (x1 - x0) > 0.06,
  );
  const xTicks = [x0, ...xRound];

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
    const target = x0 + ((px - PAD.l) / (width - PAD.l - PAD.r)) * (x1 - x0);
    let best = xs[0];
    for (const x of xs) if (Math.abs(x - target) < Math.abs(best - target)) best = x;
    setHoverX(best);
  };

  const readX = hoverX ?? xs[xs.length - 1];
  const readout = drawn.map((s) => {
    const at =
      hoverX === null
        ? s.pts[s.pts.length - 1]
        : carry
          ? s.pts.filter((p) => p.x <= hoverX).pop()
          : s.pts.find((p) => p.x === hoverX);
    return { s, at };
  });

  return (
    <div className="lc" ref={wrap}>
      <div className="lc-readout num" aria-hidden="true">
        <span className="lc-at">
          {hoverX === null ? `latest · step ${readX}` : `step ${readX}`}
        </span>
        {readout.map(({ s, at }) => (
          <span key={s.name} className={`lc-item lc-s${(s.index % 5) + 1}`}>
            <i className="lc-swatch" />
            {s.name}
            <b className="mono">{at ? format(at.y) : "—"}</b>
          </span>
        ))}
      </div>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={label}
        onPointerMove={onMove}
        onPointerLeave={() => setHoverX(null)}
      >
        <defs>
          <clipPath id={clip}>
            <rect x={PAD.l} y={0} width={width - PAD.l} height={height - PAD.b + 1} />
          </clipPath>
        </defs>
        {yAxis.values.map((t) => (
          <g key={`y${t}`}>
            <line className="lc-grid" x1={PAD.l} x2={width - PAD.r} y1={sy(t)} y2={sy(t)} />
            <text className="lc-tick" x={PAD.l - 8} y={sy(t) + 3.5} textAnchor="end">
              {tickLabel(t, yAxis.step)}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={`x${t}`} className="lc-tick" x={sx(t)} y={height - 8} textAnchor="middle">
            {t}
          </text>
        ))}
        {[...drawn].sort((a, b) => Number(a.name === highlight) - Number(b.name === highlight)).map((s) => (
          <g
            key={s.name}
            className={`lc-s${(s.index % 5) + 1}${highlight === undefined ? "" : s.name === highlight ? " lc-hi" : " lc-dim"}`}
            clipPath={`url(#${clip})`}
          >
            {s.pts.length > 1 ? (
              <path
                className="lc-line"
                d={s.pts.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join("")}
              />
            ) : (
              <circle className="lc-dot" cx={sx(s.pts[0].x)} cy={sy(s.pts[0].y)} r={3} />
            )}
          </g>
        ))}
        {hoverX !== null && (
          <g>
            <line className="lc-cross" x1={sx(hoverX)} x2={sx(hoverX)} y1={PAD.t} y2={height - PAD.b} />
            {readout.map(({ s, at }) =>
              at ? <circle key={s.name} className={`lc-dot lc-s${(s.index % 5) + 1}`} cx={sx(at.x)} cy={sy(at.y)} r={3.5} /> : null,
            )}
          </g>
        )}
      </svg>
    </div>
  );
};

export default LineChart;
