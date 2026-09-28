import { Link } from "react-router-dom";
import type { DashboardExperiment } from "../../../lib/dashboard";
import { seriesColor } from "../../../lib/chartScale";
import "./ExperimentList.css";

interface ExperimentListProps {
  experiments: DashboardExperiment[];
  colorIndex: Map<string, number>;
}

/** Every experiment with what it is and how it is going, in its chart colour. */
const ExperimentList = ({ experiments, colorIndex }: ExperimentListProps) => (
  <ul className="exp-list">
    {experiments.map((exp) => {
      const pct = Number.parseInt(exp.stats.successRate, 10) || 0;
      const color = seriesColor(colorIndex.get(exp._id) ?? 0);
      return (
        <li key={exp._id}>
          <div className="exp-list-head">
            <Link to={`/experiment/${encodeURIComponent(exp._id)}`}>{exp.name}</Link>
            <span className="num">
              {exp.stats.totalRuns} {exp.stats.totalRuns === 1 ? "run" : "runs"}
            </span>
          </div>
          {exp.description && <p className="exp-list-desc">{exp.description}</p>}
          <div className="exp-list-track" role="img" aria-label={`${pct}% of runs completed`}>
            <div style={{ width: `${pct}%`, background: color }} />
          </div>
          <div className="exp-list-foot num">
            <span>{exp.stats.successRate} succeeded</span>
            <span>avg {exp.stats.avgDuration}</span>
          </div>
        </li>
      );
    })}
  </ul>
);

export default ExperimentList;
