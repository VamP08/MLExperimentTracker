import "./StatsOverview.css";

interface StatItem {
  title: string;
  value: string;
}

interface StatsOverviewProps {
  stats: StatItem[];
}

/** A compact row of label/value facts, read left to right like a sentence. */
const StatsOverview = ({ stats }: StatsOverviewProps) => {
  return (
    <dl className="stats-overview">
      {stats.map((stat) => (
        <div key={stat.title} className="stats-item">
          <dt>{stat.title}</dt>
          <dd className="num">{stat.value}</dd>
        </div>
      ))}
    </dl>
  );
};

export default StatsOverview;
