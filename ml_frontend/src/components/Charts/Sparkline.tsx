interface SparklineProps {
  values: number[];
  /** CSS colour, one of the series tokens. */
  color: string;
  label: string;
}

const W = 240;
const H = 44;

/** Training curve sized for a list row. No axes, just the shape. */
const Sparkline = ({ values, color, label }: SparklineProps) => {
  if (values.length < 2) return <span className="spark-empty">No curve</span>;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const path = values
    .map((v, i) => `${i ? "L" : "M"}${((i / (values.length - 1)) * (W - 4) + 2).toFixed(1)},${(H - 4 - ((v - lo) / (hi - lo || 1)) * (H - 8)).toFixed(1)}`)
    .join("");
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <path d={path} fill="none" stroke={color} strokeWidth={1.8} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
};

export default Sparkline;
