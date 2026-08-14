# MLExperimentTracker

[![CI](https://github.com/VamP08/MLExperimentTracker/actions/workflows/ci.yml/badge.svg)](https://github.com/VamP08/MLExperimentTracker/actions/workflows/ci.yml)

Local-first experiment tracking for machine learning. Runs are plain JSON and JSONL files
in a directory you own — no tracking server, no database, no account, nothing leaving the
machine — and a bundled dashboard reads that directory and shows you what happened.

One install, one command:

```bash
pip install -e ".[server]"     # not on PyPI yet; install from a clone
mlexp demo                     # generate example runs
mlexp ui                       # dashboard at http://127.0.0.1:5000
```

## Tracking a run

```python
import mlexperimenttracker as met

run = met.init(
    project="cifar10-cnn",
    name="resnet50-lr3e4",
    config={"learning_rate": 3e-4, "batch_size": 64, "epochs": 30},
    tags=["baseline"],
)

for step, (loss, acc) in enumerate(train()):
    run.log({"loss": loss, "accuracy": acc}, step=step)

run.finish()
```

Or as a context manager, which also records failure:

```python
with met.init(project="cifar10-cnn") as run:
    run.log({"loss": 0.31}, step=1)
```

Three things that are deliberate:

- **The base install has no third-party dependencies.** Adding the tracker to a training
  script pulls in nothing. `psutil`/`nvidia-ml-py` (system metrics) and `fastapi`/`uvicorn`
  (the dashboard) are optional extras, imported lazily and degraded gracefully when absent.
- **A crashed run does not report as running.** `atexit`, `sys.excepthook` and
  SIGINT/SIGTERM handlers write a terminal state, so an interrupted run lands in
  `interrupted` and a crashed one in `failed`. A hard `kill -9` still defeats this — there
  is no heartbeat.
- **Summary statistics are computed as you log**, using Welford's algorithm, so the run
  directory is complete at every moment without a second pass over the metrics.

Artifacts have typed helpers that produce exactly the payloads the dashboard renders:

```python
run.log_confusion_matrix(labels=[...], matrix=[[...]], accuracy=0.94)
run.log_roc_curve(fpr=[...], tpr=[...], thresholds=[...], auc=0.97, class_name="cat")
run.log_feature_importance([{"name": "age", "importance": 0.31}])
run.log_checkpoint("epoch_10", step=10, path="checkpoints/epoch_10.pt")
```

## Try it

`examples/quickstart.py` is a complete tracked training run in one file, and it needs
**nothing but the standard library** — a hand-written logistic regression trained by
gradient descent on synthetic data, which is the point rather than a shortcut: an example
that required torch to demonstrate a dependency-free tracker would be arguing against
itself.

```bash
python examples/quickstart.py     # a few seconds
mlexp ui
```

It logs five metrics per epoch, a confusion matrix, an ROC curve, permutation feature
importances and three checkpoints with real weight files, prints its progress into the
captured log, and records a provenance manifest you can immediately check with
`mlexp verify`. The data is drawn from a known model with two deliberately uninformative
columns, so the importance chart has a right answer to be judged against. See
`examples/README.md` for what to look at afterwards.

## Log capture

Every run records its own output. `stdout`, `stderr` and the `logging` root handler are
captured into `logs.jsonl` for the life of the run — the terminal still receives every
byte, existing logging handlers are untouched, and both are restored on every exit path
including a crash.

```python
run.log_text("resolved device: cuda:0", level="info")   # the explicit half
met.init(..., capture_output=False, capture_logging=False)  # turn the implicit halves off
```

It is on by default because a run whose output was not recorded cannot answer the first
question anybody asks of a failure. The file is byte-capped, and reaching the cap is
written into a final record rather than ending the log silently — a log that stops
part-way through with no explanation reads as the end of the run.

## Status

**Honest summary: the tracking SDK, the storage format, the API and the CLI are done and
tested. Parts of the dashboard are not wired up.**

| Area | State |
|---|---|
| Python SDK — init, log, artifacts, checkpoints, crash handling | Working |
| Storage format, versioned as `format_version` 1.2 | Working |
| API server, CLI (`mlexp ui / demo / ls / show / path / provenance / verify / replay`) | Working |
| Provenance capture — commit, uncommitted diff, packages, dataset hashes, environment | Working |
| `mlexp verify` — drift against the recorded world; `mlexp replay` — rebuild the commit | Working |
| Log capture — `stdout`, `stderr` and `logging` into `logs.jsonl`, served by the API | Working |
| Dashboard: experiments, runs, overview, params, metrics, logs, evaluation, system metrics, checkpoints, artifacts | Working |
| Run comparison, ROC curve, confusion matrix, feature importance, gradient view | Working — reachable from the Evaluation, Metrics and Runs pages |
| Continuous integration — ruff and pytest on Linux (3.10–3.12) and Windows, plus the dashboard lint and build | Workflow in the repo; no runs until it has a remote |

491 tests pass, including a suite that runs the previous Node backend side by side and
diffs its JSON against this one route by route.

### Provenance, verify and replay

`met.init()` records the git commit, the **uncommitted diff** as a patch file, the resolved
package versions, a content hash of any dataset you name, and the environment — into
`provenance.json` beside the run. Capture never fails the training run: anything it cannot
determine is written as a recorded reason.

```bash
mlexp provenance <run_id>      # what the run recorded about the world it ran in
mlexp verify <run_id>          # what has changed since — exit 0 ok, 1 drifted, 2 unverifiable
mlexp replay <run_id> --into ../rebuild
```

`verify` answers in three states, not two: a check reports drift only when it asked the
question and got a different answer, so a missing `git` binary is `unverifiable` rather than
a false accusation. `replay` uses `git worktree add` into a directory you name — it never
runs `checkout`, `reset` or `stash`, so your working tree is not touched.

What it does **not** restore: the package set is resolved versions rather than a lockfile,
datasets are recorded and not restored, untracked files are named and not restored, and
hardware, driver and kernel nondeterminism are outside what any manifest can capture.
`uncommitted.patch` is a diff of your working tree, so treat it as sensitive — pass
`capture_diff=False` for a tree you would not paste into a chat window.

## Storage layout

```
$EXPERIMENT_STORAGE_PATH/          # default: ~/.experiment_tracker
  <project>/
    <run_id>/
      metadata.json                # REQUIRED — a run without this is invisible
      summary.json                 # final state, duration, aggregated metrics
      config.json                  # hyperparameters, flat and top-level
      metrics.jsonl                # one JSON object per logged step
      artifacts.jsonl              # one artifact record per line
      system_metrics.json          # array of resource samples
      checkpoints/*.json           # checkpoint sidecars
      provenance.json              # commit, packages, dataset hashes, environment
      uncommitted.patch            # the working-tree diff — may contain secrets
      logs.jsonl                   # captured stdout, stderr and logging, append-only
```

Only `metadata.json` is required; anything else missing degrades one part of the UI rather
than failing the request. Two properties are easy to get wrong if you write the format by
hand:

- **The server computes no aggregates.** Scalar metrics come only from `summary.json`'s
  `metrics_summary`; per-step history comes only from `metrics.jsonl`. A writer produces
  both. The SDK does this for you.
- **`metrics.jsonl` is wide, not tall.** One line per step, carrying every metric observed
  at that step as its own key. `timestamp`, `absolute_timestamp`, `step`, `run_id`,
  `run_status`, `run_state` and `start_timestamp` are reserved; every other key is a metric
  name.

## API

Served at the same origin as the dashboard. `:id` must be a single path component —
separators and traversal sequences are rejected with a 404.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | Version, storage root, project and run counts |
| GET | `/api/dashboard` | All experiments with aggregate stats |
| GET | `/api/experiment/all` · `/api/experiment` · `/api/experiment/{id}` | Experiments |
| GET | `/api/experiment/{id}/runs` | Runs with params and latest metrics |
| GET | `/api/run` · `/api/run/{id}` | Runs |
| GET | `/api/run/{id}/metrics` | Metrics pivoted into per-name series |
| GET | `/api/run/{id}/metrics/timeseries?metric=` | One metric as a time series |
| GET | `/api/run/{id}/metrics/export?format=csv\|json` | Export |
| GET | `/api/run/{id}/system-metrics` · `/checkpoints` · `/artifacts` | Run detail |
| GET | `/api/run/{id}/logs?level=&limit=&offset=` · `/logs/download` | Captured output |
| GET | `/api/run/{id}/provenance` · `/patch` · `/verify` | The recorded world, and drift against it |
| PATCH | `/api/run/{id}/tags` · `/description` | Update a run |
| PATCH | `/api/experiment/{id}` | Update experiment description |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `EXPERIMENT_STORAGE_PATH` | `~/.experiment_tracker` | Root of the run storage tree |

`mlexp ui` takes `--host`, `--port`, `--storage` and `--no-browser`.

The server binds loopback on purpose. There is no authentication layer, because this is a
single-user local tool and the trust boundary is the loopback interface rather than a login
form. If you bind it to a network interface, add authentication first — the API has no
authorisation checks and four endpoints write to disk.

## Development

```bash
pip install -e ".[all]"
pytest tests                      # 491 tests
ruff check src tests examples
python scripts/build_ui.py        # build the React app into the package
```

The dashboard lives in `ml_frontend/` (React 19, Vite, TypeScript). For frontend work run
`npm run dev` there alongside `mlexp ui`; Vite proxies `/api` to port 5000, so point the
server at that port. `scripts/build_ui.py` compiles it into
`src/mlexperimenttracker/server/static/`, which is gitignored as a build artifact and
packaged into the wheel.

`ml_backend/` is the previous Node implementation. It is retained because the parity test
suite runs it and diffs its responses against the Python server; it is not part of the
product and is not needed to use this.

## Known limitations

- The dashboard has no per-run or per-experiment settings page. Archiving and deleting a
  run were never implemented, so the two pages offering them were removed rather than left
  as buttons that do nothing.
- Nothing in the dashboard has been exercised by a rendering test. Every payload shape it
  reads has been checked against a running server; the pages themselves are covered only by
  the TypeScript build and the linter.
- The CI workflow is in the repository but has never run, because the repository has no
  remote yet. Treat the badge as a promise until it goes green.
- Setting a run description also changes its display name — both derive from the same
  `notes` field.
- Every request walks the storage tree with no caching or pagination, so response time
  grows with total run history.
- Artifacts carry no file path, so nothing can be downloaded from the UI.
- A `kill -9` still leaves a run reading as running; there is no heartbeat.

## Licence

MIT — see [LICENSE](LICENSE).
