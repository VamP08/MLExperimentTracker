import { useState } from "react";
import { Link } from "react-router-dom";
import LineChart from "../../../components/Runs/MetricsChart/LineChart";
import { fmt, niceAxis, seriesColor, tickLabel } from "../../../lib/chartScale";
import type { ChartRun } from "./chartRuns";
import "./ExperimentCharts.css";

export interface Ranked {
  run: ChartRun;
  pts: { x: number; y: number }[];
  final: number;
}

interface MetricProps {
  metric: string;
  better: "max" | "min";
  ranked: Ranked[];
}

/** Every run's curve for one metric on shared axes, the leader drawn on top. */
export const CurvesPanel = ({
  metric,
  metrics,
  onMetric,
  ranked,
  better,
}: MetricProps & { metrics: string[]; onMetric: (name: string) => void }) => {
  const best = ranked[0];
  return (
    <section className="panel" aria-labelledby="ec-curves">
      <div className="panel-head">
        <h2 id="ec-curves">Training curves</h2>
        <span className="sub">{ranked.length === 1 ? "1 run" : `${ranked.length} runs`}, by step</span>
        <span className="spacer" />
        {metrics.length > 1 && (
          <div className="ec-metrics" role="group" aria-label="Metric">
            {metrics.map((m) => (
              <button key={m} type="button" className="ec-metric" aria-pressed={m === metric} onClick={() => onMetric(m)}>
                {m}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="panel-body">
        {ranked.length === 0 ? (
          <p className="ec-empty">No run logged {metric} at a step.</p>
        ) : (
          <>
            <p className="ec-lead">
              <span className="ec-swatch" style={{ background: seriesColor(best.run.index) }} />
              <Link to={`/runs/${encodeURIComponent(best.run.id)}`}>{best.run.name}</Link> leads on {metric} at{" "}
              <b className="num">{fmt(best.final)}</b>
              <span className="muted"> · {better === "max" ? "higher" : "lower"} is better</span>
            </p>
            <LineChart
              series={ranked.map((r) => ({ name: r.run.name, index: r.run.index, pts: r.pts }))}
              highlight={best.run.name}
              carry
              height={300}
              label={`${metric} for ${ranked.length} runs; ${best.run.name} leads at ${fmt(best.final)}`}
            />
          </>
        )}
      </div>
    </section>
  );
};

/** Runs ordered by where they finished on the chosen metric. */
export const Leaderboard = ({ metric, better, ranked }: MetricProps) => {
  const finals = ranked.map((r) => r.final);
  const axis = niceAxis(Math.min(...finals), Math.max(...finals));
  const span = axis.hi - axis.lo || 1;
  return (
    <section className="panel" aria-labelledby="ec-board">
      <div className="panel-head">
        <h2 id="ec-board">Leaderboard</h2>
        <span className="sub">
          final {metric}, {better === "max" ? "highest" : "lowest"} first
        </span>
      </div>
      {ranked.length === 0 ? (
        <div className="panel-body">
          <p className="ec-empty">Nothing to rank yet.</p>
        </div>
      ) : (
        <ol className="ec-board">
          {ranked.map((r, i) => (
            <li key={r.run.id} className={i === 0 ? "is-best" : undefined}>
              <span className="ec-rank num">{i + 1}</span>
              <div className="ec-board-main">
                <div className="ec-board-label">
                  <Link to={`/runs/${encodeURIComponent(r.run.id)}`}>{r.run.name}</Link>
                  <b className="num">{fmt(r.final)}</b>
                </div>
                <div className="ec-track" aria-hidden="true">
                  <div style={{ width: `${Math.max(3, ((r.final - axis.lo) / span) * 100)}%`, background: seriesColor(r.run.index) }} />
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
};

/** Numeric parameters that take at least two values across the charted runs. */
function varyingParams(runs: ChartRun[]): string[] {
  const keys = new Set(runs.flatMap((r) => Object.keys(r.params)));
  return [...keys]
    .filter((k) => new Set(runs.map((r) => r.params[k]).filter((v) => typeof v === "number")).size > 1)
    .sort((a, b) => (a === "learningRate" ? -1 : b === "learningRate" ? 1 : a.localeCompare(b)));
}

const SW = 640;
const SH = 250;
const SP = { l: 46, r: 14, t: 10, b: 34 };

/** One parameter against the final metric: a dot per run, to see whether the knob mattered. */
export const ParamScatter = ({ metric, ranked }: MetricProps) => {
  const runs = ranked.map((r) => r.run);
  const params = varyingParams(runs);
  const [chosen, setChosen] = useState<string | null>(null);
  const param = chosen && params.includes(chosen) ? chosen : params[0];

  const pts = param
    ? ranked
        .map((r) => ({ r, x: r.run.params[param] }))
        .filter((p): p is { r: Ranked; x: number } => typeof p.x === "number" && Number.isFinite(p.x))
    : [];
  const xs = pts.map((p) => p.x);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  // Learning rates and the like span decades, so switch to a log axis.
  const log = xMin > 0 && xMax / xMin >= 20;
  const tx = (v: number) => (log ? Math.log10(v) : v);
  const xAxis = log
    ? { lo: Math.floor(Math.log10(xMin)), hi: Math.ceil(Math.log10(xMax)) }
    : niceAxis(xMin, xMax);
  const xTicks = log
    ? Array.from({ length: xAxis.hi - xAxis.lo + 1 }, (_, i) => xAxis.lo + i)
    : (xAxis as ReturnType<typeof niceAxis>).values;
  const xSpan = xAxis.hi - xAxis.lo || 1;
  const yAxis = niceAxis(Math.min(...pts.map((p) => p.r.final)), Math.max(...pts.map((p) => p.r.final)));
  const sx = (v: number) => SP.l + ((tx(v) - xAxis.lo) / xSpan) * (SW - SP.l - SP.r);
  const sxTick = (t: number) => SP.l + ((t - xAxis.lo) / xSpan) * (SW - SP.l - SP.r);
  const sy = (v: number) => SP.t + (1 - (v - yAxis.lo) / (yAxis.hi - yAxis.lo || 1)) * (SH - SP.t - SP.b);
  const bestId = ranked[0]?.run.id;
  // Small values use one notation across the axis: 0, 2e-5, 4e-5.
  const xStep = log ? 1 : (xAxis as ReturnType<typeof niceAxis>).step;
  const xLabel = (t: number) => (t === 0 ? "0" : xStep < 1e-3 ? Number(t.toPrecision(3)).toExponential() : tickLabel(t, xStep));

  return (
    <section className="panel" aria-labelledby="ec-scatter">
      <div className="panel-head">
        <h2 id="ec-scatter">Parameter vs result</h2>
        <span className="sub">final {metric}, a dot per run</span>
        <span className="spacer" />
        {params.length > 1 && (
          <label>
            <span className="sr-only">Parameter</span>
            <select className="select" value={param} onChange={(e) => setChosen(e.target.value)}>
              {params.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="panel-body">
        {!param || pts.length < 2 ? (
          <p className="ec-empty">No numeric parameter varies across these runs.</p>
        ) : (
          <svg className="ec-scatter" viewBox={`0 0 ${SW} ${SH}`} role="img" aria-label={`${metric} against ${param} for ${pts.length} runs`}>
            {yAxis.values.map((t) => (
              <g key={`y${t}`}>
                <line className="ec-grid" x1={SP.l} x2={SW - SP.r} y1={sy(t)} y2={sy(t)} />
                <text className="ec-tick" x={SP.l - 6} y={sy(t) + 3} textAnchor="end">
                  {tickLabel(t, yAxis.step)}
                </text>
              </g>
            ))}
            {xTicks.map((t) => (
              <text key={`x${t}`} className="ec-tick" x={sxTick(t)} y={SH - 12} textAnchor="middle">
                {log ? `1e${t}` : xLabel(t)}
              </text>
            ))}
            <text className="ec-axis" x={SW - SP.r} y={SH - 1} textAnchor="end">
              {param}
              {log ? " (log)" : ""}
            </text>
            {pts.map(({ r, x }) => (
              <circle
                key={r.run.id}
                className={r.run.id === bestId ? "ec-dot is-best" : "ec-dot"}
                cx={sx(x)}
                cy={sy(r.final)}
                r={r.run.id === bestId ? 7 : 5.5}
                fill={seriesColor(r.run.index)}
              >
                <title>{`${r.run.name}: ${param} ${x}, ${metric} ${fmt(r.final)}`}</title>
              </circle>
            ))}
          </svg>
        )}
      </div>
    </section>
  );
};
