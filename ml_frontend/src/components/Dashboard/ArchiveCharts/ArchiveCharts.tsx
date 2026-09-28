import type { DashboardExperiment } from "../../../lib/dashboard";
import { niceAxis, seriesColor } from "../../../lib/chartScale";
import "./ArchiveCharts.css";

interface ArchiveChartsProps {
  experiments: DashboardExperiment[];
  /** Each experiment's position in the full list, so its colour survives filtering. */
  colorIndex: Map<string, number>;
  /** Run durations in seconds by experiment id, from the comparison endpoint; absent while loading. */
  durations: Map<string, number[]> | null;
  uiStatus: (state: string | undefined) => string;
}

const OUTCOMES = [
  { key: "completed", label: "Completed", color: "var(--ok)" },
  { key: "failed", label: "Failed", color: "var(--fail)" },
  { key: "running", label: "Running", color: "var(--accent)" },
  { key: "archived", label: "Interrupted", color: "var(--unk)" },
] as const;

function dayKey(iso: string): string | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The archive at a glance: when runs happened and how they ended, who succeeds, how long runs take. */
const ArchiveCharts = ({ experiments, colorIndex, durations, uiStatus }: ArchiveChartsProps) => {
  // Runs per day, stacked by outcome.
  const days = new Map<string, Record<string, number>>();
  for (const exp of experiments) {
    for (const run of exp.runs) {
      const key = dayKey(run.startedAt);
      if (!key) continue;
      const bucket = days.get(key) ?? { completed: 0, failed: 0, running: 0, archived: 0 };
      bucket[uiStatus(run.status)] += 1;
      days.set(key, bucket);
    }
  }
  const dayKeys = [...days.keys()].sort().slice(-14);
  const peak = Math.max(1, ...dayKeys.map((k) => Object.values(days.get(k)!).reduce((a, b) => a + b, 0)));
  const W = 360;
  const H = 150;
  const step = dayKeys.length ? (W - 28) / dayKeys.length : 0;
  const yTicks = peak <= 4 ? Array.from({ length: peak + 1 }, (_, i) => i) : [0, Math.round(peak / 2), peak];
  const present = OUTCOMES.filter((o) => dayKeys.some((k) => days.get(k)![o.key] > 0));

  // Durations: one lane per experiment, a dot per run.
  const allDur = durations ? experiments.flatMap((e) => durations.get(e._id) ?? []) : [];
  const maxDur = Math.max(1, ...allDur);
  const inMinutes = maxDur >= 120;
  const unit = inMinutes ? 60 : 1;
  // Round numbers on the axis: 0 · 5 · 10, not a raw midpoint of the longest run.
  const durAxis = niceAxis(0, maxDur / unit);
  const durX = (sec: number) => 14 + (sec / unit / durAxis.hi) * 272;
  const lane = experiments.length ? 130 / experiments.length : 0;

  return (
    <div className="archive-charts">
      <section className="panel" aria-labelledby="ac-time">
        <div className="panel-head">
          <h2 id="ac-time">Runs over time</h2>
          <span className="sub">by day and outcome</span>
        </div>
        <div className="ac-body">
          {dayKeys.length === 0 ? (
            <p className="ac-empty">No run start times recorded.</p>
          ) : (
            <svg viewBox={`0 0 ${W} ${H + 18}`} role="img" aria-label={`Runs per day over ${dayKeys.length} days`}>
              {yTicks.map((t) => {
                const y = H - (t / peak) * (H - 8);
                return (
                  <g key={t}>
                    <line className="ac-grid" x1={22} x2={W} y1={y} y2={y} />
                    <text className="ac-tick" x={16} y={y + 3} textAnchor="end">
                      {t}
                    </text>
                  </g>
                );
              })}
              {dayKeys.map((k, i) => {
                let y = H;
                const b = days.get(k)!;
                return (
                  <g key={k}>
                    {OUTCOMES.map((o) => {
                      const h = (b[o.key] / peak) * (H - 8);
                      if (!h) return null;
                      y -= h;
                      return <rect key={o.key} x={28 + i * step + step * 0.18} y={y} width={step * 0.64} height={Math.max(h - 1, 1)} rx={2} fill={o.color} />;
                    })}
                    {(dayKeys.length <= 10 || i % Math.ceil(dayKeys.length / 7) === 0) && (
                      <text className="ac-tick" x={28 + i * step + step / 2} y={H + 13} textAnchor="middle">
                        {k.slice(5)}
                      </text>
                    )}
                  </g>
                );
              })}
            </svg>
          )}
          <div className="ac-legend">
            {present.map((o) => (
              <span key={o.key}>
                <i style={{ background: o.color }} />
                {o.label}
              </span>
            ))}
          </div>
        </div>
      </section>

      <section className="panel" aria-labelledby="ac-success">
        <div className="panel-head">
          <h2 id="ac-success">Success rate</h2>
          <span className="sub">completed runs</span>
        </div>
        <div className="ac-body ac-bars">
          {experiments.map((exp) => {
            const pct = Number.parseInt(exp.stats.successRate, 10) || 0;
            return (
              <div key={exp._id} className="ac-bar">
                <div className="ac-bar-label">
                  <span>{exp.name}</span>
                  <b className="num">{pct}%</b>
                </div>
                <div className="ac-track" role="img" aria-label={`${exp.name}: ${pct}% of runs completed`}>
                  <div style={{ width: `${pct}%`, background: seriesColor(colorIndex.get(exp._id) ?? 0) }} />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="panel" aria-labelledby="ac-duration">
        <div className="panel-head">
          <h2 id="ac-duration">Run duration</h2>
          <span className="sub">{inMinutes ? "minutes" : "seconds"}, a dot per run</span>
        </div>
        <div className="ac-body">
          {durations === null ? (
            <p className="ac-empty">Loading run durations…</p>
          ) : (
            <svg viewBox="0 0 300 160" role="img" aria-label="Run durations by experiment">
              {durAxis.values.map((t) => (
                <g key={t}>
                  <line className="ac-grid" x1={durX(t * unit)} x2={durX(t * unit)} y1={0} y2={134} />
                  <text className="ac-tick" x={durX(t * unit)} y={150} textAnchor="middle">
                    {t}
                  </text>
                </g>
              ))}
              {experiments.map((exp, i) =>
                (durations.get(exp._id) ?? []).map((sec, j) => (
                  <circle
                    key={`${exp._id}-${j}`}
                    cx={durX(sec)}
                    cy={lane * i + lane / 2 + ((j % 3) - 1) * 5}
                    r={4.5}
                    fill={seriesColor(colorIndex.get(exp._id) ?? 0)}
                    fillOpacity={0.85}
                  />
                )),
              )}
            </svg>
          )}
          <div className="ac-legend">
            {experiments.map((exp) => (
              <span key={exp._id}>
                <i className="round" style={{ background: seriesColor(colorIndex.get(exp._id) ?? 0) }} />
                {exp.name}
              </span>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
};

export default ArchiveCharts;
