import type React from 'react';
import './EmptyState.css';

interface Props {
  /** Re-runs the dashboard fetch, so the page can be filled without a reload. */
  onRetry: () => void;
}

/**
 * Shown when the storage directory holds no runs. This is the first screen on a fresh
 * install, so it says what the tool is reading and the two ways to put something there
 * (GAPS M24) rather than leaving an unexplained blank page.
 */
const EmptyState: React.FC<Props> = ({ onRetry }) => {
  return (
    <section className="dashboard-empty" aria-labelledby="dashboard-empty-title">
      <h2 className="dashboard-empty-title" id="dashboard-empty-title">
        No runs yet
      </h2>
      <p className="dashboard-empty-lead">
        The dashboard is running and reading your storage directory —{' '}
        <code>~/.experiment_tracker</code> unless <code>EXPERIMENT_STORAGE_PATH</code> or{' '}
        <code>mlexp ui --storage</code> points somewhere else. Nothing has been written there yet.
      </p>

      <div className="dashboard-empty-options">
        <div className="dashboard-empty-card">
          <h3 className="dashboard-empty-card-title">Fill it with example runs</h3>
          <p className="dashboard-empty-card-text">
            Writes a small set of example projects so you can look around before instrumenting anything.
          </p>
          <pre className="dashboard-empty-code">
            <code>mlexp demo</code>
          </pre>
        </div>

        <div className="dashboard-empty-card">
          <h3 className="dashboard-empty-card-title">Track a real run</h3>
          <p className="dashboard-empty-card-text">
            Three calls in your training script. The run directory is complete at every moment, so this page
            shows progress while training is still going.
          </p>
          <pre className="dashboard-empty-code">
            <code>
              {`import mlexperimenttracker as met

run = met.init(project="cifar10-cnn", config={"lr": 3e-4})
run.log({"loss": loss, "accuracy": acc}, step=step)
run.finish()`}
            </code>
          </pre>
        </div>
      </div>

      <button type="button" className="dashboard-empty-retry" onClick={onRetry}>
        Check again
      </button>
    </section>
  );
};

export default EmptyState;
