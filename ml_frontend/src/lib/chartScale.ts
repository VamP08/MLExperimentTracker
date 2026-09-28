/** Number formatting and axis scaling shared by all charts. */

export function fmt(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs !== 0 && (abs < 0.001 || abs >= 1e6)) return value.toExponential(2);
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(abs < 1 ? 4 : abs < 100 ? 3 : 1);
}

/** Round-number axis ticks across [lo, hi], and the spacing they were chosen at. */
export function ticks(lo: number, hi: number, count = 4): { values: number[]; step: number } {
  if (lo === hi) return { values: [lo], step: 1 };
  const raw = (hi - lo) / (count - 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const values: number[] = [];
  for (let t = Math.ceil(lo / step - 1e-9) * step; t <= hi + step * 1e-9; t += step) values.push(Number(t.toPrecision(10)));
  return { values, step };
}

/** A value axis snapped outward to round numbers, so the ticks sit on its ends. */
export function niceAxis(min: number, max: number): { lo: number; hi: number; values: number[]; step: number } {
  const pad = (max - min) * 0.06 || Math.abs(max) * 0.05 || 1;
  const { step } = ticks(min - pad, max + pad, 5);
  // A series that never goes negative gets no negative axis.
  const lo = Math.max(min >= 0 ? 0 : -Infinity, Math.floor((min - pad) / step + 1e-9) * step);
  const hi = Math.ceil((max + pad) / step - 1e-9) * step;
  const values: number[] = [];
  for (let i = 0; lo + i * step <= hi + step * 1e-9; i++) values.push(Number((lo + i * step).toPrecision(10)));
  return { lo, hi, step, values };
}

/** A tick label with only the decimals its spacing needs: 0.7, not 0.7000. */
export function tickLabel(value: number, step: number): string {
  if (Math.abs(value) >= 1e5 || (value !== 0 && Math.abs(value) < 1e-4)) return value.toExponential(1);
  const decimals = Math.max(0, Math.ceil(-Math.log10(step) - 1e-9) + (String(step).includes("25") ? 1 : 0));
  return value.toFixed(decimals);
}

/** The five series colours. */
export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)"];
export const seriesColor = (index: number): string => SERIES[index % SERIES.length];
