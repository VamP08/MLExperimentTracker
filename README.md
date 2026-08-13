# MLExperimentTracker

Local-first experiment tracking for machine learning. Runs are plain JSON and JSONL files
in a directory you own — no tracking server, no database, no account, nothing leaving the
machine — and a bundled dashboard reads that directory and shows you what happened.

One install, one command:

```bash
pip install -e ".[server]"     # not on PyPI yet; install from a clone
mlexp demo                     # generate example runs
mlexp ui                       # dashboard at http://127.0.0.1:8000
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

## Status

**Honest summary: the tracking SDK, the storage format, the API and the CLI are done and
tested. Parts of the dashboard are not wired up.**

| Area | State |
|---|---|
| Python SDK — init, log, artifacts, checkpoints, crash handling | Working |
| Storage format, versioned as `format_version` 1.0 | Working |
| API server, CLI (`mlexp ui / demo / ls / show / path`) | Working |
| Dashboard: experiments, runs, overview, params, metrics, system metrics, checkpoints, artifacts | Working |
| Run comparison, ROC curve, confusion matrix, feature importance, gradient view | Components written, not routed |
| Logs tab, both Settings pages | Placeholder UI, not connected |
| Continuous integration | Not set up |

311 tests pass, including a suite that runs the previous Node backend side by side and
diffs its JSON against this one route by route.

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
pytest tests                      # 311 tests
ruff check src tests
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

- Several finished dashboard components are not routed to any page.
- No continuous integration.
- `PATCH /api/experiment/{id}` writes to a file no read path loads, so the edit does not
  survive a reload.
- Setting a run description also changes its display name — both derive from the same
  `notes` field.
- Every request walks the storage tree with no caching or pagination, so response time
  grows with total run history.
- Artifacts carry no file path, so nothing can be downloaded from the UI.
- A `kill -9` still leaves a run reading as running; there is no heartbeat.

## Licence

MIT — see [LICENSE](LICENSE).
