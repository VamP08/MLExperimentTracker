# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The **on-disk storage format is versioned separately** from the package, as
`format_version` inside each run's `metadata.json`. A package version says what the code
can do; a format version says what a run directory on somebody's disk contains, and the
two move at different speeds. Both are listed below.

## [Unreleased]

## [0.2.0] - 2026-09-29

Storage format unchanged (1.2).

### Changed

- **Dashboard redesign.** White and blue with a dark theme, colour kept for status and chart
  series. Fixed sidebar with the archive as a tree, and only the main pane scrolls. Fonts
  are bundled, so the dashboard makes no external requests.
- The landing page charts runs per day, success rate and run durations, and lists the
  latest runs with their training curves.
- Each run page opens with its reproducibility verdict and the `mlexp verify` command.
- The experiment overview overlays the newest runs' training curves, ranks runs by their
  final value, and plots a parameter against the result.
- New app mark and favicon.
- Comments and docstrings trimmed throughout.

### Added

- Component tests for the dashboard (`npm test`, Vitest), run in CI.
- `scripts/bench_storage.py`, which times the storage walk at different archive sizes.

### Removed

- The old Express implementation (`parity_reference/`). The Python port matched it route
  by route, and `tests/test_parity.py` keeps the recorded responses.
- The run switcher above each run (the sidebar lists runs now), and `framer-motion`.

### Notes

- The 0.1.0 notes below say CI had never run. It has run on every push since the
  repository went public.

## [0.1.0] — 2026-08-13

First release. Storage format **1.2**.

### Added

- **Tracking SDK.** `met.init(project=…, name=…, config=…, tags=…)` returns a `Run` that
  works as a context manager; `run.log({...}, step=…)` appends one wide row per step;
  `finish()` writes the terminal state. Typed artifact helpers —
  `log_confusion_matrix`, `log_roc_curve`, `log_feature_importance`, `log_artifact`,
  `log_checkpoint`, `log_dataset`, `log_text` — produce exactly the payloads the dashboard
  renders, including the one camelCase key the format requires.
- **Zero third-party dependencies in the base install**, and a test that keeps it that
  way: `tests/test_no_dependencies.py` drives a full run lifecycle in a subprocess behind
  a `sys.meta_path` import blocker, and asserts the blocker works before asserting
  anything else. The dashboard (`fastapi`, `uvicorn`) and resource sampling (`psutil`,
  `nvidia-ml-py`) are optional extras, imported lazily and degraded rather than required.
- **Terminal state on every exit path.** `atexit`, `sys.excepthook` and SIGINT/SIGTERM
  handlers write `failed` or `interrupted` with the traceback, so a run that dies does not
  keep reading as running. A `kill -9` still defeats this; there is no heartbeat.
- **Summary statistics computed as you log**, using Welford's algorithm, so the run
  directory is complete at every moment without a second pass over the metrics.
- **On-disk storage format**, specified independently of this package and versioned in
  `metadata.json`. It reached **1.2** over the course of this release: 1.0 is the base
  layout, 1.1 added `provenance.json` and `uncommitted.patch`, 1.2 added `logs.jsonl`.
  Both bumps are additive — a new file, appended to and never rewritten, read only by
  name — so a 1.0 reader handed a 1.2 directory reads it correctly, and a 1.2 reader
  handed a 1.0 directory finds absent files it already has to handle.
- **Provenance capture, on by default.** `provenance.json` records the git commit and
  branch, the uncommitted working-tree diff as a real patch file with its own hash and
  size, the resolved package versions, the CPU and GPU inventory, an allowlisted slice of
  the environment, the command line, and a content hash of any dataset you name. Capture
  never raises into the training run: anything it cannot determine is written as a
  recorded reason rather than an exception.
- **`mlexp verify`** — re-checks a run's recorded world against the current one and
  answers in three states rather than two, so a missing `git` binary reports
  `unverifiable` instead of accusing the tree of drift. Exit code 0 ok, 1 drifted,
  2 unverifiable.
- **`mlexp replay`** — reconstructs the commit a run was executed from via
  `git worktree add` into a directory you name, and applies the recorded patch. It never
  runs `checkout`, `reset` or `stash`, so the working tree is not touched.
- **Log capture** (format 1.2). `stdout`, `stderr` and the `logging` root handler are
  captured into `logs.jsonl` for the life of the run, with the terminal still receiving
  every byte and every stream restored on every exit path including a crash. Capture is
  byte-capped, and reaching the cap is recorded in a final record rather than ending the
  file silently. `run.log_text()` writes an explicit line; `capture_output=False` and
  `capture_logging=False` turn the implicit halves off.
- **HTTP API and dashboard**, served at one origin by `mlexp ui`. A FastAPI server reads
  the storage tree — experiments, runs, metrics (pivoted, as a time series, or exported to
  CSV/JSON), system metrics, checkpoints, artifacts, provenance, the patch, verification
  and logs — and a React dashboard built into the wheel renders it. Run identifiers must
  be a single path component; separators and traversal sequences are rejected with a 404.
- **CLI**: `mlexp ui`, `ls`, `show`, `path`, `provenance`, `verify`, `replay`, and `demo`,
  which generates a deterministic seeded archive so the dashboard has something in it on a
  clean machine.
- **`examples/quickstart.py`** — a complete tracked training run in one file using nothing
  but the standard library: a hand-written logistic regression trained by gradient descent
  on synthetic data, logging metrics per epoch, a confusion matrix, an ROC curve,
  permutation feature importances and checkpoints with real weight files.
- **491 tests**, including a parity suite that boots the retained Express implementation
  and diffs its JSON against the Python server route by route, with the deliberate
  divergences enumerated rather than papered over.
- **Continuous integration** — `.github/workflows/ci.yml` runs ruff and the full suite on
  Ubuntu (3.10, 3.11, 3.12) and Windows (3.12), and lints and builds the dashboard. Node
  and `parity_reference/node_modules` are installed on every Python leg so the parity
  suite runs live rather than skipping, and the job fails loudly if those prerequisites go
  missing.

### Removed

- **The per-run and per-experiment Settings pages.** Both were placeholder UI whose
  controls — archive, delete, rename — have no implementation anywhere in the product. A
  button that does nothing is a worse claim than an absent page, so the pages and their
  tabs are gone. The global theme toggle at `/settings` is unaffected.
- **The unused Node dependencies in `parity_reference/`** — `bcrypt`, `jsonwebtoken`,
  `mongodb`, `mongoose`, `multer`, `ts-node`, `typescript` — along with the five Mongoose
  models that were the only importers of `mongoose` and described a database design this
  project never had. 116 packages removed; the retained Express server still serves every
  route the parity suite diffs.
- **The Tailwind toolchain from the dashboard.** It was installed three ways and never
  activated: no config file, no directive in any of the 39 stylesheets. All the CSS is
  hand-written and stays that way.
- **Next.js debris** — the `app/page.tsx` wrapper that nothing imported and the
  `"use client"` directives, both inert in a Vite SPA.

### Fixed

- **The Logs tab shows the run's real output.** It reads
  `GET /api/run/{id}/logs?level=&limit=&offset=`, filters by level, pages, and offers the
  plain-text download. It previously rendered thirteen hardcoded entries dated 2023-06-15
  with a dead filter and a dead download button.

### Known limitations

Stated here rather than left to be discovered:

- The dashboard has no per-run or per-experiment settings page. Archiving and deleting a
  run were never implemented, so the two pages that offered them were removed rather than
  shipped as controls that do nothing.
- No rendering test covers the dashboard. Every payload shape it reads was checked against
  a running server; the pages themselves are covered only by the build and the linter.
- The CI workflow is in the repository but has never run, because the repository has no
  remote yet.
- `PATCH /api/experiment/{id}` writes a description that the Python server reads back, but
  the retained Express implementation does not; that divergence is deliberate and tested.
- Setting a run description also changes its display name — both derive from the same
  `notes` field in the storage format.
- Every request walks the storage tree with no index, cache or pagination, so response
  time grows with total run history.
- Artifacts carry no file path, so nothing can be downloaded from the UI. Visualisation
  payloads are inlined into the artifact record instead.
- `uncommitted.patch` is a diff of your working tree and should be treated as sensitive.
  Pass `capture_diff=False` for a tree you would not paste into a chat window.

[Unreleased]: https://github.com/VamP08/MLExperimentTracker/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/VamP08/MLExperimentTracker/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/VamP08/MLExperimentTracker/releases/tag/v0.1.0
