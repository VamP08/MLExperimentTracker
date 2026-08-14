# Examples

## `quickstart.py`

A complete tracked training run in one file, using **nothing but the Python standard
library**. It trains a logistic regression by gradient descent on synthetic data and
records every artifact the dashboard knows how to draw.

The absence of dependencies is deliberate. The tracker's base install pulls in no
third-party package, and an example that required torch or scikit-learn to demonstrate
that would be arguing against itself — you would be installing 800 MB to find out whether
a tracker installs cleanly. The model here is small enough to read in one sitting, and
every number the dashboard shows was measured rather than chosen.

### Run it

```bash
pip install -e ".[server]"        # from a clone; the example needs only the base install
python examples/quickstart.py
mlexp ui                          # dashboard at http://127.0.0.1:5000
```

It takes a few seconds. Output looks like this:

```
run quickstart_20260813T183147Z_5db0 -> ...\quickstart\quickstart_20260813T183147Z_5db0
dataset ...\examples\quickstart_output\synthetic.csv (900 train / 300 val)
epoch   1/60  loss 0.6434  acc 0.7756  val_loss 0.6394  val_acc 0.8067
epoch  20/60  loss 0.4638  acc 0.7800  val_loss 0.4358  val_acc 0.8000
  checkpoint epoch_020 -> epoch_020.weights.json
epoch  60/60  loss 0.4466  acc 0.7867  val_loss 0.4144  val_acc 0.7900
  checkpoint epoch_060 -> epoch_060.weights.json
training finished; evaluating on the held-out split
  confusion matrix [[107, 26], [37, 130]]  accuracy 0.7900
  roc auc 0.8958 over 302 thresholds
  permutation importance tenure_months=0.2133, monthly_charges=0.0860, ...
done. inspect it with:  mlexp show quickstart_20260813T183147Z_5db0
```

### What to look at afterwards

In the dashboard, open the **quickstart** experiment and then the run:

| Where | What was written |
|---|---|
| Metrics | `loss`, `accuracy`, `val_loss`, `val_accuracy`, `grad_norm` — one row per epoch, from `run.log(...)` |
| Overview | the config, the summary statistics computed as the run went, and the provenance manifest |
| Checkpoints | three sidecars, each naming a real weight file written next to it |
| Artifacts | the confusion matrix, the ROC curve and the feature importances |
| Logs | the script's own `print` output, captured verbatim — nothing in the script writes a log file |

From the command line:

```bash
mlexp ls                                 # the experiment and its run
mlexp show <run_id>                      # config, metrics, artifacts, checkpoints
mlexp provenance <run_id>                # commit, packages, dataset hash, environment
mlexp verify <run_id>                    # what has changed since the run
```

`verify` is the one worth trying twice. Immediately after the run it reports
`REPRODUCIBLE`; edit a tracked file, or `examples/quickstart_output/synthetic.csv`, and it
names exactly what drifted.

### Two things worth noticing in the code

**The feature importances have a right answer.** The synthetic data is drawn from a known
logistic model in which `noise_a` and `noise_b` contribute nothing, so the importance
chart is checkable rather than merely plausible: those two columns should land at the
bottom. The importances are permutation importances — the measured drop in validation
accuracy when a column is shuffled, repeated five times so the `std` the artifact carries
means something — rather than the model's own coefficients.

**Labels are sampled, not thresholded.** A deterministic label would make the problem
separable, every metric would read 1.0, and the charts would teach a reader nothing about
what this dashboard looks like on real data. Validation accuracy here settles around 0.79
because the data has irreducible noise in it, which is the honest shape.

### Options

```
--project        experiment name (default: quickstart)
--seed           seed for the data and the shuffling (default: 7)
--epochs         full-batch gradient steps (default: 60)
--learning-rate  (default: 0.6)
--l2             L2 penalty (default: 0.001)
--train-samples  (default: 900)
--val-samples    (default: 300)
--storage        storage root; defaults to $EXPERIMENT_STORAGE_PATH or ~/.experiment_tracker
--outdir         where the dataset and the checkpoint weights go (default: examples/quickstart_output)
```

The seed governs everything: the same seed produces the same dataset, the same weights and
the same final numbers, so two runs differ only where you changed something. Run it twice
with different `--learning-rate` values to get a comparison worth looking at.

`--outdir` is regenerated on every run and is gitignored. It holds the generated dataset
and the checkpoint weight files — the weights live outside the run directory on purpose,
which is the arrangement a real `.pt` file would be in: the dashboard lists the JSON
sidecars, never the weights themselves.
