import { useEffect, useState } from "react";
import { apiFetch } from "../../../lib/api";
import type { ExperimentRun } from "../../../components/Experiment/experiment";

/**
 * How many runs the charts fetch curves for, newest first: one request per run, so an
 * experiment with hundreds of runs would otherwise fire hundreds on every visit.
 */
export const CURVE_LIMIT = 12;

interface Series {
  name: string;
  data: { step: number | null; value: unknown }[];
}

export interface ChartRun {
  id: string;
  name: string;
  /** Colour slot, fixed by start order so a run keeps its colour across every chart. */
  index: number;
  series: { name: string; pts: { x: number; y: number }[] }[];
  params: Record<string, unknown>;
}

/**
 * Curves for the newest runs plus their parameters, from the routes the run page already uses:
 * `/api/run/:id/metrics` per run and the experiment's comparison rows. Null while loading.
 */
export function useChartRuns(experimentId: string, runs: ExperimentRun[]): ChartRun[] | null {
  const [result, setResult] = useState<ChartRun[] | null>(null);
  const key = runs.map((r) => r._id).join("\u0000");

  useEffect(() => {
    let cancelled = false;
    setResult(null);
    const picked = [...runs]
      .sort((a, b) => (a.startedAt ?? "").localeCompare(b.startedAt ?? ""))
      .slice(-CURVE_LIMIT);
    const rows = apiFetch(`/api/experiment/${experimentId}/runs`)
      .then((res) => (res.ok ? res.json() : []))
      .catch(() => []);
    Promise.all([
      rows,
      ...picked.map((run) =>
        apiFetch(`/api/run/${run._id}/metrics`)
          .then((res) => (res.ok ? res.json() : []))
          .catch(() => []),
      ),
    ]).then(([body, ...curves]) => {
      if (cancelled) return;
      const params = new Map<string, Record<string, unknown>>(
        (Array.isArray(body) ? body : []).map((row: { _id: string; parameters?: Record<string, unknown> }) => [row._id, row.parameters ?? {}]),
      );
      setResult(
        picked.map((run, index) => ({
          id: run._id,
          name: run.name,
          index,
          params: params.get(run._id) ?? {},
          series: (Array.isArray(curves[index]) ? (curves[index] as Series[]) : []).map((s) => ({
            name: s.name,
            pts: (Array.isArray(s.data) ? s.data : [])
              .map((d) => ({ x: d.step, y: Number(d.value) }))
              .filter((p): p is { x: number; y: number } => typeof p.x === "number" && Number.isFinite(p.y)),
          })),
        })),
      );
    });
    return () => {
      cancelled = true;
    };
    // `key` stands for `runs`: the array is rebuilt on every render of the page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentId, key]);

  return result;
}
