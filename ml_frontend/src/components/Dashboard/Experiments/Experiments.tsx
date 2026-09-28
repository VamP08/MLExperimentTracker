import type { MouseEvent } from "react";
import { Link } from "react-router-dom";
import type { DashboardExperiment } from "../../../lib/dashboard";
import { shortDate } from "../format";
import "./Experiments.css";

interface ExperimentsProps {
  experiments: DashboardExperiment[];
  onExperimentClick: (experimentId: string) => void;
}

/** A zero reads as absence, so it is dimmed and the non-zero counts stand out. */
const Count = ({ value }: { value: number }) => (
  <td className={`r num${value === 0 ? " muted" : ""}`}>{value}</td>
);

/**
 * The archive, one experiment per row. The whole row opens the experiment; the name is also
 * a real link so it can be opened in a new tab or reached from the keyboard.
 */
const Experiments = ({ experiments, onExperimentClick }: ExperimentsProps) => {
  const onRowClick = (event: MouseEvent, id: string) => {
    // The name link navigates on its own; handling the click again here would double it.
    if ((event.target as HTMLElement).closest("a")) return;
    onExperimentClick(id);
  };

  return (
    <div className="table-wrap">
      <table className="table exp-table">
        <thead>
          <tr>
            <th scope="col">Experiment</th>
            <th scope="col">Description</th>
            <th scope="col" className="r">Runs</th>
            <th scope="col" className="r">Completed</th>
            <th scope="col" className="r">Failed</th>
            <th scope="col" className="r">Running</th>
            <th scope="col" className="r">Success</th>
            <th scope="col" className="r">Avg duration</th>
            <th scope="col">Last run</th>
            <th scope="col">Tags</th>
          </tr>
        </thead>
        <tbody>
          {experiments.map((exp) => (
            <tr key={exp._id} className="exp-row" onClick={(e) => onRowClick(e, exp._id)}>
              <td className="exp-name">
                <Link to={`/experiment/${encodeURIComponent(exp._id)}`}>{exp.name || exp._id}</Link>
              </td>
              <td className="muted" title={exp.description || undefined}>
                <div className="exp-desc">{exp.description || "—"}</div>
              </td>
              <td className="r num">{exp.stats.totalRuns}</td>
              <Count value={exp.stats.completedRuns} />
              <Count value={exp.stats.failedRuns} />
              <Count value={exp.stats.runningRuns} />
              <td className="r num">{exp.stats.successRate}</td>
              <td className="r num">{exp.stats.avgDuration}</td>
              <td className="num exp-when">{shortDate(exp.stats.lastRun)}</td>
              <td>
                {exp.tags.length > 0 ? (
                  <span className="exp-tags">
                    {/* Three tags keep every row one line tall; the rest are in the tooltip. */}
                    {exp.tags.slice(0, 3).map((tag) => (
                      <span key={tag} className="tag">
                        {tag}
                      </span>
                    ))}
                    {exp.tags.length > 3 && (
                      <span className="tag exp-more" title={exp.tags.slice(3).join(", ")}>
                        +{exp.tags.length - 3}
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default Experiments;
