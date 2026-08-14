import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FiChevronLeft, FiChevronRight } from "react-icons/fi";
import "./RunSwitcher.css";

/**
 * One row of `GET /api/experiment/:id/runs`. The endpoint returns more than
 * this; only the three fields the switcher names are read.
 */
interface SiblingRun {
  _id: string;
  name: string;
  status: string;
}

interface RunSwitcherProps {
  /** From the run object — `GET /api/run/:id` carries `experimentId`. */
  experimentId: string | null;
  /** The run currently on screen. */
  runId: string;
}

/**
 * Step between the runs of one experiment without leaving the run page.
 *
 * Three deliberate behaviours:
 *
 * - **No wrapping.** Prev is disabled on the first run and Next on the last.
 *   Wrapping around a list of runs makes "next" mean two different things
 *   depending on where you already are, which is exactly the disorientation
 *   this control exists to remove.
 * - **A failed fetch renders nothing.** No banner, no disabled shell. This is a
 *   convenience sitting on top of a page that works without it, and a broken
 *   sibling list must not be mistaken for a broken run.
 * - **One run renders nothing either.** A permanently disabled pair of arrows
 *   is furniture, not information.
 *
 * Ordering is whatever the endpoint returns, so "next" here means the same
 * thing as "the row below" on the experiment's Runs tab.
 */
const RunSwitcher = ({ experimentId, runId }: RunSwitcherProps) => {
  const navigate = useNavigate();
  const [siblings, setSiblings] = useState<SiblingRun[]>([]);

  useEffect(() => {
    if (!experimentId) {
      setSiblings([]);
      return;
    }

    let cancelled = false;

    const fetchSiblings = async () => {
      try {
        const res = await fetch(`/api/experiment/${encodeURIComponent(experimentId)}/runs`);
        if (!res.ok) throw new Error(`Request failed with ${res.status}`);

        const data = await res.json();
        if (cancelled) return;
        setSiblings(Array.isArray(data) ? data : []);
      } catch {
        // Quietly. See the note on this component.
        if (!cancelled) setSiblings([]);
      }
    };

    fetchSiblings();

    return () => {
      cancelled = true;
    };
  }, [experimentId]);

  const index = siblings.findIndex((run) => run._id === runId);

  // `index === -1` covers the window between navigating to a new run and its
  // sibling list arriving, and the case of a run the experiment listing does
  // not contain. Neither can produce an honest "run N of M".
  if (siblings.length < 2 || index === -1) return null;

  const goTo = (target: number) => {
    const run = siblings[target];
    if (run) navigate(`/runs/${encodeURIComponent(run._id)}`);
  };

  return (
    <div className="run-switcher">
      <button
        type="button"
        className="run-switcher-step"
        onClick={() => goTo(index - 1)}
        disabled={index === 0}
        aria-label="Previous run"
        title="Previous run"
      >
        <FiChevronLeft aria-hidden="true" />
        <span className="run-switcher-step-text">Prev</span>
      </button>

      <select
        className="run-switcher-select"
        value={runId}
        onChange={(event) => navigate(`/runs/${encodeURIComponent(event.target.value)}`)}
        aria-label="Switch to another run in this experiment"
      >
        {siblings.map((run) => (
          <option key={run._id} value={run._id}>
            {run.name} — {run.status || "unknown"}
          </option>
        ))}
      </select>

      <button
        type="button"
        className="run-switcher-step"
        onClick={() => goTo(index + 1)}
        disabled={index === siblings.length - 1}
        aria-label="Next run"
        title="Next run"
      >
        <span className="run-switcher-step-text">Next</span>
        <FiChevronRight aria-hidden="true" />
      </button>

      <span className="run-switcher-position">
        Run {index + 1} of {siblings.length}
      </span>
    </div>
  );
};

export default RunSwitcher;
