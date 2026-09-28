import "./EmptyState.css";

interface Props {
  /** Re-runs the dashboard fetch without a page reload. */
  onRetry: () => void;
}

/** Shown when the storage directory has no runs yet, with the two ways to add some. */
const EmptyState = ({ onRetry }: Props) => {
  return (
    <section className="panel" aria-labelledby="dashboard-empty-title">
      <div className="state empty">
        <h2 id="dashboard-empty-title">No runs yet</h2>
        <p>
          The dashboard is running and reading your storage directory — <code>~/.experiment_tracker</code>{" "}
          unless <code>EXPERIMENT_STORAGE_PATH</code> or <code>mlexp ui --storage</code> points somewhere else.
          Nothing has been written there yet.
        </p>

        <div className="empty-options">
          <div className="empty-option">
            <h3>Fill it with example runs</h3>
            <p>Writes a small set of example projects so you can look around before instrumenting anything.</p>
            <pre className="empty-code">
              <code>mlexp demo</code>
            </pre>
          </div>

          <div className="empty-option">
            <h3>Track a real run</h3>
            <p>
              Three calls in your training script. The run directory is complete at every moment, so this page
              shows progress while training is still going.
            </p>
            <pre className="empty-code">
              <code>
                {`import mlexperimenttracker as met

run = met.init(project="cifar10-cnn", config={"lr": 3e-4})
run.log({"loss": loss, "accuracy": acc}, step=step)
run.finish()`}
              </code>
            </pre>
          </div>
        </div>

        <button type="button" className="btn btn-primary" onClick={onRetry}>
          Check again
        </button>
      </div>
    </section>
  );
};

export default EmptyState;
