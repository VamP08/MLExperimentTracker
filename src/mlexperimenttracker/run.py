"""The writer half of the product: one :class:`Run` object per training script.

The whole design answers one question from DATA-CONTRACT — *what does a reader that
computes nothing need to be handed?* Three consequences shape this module.

**Every metric is written twice** (DATA-CONTRACT 4.1). The dashboard derives no mean, no
min, no max and no last value from ``metrics.jsonl``; it reads them out of
``summary.json``. So :meth:`Run.log` appends a row *and* folds the value into a running
accumulator, and the accumulator is serialised on a cadence. A metric written only to the
JSONL has a chart and no number; one written only to the summary has a number and no
chart.

**A run that dies without writing a terminal state reads as "running" forever** — there is
no heartbeat and no staleness rule anywhere in the format, so nothing will ever correct
it. That makes the exit path, not the logging path, the most valuable code here, and it is
covered four ways: the context manager, ``sys.excepthook``, signal handlers, and
``atexit``. They overlap deliberately, because each one misses a different exit.

**Names are load-bearing and lossy.** The reader camelCases every metric and parameter key
with a transform that collapses ``_``, ``-`` and space, so this module emits pure lowercase
``snake_case`` and refuses the handful of names that collide with derived statistics.

**Provenance is captured at ``init()`` and can never fail the run.** The manifest — commit,
uncommitted patch, packages, dataset hashes, environment — is what makes a recorded run
checkable later rather than merely viewable, so it is on by default; but it is attached to
somebody's training job, so every path that touches it degrades to a log line. A tracker
that can kill a training job over a missing git binary is a tracker nobody attaches to a
job that matters.

Standard library only. A tracker that adds dependencies to a training environment is a
tracker people uninstall.
"""

from __future__ import annotations

import atexit
import copy
import logging
import math
import os
import platform
import re
import secrets
import signal
import sys
import threading
import time
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any

from . import provenance as _provenance
from .contract import (
    ARTIFACTS_FILE,
    CHECKPOINTS_DIR,
    CONFIG_FILE,
    FORMAT_VERSION,
    METADATA_FILE,
    METRICS_FILE,
    RESERVED_METRIC_KEYS,
    STAT_KEYS,
    SUMMARY_FILE,
    SYSTEM_METRICS_FILE,
    TERMINAL_STATES,
    RunState,
)
from .storage import Storage
from .system import DEFAULT_INTERVAL, SystemSampler

__all__ = ["Run", "init"]

logger = logging.getLogger("mlexperimenttracker")

#: The charset a project or run directory may use. Anything outside it is either unsafe in
#: a URL path segment or unaddressable by the server's containment check, which refuses to
#: resolve it and 404s every endpoint for that run — a run that exists on disk and cannot
#: be opened is a worse failure than a rejection at ``init()``.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

#: What a metric or parameter name must look like for the reader's camelCase transform to
#: be a lossless, predictable ``snake_case -> camelCase``.
_SNAKE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

_SEPARATORS = re.compile(r"[_\s-]+")

#: The suffixes that collide with a derived statistic: metric ``loss_mean`` and the mean of
#: metric ``loss`` both reach the UI as ``lossMean``, and the later key wins silently.
_COLLIDING_SUFFIXES = tuple(f"_{stat}" for stat in STAT_KEYS)

#: Keys of ``summary.json`` this module owns and rewrites. Everything else found in the
#: file is carried through untouched, because the server writes ``notes`` and
#: ``updated_at`` there when a user edits a run description in the UI.
_MANAGED_SUMMARY_KEYS = frozenset({"state", "duration", "end_time", "notes", "metrics_summary"})

_DEFAULT_SUMMARY_INTERVAL: float = 10.0


# --------------------------------------------------------------------------------------
# Streaming statistics
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Accumulator:
    """Welford's online variance, one instance per metric name.

    A naive ``sum(x)`` / ``sum(x**2)`` accumulator loses catastrophically when the values
    are large and their variance is small — precisely the shape of a loss curve that has
    converged, where the interesting digits are the ones cancellation destroys. Welford
    costs one extra multiply per sample and is stable for the same input.
    """

    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf
    latest: float = 0.0

    def update(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)
        if value < self.minimum:
            self.minimum = value
        if value > self.maximum:
            self.maximum = value
        self.latest = value

    def snapshot(self) -> dict[str, float]:
        """The exact four stat keys the reader recognises, plus the ``latest`` without
        which the metric is invisible entirely (``run.service.js:152``).

        ``stddev`` is the **population** standard deviation. Nothing in the format records
        which convention was used and nothing validates it, so the choice is documented
        here and in the fixture rather than inferred: these are the statistics of the
        logged points, not an estimate of a wider population they were drawn from.
        """
        variance = self.m2 / self.count if self.count else 0.0
        return {
            "latest": self.latest,
            "mean": self.mean,
            "min": self.minimum,
            "max": self.maximum,
            # m2 can drift a hair below zero on a constant series; the negative would
            # propagate as a domain error out of sqrt.
            "stddev": math.sqrt(variance) if variance > 0.0 else 0.0,
        }


# --------------------------------------------------------------------------------------
# Process-wide terminal-state hooks
# --------------------------------------------------------------------------------------

#: Live runs, keyed by identity. Mutated with single dict operations only, never under a
#: lock: a signal handler runs on the main thread between bytecodes, so a lock held by the
#: main thread when the signal lands would deadlock against itself, and a lock held by a
#: worker thread would block the terminal write that the handler exists to perform.
_ACTIVE: dict[int, Run] = {}

_INSTALL_LOCK = threading.Lock()
_HOOKS_INSTALLED = False
_PREVIOUS_EXCEPTHOOK: Any = None
_PREVIOUS_SIGNAL_HANDLERS: dict[int, Any] = {}


def _install_hooks(capture_signals: bool) -> None:
    """Install the exit hooks once per process, chaining to whatever was already there.

    Chaining rather than replacing matters: a training script under a supervisor, a
    notebook kernel and pytest all install their own excepthook or SIGINT handler, and a
    tracker that silently swallows them changes how the *program* behaves, which is far
    worse than losing a run's status.

    The hooks are never uninstalled. They are inert with no active runs, and removing them
    would have to restore a handler that a later library may since have replaced.
    """
    global _HOOKS_INSTALLED, _PREVIOUS_EXCEPTHOOK
    with _INSTALL_LOCK:
        if _HOOKS_INSTALLED:
            return
        _HOOKS_INSTALLED = True

        _PREVIOUS_EXCEPTHOOK = sys.excepthook
        sys.excepthook = _excepthook
        atexit.register(_atexit_hook)

        if not capture_signals:
            return
        for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            try:
                previous = signal.getsignal(signum)
                signal.signal(signum, _signal_handler)
            except (ValueError, OSError, RuntimeError):
                # signal.signal only works on the main thread, and some platforms refuse
                # particular signals outright. A run started from a worker thread simply
                # gets no signal coverage; atexit and the context manager still apply.
                continue
            _PREVIOUS_SIGNAL_HANDLERS[int(signum)] = previous


def _finish_active(state: RunState) -> None:
    """Drive every live run to a terminal state, swallowing everything.

    Called from an excepthook, a signal handler and an atexit hook. An exception raised in
    any of those either replaces the user's traceback, lands at an arbitrary bytecode
    boundary in their code, or prints noise during interpreter shutdown.
    """
    for run in list(_ACTIVE.values()):
        try:
            run.finish(state)
        except BaseException:  # noqa: BLE001 - a hook may not raise, ever
            try:
                warnings.warn(f"could not record terminal state for run {run.id}", stacklevel=2)
            except Exception:  # pragma: no cover - warnings machinery may be gone
                pass


def _excepthook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    """An unhandled exception means ``failed`` — the single most important status the
    format can carry, because the alternative is a crashed run that reads as running."""
    state = RunState.INTERRUPTED if issubclass(exc_type, KeyboardInterrupt) else RunState.FAILED
    _finish_active(state)
    hook = _PREVIOUS_EXCEPTHOOK or sys.__excepthook__
    hook(exc_type, exc, tb)


def _signal_handler(signum: int, frame: FrameType | None) -> None:
    """Record ``interrupted``, then hand the signal back to whoever owned it.

    ``interrupted`` rather than ``failed`` because a SIGINT is a decision, not a defect —
    and because the UI files it under "archived", which is where a deliberately killed run
    belongs.
    """
    _finish_active(RunState.INTERRUPTED)

    previous = _PREVIOUS_SIGNAL_HANDLERS.get(signum, signal.SIG_DFL)
    if callable(previous):
        # Covers Python's own default SIGINT handler, which raises KeyboardInterrupt, and
        # any handler the application installed before init() ran.
        previous(signum, frame)
        return
    if previous == signal.SIG_IGN:
        return
    # SIG_DFL: restore the platform default and re-raise, so the process dies with the
    # exit status and disposition the signal actually implies rather than continuing on.
    try:
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)
    except (ValueError, OSError, RuntimeError):  # pragma: no cover - platform dependent
        pass


def _atexit_hook() -> None:
    """The last resort, and the weakest: a normal exit is indistinguishable here from
    ``sys.exit(1)``, so anything still live is recorded as ``completed``.

    Every stronger path — the context manager, the excepthook, the signal handlers — has
    already run and already marked the run terminal by this point, so this only fires for
    a script that reached the end of its main module without calling ``finish()``.
    """
    _finish_active(RunState.COMPLETED)


# --------------------------------------------------------------------------------------
# The Run
# --------------------------------------------------------------------------------------


class Run:
    """A single training run, and the only object a training script needs.

    Construct through :func:`init` rather than directly; the constructor performs the
    ``metadata.json`` write that makes the run visible, and doing that inside ``__init__``
    is what guarantees no code path can obtain a Run that the dashboard cannot see.
    """

    def __init__(
        self,
        storage: Storage,
        project: str,
        run_id: str,
        *,
        name: str | None = None,
        config: Mapping[str, Any] | None = None,
        tags: Sequence[str] | None = None,
        notes: str = "",
        system_metrics: bool = False,
        system_metrics_interval: float = DEFAULT_INTERVAL,
        summary_interval: float = _DEFAULT_SUMMARY_INTERVAL,
        provenance: bool = True,
        capture_diff: bool = True,
        datasets: Sequence[str | os.PathLike[str]] | None = None,
    ) -> None:
        self._storage = storage
        self._project = project
        self._run_id = run_id
        self._name = name or run_id
        self._notes = notes or ""
        self._summary_interval = max(0.0, float(summary_interval))

        self._lock = threading.RLock()
        self._accumulators: dict[str, _Accumulator] = {}
        self._checked_names: set[str] = set()
        self._non_finite_warned: set[str] = set()
        self._next_step = 0
        self._state = RunState.INITIALIZED
        self._finished = False
        self._sampler: SystemSampler | None = None
        #: The manifest as it exists **on disk**, or ``None`` when capture was disabled,
        #: failed, or could not be written. Keeping the in-memory copy in step with the
        #: file is what lets :meth:`log_dataset` amend a manifest rather than invent one.
        self._manifest: dict[str, Any] | None = None

        # Wall clock for what is written, monotonic for what is measured: a duration
        # derived from wall clock goes negative when NTP steps the clock mid-run, and a
        # negative duration renders as "0s" with no indication anything went wrong.
        self._created_at = datetime.now(timezone.utc).astimezone()
        self._epoch_start = self._created_at.timestamp()
        self._monotonic_start = time.monotonic()
        self._last_summary_flush = self._monotonic_start

        metadata = {
            # Absent in the recovered format, so a reader cannot tell what it is reading.
            # Written first, before any run directory exists that lacks it.
            "format_version": FORMAT_VERSION,
            "created_at": _iso(self._created_at),
            "name": self._name,
            "state": self._state.value,
            # A list, always. A bare string here throws inside the dashboard's forEach and
            # returns 500 for every project on the landing page, not just this run.
            "tags": [str(tag) for tag in (tags or [])],
            "notes": self._notes,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "working_directory": os.getcwd(),
        }
        self._dir = storage.create_run(project, run_id, metadata)

        if config is not None:
            self._storage.write_json(self._dir / CONFIG_FILE, flatten_config(config))

        if provenance:
            self._capture_provenance(datasets, capture_diff=capture_diff)

        _ACTIVE[id(self)] = self
        logger.info("run %s started in %s", run_id, self._dir)

        if system_metrics:
            sampler = SystemSampler(
                storage,
                self._dir / SYSTEM_METRICS_FILE,
                interval=system_metrics_interval,
                disk_path=storage.root,
            )
            if sampler.start():
                self._sampler = sampler

    # ----------------------------------------------------------------------------------
    # Identity
    # ----------------------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"Run(project={self._project!r}, id={self._run_id!r}, state={self._state.value!r})"

    @property
    def id(self) -> str:
        return self._run_id

    @property
    def project(self) -> str:
        return self._project

    @property
    def name(self) -> str:
        return self._name

    @property
    def path(self) -> Path:
        """The run directory. Printing it is how a user checks that the SDK and the server
        agree about where the data lives — they resolve the root independently."""
        return self._dir

    @property
    def state(self) -> RunState:
        return self._state

    @property
    def provenance(self) -> dict[str, Any] | None:
        """The manifest written to ``provenance.json``, or ``None`` if there is none.

        A copy, because the manifest describes a moment: handing out the live dict would
        let a caller edit the record of a world that already happened while the file on
        disk says something else. ``None`` is a normal state — capture disabled, no git
        binary, an unwritable directory — and never an error.
        """
        return None if self._manifest is None else copy.deepcopy(self._manifest)

    # ----------------------------------------------------------------------------------
    # Logging
    # ----------------------------------------------------------------------------------

    def log(self, metrics: Mapping[str, float], step: int | None = None) -> None:
        """Append one wide row to ``metrics.jsonl`` and fold the values into the summary.

        One row per call carrying every metric observed at that step — never one row per
        metric. Tall ``{"name": …, "value": …}`` records produce two chart series literally
        called ``name`` and ``value``, which is the single most common way this format is
        written wrong.

        Sparse logging is fully supported: a key absent from a row contributes no point at
        that step, so validation metrics logged once per epoch sit naturally alongside a
        per-batch loss. Note that a metric logged at only one step never appears on a chart
        at all — the chart component drops any series with a single point — so a final test
        score belongs in the summary, which happens automatically here.
        """
        if not isinstance(metrics, Mapping):
            raise TypeError(
                f"log() takes a mapping of metric name to number, not {type(metrics).__name__}"
            )

        with self._lock:
            if self._finished:
                raise RuntimeError(
                    f"run {self._run_id} has finished ({self._state.value}); "
                    "start a new run rather than logging into a terminal one"
                )

            values = {key: value for key, value in self._clean(metrics)}

            if step is None:
                step = self._next_step
            else:
                step = _as_step(step)
            self._next_step = step + 1

            elapsed = self._elapsed()
            record: dict[str, Any] = {
                "step": step,
                # Relative seconds here, epoch seconds in absolute_timestamp — and epoch
                # seconds again under the name `timestamp` in system_metrics.json. The
                # collision is in the format, not in this module.
                "timestamp": round(elapsed, 3),
                "absolute_timestamp": round(self._epoch_start + elapsed, 3),
            }
            record.update(values)
            self._storage.append_jsonl(self._dir / METRICS_FILE, record)

            for key, value in values.items():
                accumulator = self._accumulators.get(key)
                if accumulator is None:
                    accumulator = self._accumulators[key] = _Accumulator()
                accumulator.update(value)

            if self._state is RunState.INITIALIZED:
                self._mark_running()
            elif time.monotonic() - self._last_summary_flush >= self._summary_interval:
                # Periodic, not per-step: summary.json is rewritten whole, so flushing it
                # on every call would turn a tight training loop into a write loop. The
                # terminal write on the exit path is what guarantees the final numbers.
                self._write_summary(self._state)

    def _clean(self, metrics: Mapping[str, float]) -> list[tuple[str, float]]:
        """Validate names, coerce values, and drop what cannot be written.

        Coercion goes through ``float()``, which accepts NumPy scalars and zero-dimension
        tensors without this module importing — or requiring — either library.
        """
        cleaned: list[tuple[str, float]] = []
        for key, raw in metrics.items():
            if not isinstance(key, str) or not key:
                raise TypeError(f"metric names must be non-empty strings, got {key!r}")
            if key in RESERVED_METRIC_KEYS:
                raise ValueError(
                    f"{key!r} is reserved row metadata in metrics.jsonl and cannot be a "
                    f"metric name. Reserved keys: {', '.join(sorted(RESERVED_METRIC_KEYS))}"
                )
            self._check_name(key)

            if isinstance(raw, (str, bytes)):
                # float("0.5") would succeed, and silently coercing it would hide the far
                # more common case — a label or a status string logged by mistake, which
                # the reader would happily plot as a chart series of strings.
                raise TypeError(
                    f"metric {key!r} must be a number, got {type(raw).__name__}. Every "
                    "non-reserved key on a row becomes a chart series with no type check, "
                    "so a string here would be plotted as one."
                )
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    f"metric {key!r} must be a number, got {type(raw).__name__}"
                ) from exc

            if not math.isfinite(value):
                # A bare NaN or Infinity is not legal JSON: the reader drops the entire
                # line, taking every other metric at this step with it. Dropping the one
                # key keeps the rest of the row.
                if key not in self._non_finite_warned:
                    self._non_finite_warned.add(key)
                    warnings.warn(
                        f"metric {key!r} was {raw!r}; non-finite values are not "
                        "representable in JSON and are omitted from the row",
                        stacklevel=4,
                    )
                continue
            cleaned.append((key, value))
        return cleaned

    def _check_name(self, key: str) -> None:
        """Warn once per name about the two ways the reader's key transform loses."""
        if key in self._checked_names:
            return
        self._checked_names.add(key)
        if key.endswith(_COLLIDING_SUFFIXES):
            warnings.warn(
                f"metric {key!r} collides with a derived statistic: the dashboard "
                f"publishes {key!r} and the {key.rsplit('_', 1)[1]} of "
                f"{key.rsplit('_', 1)[0]!r} under the same name, and one silently "
                "overwrites the other",
                stacklevel=5,
            )
        elif not _SNAKE_NAME.match(key):
            warnings.warn(
                f"metric name {key!r} is not lowercase snake_case; the dashboard "
                "camelCases every metric name with a lossy transform, so names differing "
                "only in case or separator collide",
                stacklevel=5,
            )

    # ----------------------------------------------------------------------------------
    # Artifacts
    # ----------------------------------------------------------------------------------

    def log_artifact(
        self,
        name: str,
        type: str,  # noqa: A002 - the field is called `type` on disk and in the API
        metadata: Mapping[str, Any] | None = None,
        version: str = "1",
        file_count: int = 0,
    ) -> None:
        """Append one artifact record to ``artifacts.jsonl``.

        The format has no path and no URL field and nothing in the product can download an
        artifact, so ``metadata`` is not a description of a file — it *is* the payload, and
        it reaches the visualisation components verbatim with no key transform of any kind.
        That makes it the one place in the contract where a key is not snake_case.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("artifact name must be a non-empty string")
        if not isinstance(type, str) or not type:
            raise ValueError("artifact type must be a non-empty string")
        payload = dict(metadata or {})

        with self._lock:
            self._require_live()
            self._storage.append_jsonl(
                self._dir / ARTIFACTS_FILE,
                {
                    "name": name,
                    "type": type,
                    # Half of the composite id `${name}_${version}`; a number would work
                    # but produces a different id for 1 and "1".
                    "version": str(version),
                    "created_at": _iso(datetime.now(timezone.utc).astimezone()),
                    "file_count": int(file_count),
                    "metadata": payload,
                },
            )

    def log_confusion_matrix(
        self,
        labels: Sequence[str],
        matrix: Sequence[Sequence[float]],
        accuracy: float | None = None,
        precision: Sequence[float] | None = None,
        recall: Sequence[float] | None = None,
        f1_score: Sequence[float] | None = None,
        *,
        name: str = "confusion_matrix",
        version: str = "1",
    ) -> None:
        """Log a confusion matrix in the exact shape ``ConfusionMatrix.tsx`` expects.

        ``matrix`` is row = true class, column = predicted class, in ``labels`` order.

        The Python argument is ``f1_score`` and the key written to disk is ``f1Score``:
        the component reads the metadata object straight off the artifact with no key
        transform, so this is the only camelCase key in the whole contract and a
        snake_case spelling would simply not be found. Getting it wrong renders nothing
        and reports nothing.
        """
        label_list = [str(label) for label in labels]
        rows = [[_as_number(cell) for cell in row] for row in matrix]
        if len(rows) != len(label_list) or any(len(row) != len(label_list) for row in rows):
            raise ValueError(
                f"matrix must be {len(label_list)}x{len(label_list)} to match labels, "
                f"got {len(rows)} rows"
            )

        payload: dict[str, Any] = {"labels": label_list, "matrix": rows}
        if accuracy is not None:
            payload["accuracy"] = float(accuracy)
        for key, series in (("precision", precision), ("recall", recall), ("f1Score", f1_score)):
            if series is None:
                continue
            values = [float(value) for value in series]
            if len(values) != len(label_list):
                raise ValueError(
                    f"{key} must carry one value per label ({len(label_list)}), got {len(values)}"
                )
            payload[key] = values

        self.log_artifact(name, "confusion_matrix", payload, version=version)

    def log_roc_curve(
        self,
        fpr: Sequence[float],
        tpr: Sequence[float],
        thresholds: Sequence[float],
        auc: float,
        class_name: str | None = None,
        *,
        name: str | None = None,
        version: str = "1",
    ) -> None:
        """Log one ROC curve — **one artifact line per class**.

        The component collects every ``roc_curve`` artifact on the run and draws them as a
        curve list labelled by ``className``, so a multi-class model calls this once per
        class. The default artifact name carries the class for the same reason: two lines
        sharing a name and a version produce duplicate ids in the artifacts tab.
        """
        false_positive = [float(value) for value in fpr]
        true_positive = [float(value) for value in tpr]
        cuts = [float(value) for value in thresholds]
        if not (len(false_positive) == len(true_positive) == len(cuts)):
            raise ValueError(
                "fpr, tpr and thresholds must be the same length, got "
                f"{len(false_positive)}, {len(true_positive)}, {len(cuts)}"
            )

        payload: dict[str, Any] = {
            "fpr": false_positive,
            "tpr": true_positive,
            "thresholds": cuts,
            "auc": float(auc),
        }
        if class_name is not None:
            payload["className"] = str(class_name)

        if name is None:
            name = "roc_curve" if class_name is None else f"roc_curve_{_slug(str(class_name))}"
        self.log_artifact(name, "roc_curve", payload, version=version)

    def log_feature_importance(
        self,
        features: Mapping[str, float] | Sequence[Any],
        *,
        name: str = "feature_importance",
        version: str = "1",
    ) -> None:
        """Log feature importances, accepting whatever shape the caller already has.

        A ``{name: importance}`` mapping, a sequence of ``{"name", "importance", "std"}``
        dicts, or a sequence of ``(name, importance)`` / ``(name, importance, std)`` tuples
        all normalise to the ``features`` array the component requires — it checks for that
        key explicitly and renders an error without it.

        Sorted by importance, descending, because the component renders the array in order
        and an unsorted importance chart is unreadable.
        """
        normalised: list[dict[str, Any]] = []
        if isinstance(features, Mapping):
            items: list[Any] = [{"name": key, "importance": value} for key, value in features.items()]
        else:
            items = list(features)

        for item in items:
            if isinstance(item, Mapping):
                if "name" not in item or "importance" not in item:
                    raise ValueError(
                        "each feature must carry 'name' and 'importance', got "
                        f"keys {sorted(item)}"
                    )
                entry: dict[str, Any] = {
                    "name": str(item["name"]),
                    "importance": float(item["importance"]),
                }
                if item.get("std") is not None:
                    entry["std"] = float(item["std"])
            elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                if len(item) not in (2, 3):
                    raise ValueError(
                        "a feature tuple must be (name, importance) or (name, importance, std)"
                    )
                entry = {"name": str(item[0]), "importance": float(item[1])}
                if len(item) == 3 and item[2] is not None:
                    entry["std"] = float(item[2])
            else:
                raise TypeError(f"cannot read a feature importance from {type(item).__name__}")
            normalised.append(entry)

        normalised.sort(key=lambda entry: entry["importance"], reverse=True)
        self.log_artifact(name, "feature_importance", {"features": normalised}, version=version)

    # ----------------------------------------------------------------------------------
    # Checkpoints
    # ----------------------------------------------------------------------------------

    def log_checkpoint(self, name: str, step: int, path: str | os.PathLike[str] | None = None) -> Path:
        """Write a ``checkpoints/<name>.json`` sidecar and return its path.

        The reader lists only ``*.json`` files in that directory, so the weights themselves
        — ``.pt``, ``.safetensors``, ``.ckpt`` — are invisible to the product no matter
        where they are written; the sidecar is the checkpoint as far as the UI is concerned.

        When ``path`` points at real weights, their true byte size is recorded. Nothing
        reads that field today — the server reports the *sidecar's* size, so every
        checkpoint currently displays as a few hundred bytes — but writing the real number
        costs nothing and turns the eventual fix into a one-line server change against data
        that already exists.
        """
        sidecar = self._storage.resolve_within(
            self._project, self._run_id, CHECKPOINTS_DIR, f"{name}.json"
        )
        if sidecar is None:
            raise ValueError(
                f"checkpoint name {name!r} is not a usable filename; it must be a single "
                "path component of [A-Za-z0-9._-]"
            )

        payload: dict[str, Any] = {
            "checkpoint_name": str(name),
            # Drives the newest-first sort in the UI; an unparseable value there makes the
            # order of the whole list undefined.
            "created_at": _iso(datetime.now(timezone.utc).astimezone()),
            "step": _as_step(step),
        }
        if path is not None:
            weights = Path(path)
            payload["path"] = str(weights)
            try:
                # Not routed through Storage: the weights are the user's file, outside the
                # run tree by design, and Storage's containment is scoped to that tree.
                payload["size_bytes"] = weights.stat().st_size
            except OSError:
                payload["size_bytes"] = 0

        with self._lock:
            self._require_live()
            self._storage.write_json(sidecar, payload)
        return sidecar

    # ----------------------------------------------------------------------------------
    # Provenance
    # ----------------------------------------------------------------------------------

    def log_dataset(
        self, path: str | os.PathLike[str], *, name: str | None = None
    ) -> dict[str, Any]:
        """Hash a file or directory and record it in the manifest. Returns the entry.

        This is the hook that makes "which data produced this model" answerable, so it has
        to work after ``init()`` and not only at it: the training set is often assembled,
        downloaded or resampled several lines *after* the run starts, and a manifest that
        could only be populated before that would record the intent rather than the data.

        The manifest is re-read from disk before the entry is appended rather than being
        rewritten from the in-memory copy, so a concurrent edit — a second process, a user
        with an editor — is preserved instead of being overwritten by a stale snapshot.
        Entries are keyed by ``name`` when one is given and by ``path`` otherwise, and a
        repeat of the same key **replaces** the earlier entry: two entries claiming the
        same dataset with different digests are not a history, they are a record no
        verifier can act on.

        Nothing here raises over the data: an unreadable path returns an entry carrying
        ``error`` instead of a digest, because a typo in a dataset path must cost the
        dataset field and never the run. It does raise on being called after ``finish()``,
        which is a programming error rather than an environmental one.
        """
        # Hashed outside the lock. A directory of tens of gigabytes takes minutes, and
        # holding the run lock for that long would block every `log()` call in the
        # training loop behind a bookkeeping write.
        entry = _provenance.hash_path(path)
        if name is not None:
            entry["name"] = str(name)

        with self._lock:
            self._require_live()
            manifest = self._storage.read_provenance(self._project, self._run_id)
            if manifest is None:
                manifest = self._manifest
            if manifest is None:
                # No manifest means capture was disabled or failed. Writing one here would
                # produce a file whose git block is empty for a reason nothing recorded,
                # which a later `verify` cannot tell apart from "not a repository" — a
                # worse outcome than an honest absence.
                logger.warning(
                    "run %s has no provenance manifest, so dataset %s was hashed but not "
                    "recorded; pass provenance=True to init() to capture one",
                    self._run_id,
                    entry.get("path"),
                )
                return entry

            datasets = [item for item in manifest.get("datasets") or [] if isinstance(item, dict)]
            identity = entry.get("name") or entry.get("path")
            for index, existing in enumerate(datasets):
                if (existing.get("name") or existing.get("path")) == identity:
                    datasets[index] = entry
                    break
            else:
                datasets.append(entry)
            manifest["datasets"] = datasets

            # `patch=None` leaves any `uncommitted.patch` on disk untouched: this write
            # amends the manifest, and the patch it names was captured at init().
            if not self._storage.write_provenance(self._project, self._run_id, manifest, None):
                logger.warning(
                    "could not update the provenance manifest for run %s", self._run_id
                )
                return entry
            self._manifest = manifest
        return entry

    def _capture_provenance(
        self,
        datasets: Sequence[str | os.PathLike[str]] | None,
        *,
        capture_diff: bool,
    ) -> None:
        """Capture the manifest and write it. Swallows everything, deliberately.

        :func:`provenance.capture` already degrades every block it cannot fill, so this
        ``except`` is for the failure it cannot anticipate — a monkeypatched module, a
        broken NVML binding that segfaults its way into a Python exception, an OS error
        raised from a place the capture layer does not guard. The rule is unconditional and
        this is where it is enforced: **nothing about recording the world may end the run
        that is being recorded.**
        """
        try:
            entries = [_provenance.hash_path(path) for path in (datasets or ())]
            manifest, patch = _provenance.capture(capture_diff=capture_diff, datasets=entries)
            payload = manifest.to_dict()
            if not self._storage.write_provenance(self._project, self._run_id, payload, patch):
                logger.warning(
                    "provenance manifest could not be written for run %s", self._run_id
                )
                return
            self._manifest = payload
        except Exception:
            logger.warning(
                "provenance capture failed for run %s; the run continues without a manifest",
                self._run_id,
                exc_info=True,
            )

    # ----------------------------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------------------------

    def finish(self, state: RunState | str = RunState.COMPLETED, notes: str | None = None) -> None:
        """Write the terminal ``summary.json``. Idempotent, and safe from a signal handler.

        The terminal state goes into ``summary.json`` rather than ``metadata.json`` because
        the dashboard's counters read ``summary.state`` exclusively and never consult
        metadata — a run whose only terminal marker were in metadata would show the right
        badge on its own page and count toward nothing on the landing page.

        Idempotence is not a nicety here. Four independent paths call this — the context
        manager, the excepthook, a signal handler, and atexit — and at least two of them
        fire for a single Ctrl-C.
        """
        terminal = RunState(state) if not isinstance(state, RunState) else state
        if terminal not in TERMINAL_STATES:
            raise ValueError(
                f"{terminal.value!r} is not a terminal state; expected one of "
                f"{', '.join(sorted(s.value for s in TERMINAL_STATES))}"
            )

        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._state = terminal

        _ACTIVE.pop(id(self), None)

        sampler, self._sampler = self._sampler, None
        if sampler is not None:
            sampler.stop()

        if notes is not None:
            self._notes = notes
        self._write_summary(terminal)
        logger.info("run %s finished: %s", self._run_id, terminal.value)

    def __enter__(self) -> Run:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """The precise exit path: it is the only one that knows *why* the block ended.

        Every other hook infers. This one is told, which is why the context-manager form is
        the one worth recommending.
        """
        if exc_type is None:
            self.finish(RunState.COMPLETED)
        elif issubclass(exc_type, KeyboardInterrupt):
            self.finish(RunState.INTERRUPTED)
        elif issubclass(exc_type, SystemExit):
            code = getattr(exc, "code", 0)
            self.finish(
                RunState.COMPLETED if code in (None, 0) else RunState.FAILED
            )
        else:
            self.finish(RunState.FAILED)
        return False

    # ----------------------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------------------

    def _elapsed(self) -> float:
        return time.monotonic() - self._monotonic_start

    def _require_live(self) -> None:
        if self._finished:
            raise RuntimeError(
                f"run {self._run_id} has finished ({self._state.value}) and cannot be written to"
            )

    def _mark_running(self) -> None:
        """Flip to ``running`` on the first logged step, by merge rather than overwrite.

        The UI writes ``tags`` back into this file, and a blind rewrite would destroy tags
        a user added while the run was in flight. Re-reading narrows that race; nothing in
        the format can close it, because there is no locking and no compare-and-swap.

        ``running`` is deliberately not written at ``init()``: a run that has not logged a
        step has not started training, and claiming otherwise would be the one lie the
        dashboard has no way to detect.
        """
        self._state = RunState.RUNNING
        path = self._dir / METADATA_FILE
        metadata = self._storage.read_json(path)
        if isinstance(metadata, dict):
            metadata["state"] = RunState.RUNNING.value
            self._storage.write_json(path, metadata)
        self._write_summary(RunState.RUNNING)

    def _write_summary(self, state: RunState) -> None:
        """Rewrite ``summary.json`` whole, preserving what the server may have put there.

        ``notes`` is the subtle one: it is simultaneously the run's display name and its
        description, and the UI's description editor writes it into this file. An SDK that
        was given no notes therefore defers to whatever is on disk, and only overwrites
        when the caller actually supplied something.
        """
        path = self._dir / SUMMARY_FILE
        existing = self._storage.read_json(path)
        existing = existing if isinstance(existing, dict) else {}

        summary: dict[str, Any] = {
            "state": state.value,
            # Seconds, as a JSON number. The formatter renders anything else — an ISO-8601
            # duration, a timedelta repr, milliseconds — as "0s" or as a wrong number, with
            # no error either way.
            "duration": round(self._elapsed(), 3),
        }
        if state in TERMINAL_STATES:
            summary["end_time"] = _iso(datetime.now(timezone.utc).astimezone())
        elif isinstance(existing.get("end_time"), str):
            summary["end_time"] = existing["end_time"]
        summary["notes"] = self._notes or existing.get("notes") or ""
        summary["metrics_summary"] = {
            key: accumulator.snapshot() for key, accumulator in self._accumulators.items()
        }
        for key, value in existing.items():
            if key not in summary and key not in _MANAGED_SUMMARY_KEYS:
                summary[key] = value

        self._storage.write_json(path, summary)
        self._last_summary_flush = time.monotonic()


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def init(
    project: str = "default",
    *,
    name: str | None = None,
    config: Mapping[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    notes: str = "",
    run_id: str | None = None,
    storage_path: str | os.PathLike[str] | None = None,
    system_metrics: bool = False,
    system_metrics_interval: float = DEFAULT_INTERVAL,
    summary_interval: float = _DEFAULT_SUMMARY_INTERVAL,
    capture_signals: bool = True,
    provenance: bool = True,
    capture_diff: bool = True,
    datasets: Sequence[str | os.PathLike[str]] | None = None,
) -> Run:
    """Start a run: create its directory, write ``metadata.json``, arm the exit hooks.

    ``metadata.json`` is written before this function returns because it is the only file
    whose absence hides a run completely — the run endpoint 404s and the dashboard skips
    the run while still counting it against the project's success rate.

    On ``notes``: it is the run's display **name** and its description on the run page, and
    the first non-empty notes in a project becomes that project's description. The default
    is empty, which means a run displays as ``Run <id>``; setting it accepts that name and
    description are one field. That is a defect in the reader, and this is the honest way
    to live with it.

    System metrics are off by default. Sampling costs a thread and a whole-file rewrite per
    tick, and a tracker should not spend either without being asked.

    **Provenance is on by default**, and unlike system metrics it is worth the cost: it
    writes ``provenance.json`` describing the commit, the uncommitted patch, the resolved
    package versions, the allowlisted environment and the command line — the difference
    between a run someone can look at and a run someone can check. The capture is taken in
    the **current working directory**, which is the repository the training script was
    launched from, and it costs a handful of ``git`` invocations plus one pass over the
    installed distributions; ``datasets`` adds a full content hash per path, which is the
    only part that can take minutes and is therefore never implicit. Datasets can also be
    added later with :meth:`Run.log_dataset`.

    ``capture_diff`` controls the one part of the capture that can leak: the patch is the
    diff of a dirty working tree, and a dirty working tree is exactly where a half-finished
    ``.env`` edit or a key pasted in to get one experiment running lives. It is captured by
    default because a run without it is not reproducible, it is capped and the cap is
    recorded rather than silent, and it is a separate file so it can be deleted without
    destroying the record that it existed. Turn it off for a tree you would not paste into
    a chat window. Set ``provenance=False`` to write nothing at all.
    """
    storage = Storage(storage_path)
    project = _resolve_project(storage, project)

    if run_id is None:
        run_id = _generate_run_id(storage, project)
    else:
        _validate_name(run_id, "run id")

    _install_hooks(capture_signals)
    return Run(
        storage,
        project,
        run_id,
        name=name,
        config=config,
        tags=tags,
        notes=notes,
        system_metrics=system_metrics,
        system_metrics_interval=system_metrics_interval,
        summary_interval=summary_interval,
        provenance=provenance,
        capture_diff=capture_diff,
        datasets=datasets,
    )


def _resolve_project(storage: Storage, project: str) -> str:
    """Validate the project name and adopt the casing of an existing sibling.

    A case-insensitive filesystem — Windows, default macOS — merges ``MyProject`` and
    ``myproject`` into one directory while Linux keeps two, so the same script produces a
    different dashboard on different machines. Adopting the existing spelling makes the
    behaviour identical everywhere, and does it without silently lowercasing a name the
    user chose.
    """
    _validate_name(project, "project name")
    folded = project.casefold()
    for existing in storage.list_projects():
        if existing != project and existing.casefold() == folded:
            return existing
    return project


def _validate_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if not _SAFE_NAME.match(value):
        raise ValueError(
            f"{label} {value!r} must match [A-Za-z0-9._-]+ — it is used verbatim as a "
            "directory name and as a URL path segment, and the server refuses to resolve "
            "anything else, which 404s every endpoint for the run"
        )
    if value.startswith("."):
        raise ValueError(f"{label} {value!r} must not start with a dot")


def _generate_run_id(storage: Storage, project: str) -> str:
    """``<project>_<UTC timestamp>_<4 hex>``.

    Run IDs must be unique across **all** projects, not just within one: the server
    resolves a run by scanning every project and taking the first directory with a matching
    name, so two projects sharing a run ID make one of them unreachable — and because the
    tag and description endpoints resolve the same way, an edit aimed at one can land in
    the other. Embedding the project makes that collision structurally impossible rather
    than merely unlikely, which is what DATA-CONTRACT 8.3 recommends and what a bare UUID
    does not give: a UUID is unique but says nothing, and this ID is legible in a URL.
    """
    # Only the timestamp and the random suffix carry uniqueness, so a very long project
    # name can be truncated in the prefix without weakening anything; a 260-character path
    # limit is a real constraint on Windows.
    prefix = project[:48]
    for _ in range(8):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        candidate = f"{prefix}_{stamp}_{secrets.token_hex(2)}"
        path = storage.run_path(project, candidate)
        if path is not None and not path.exists():
            return candidate
    raise RuntimeError("could not generate an unused run id")  # pragma: no cover


# --------------------------------------------------------------------------------------
# Config normalisation
# --------------------------------------------------------------------------------------


def flatten_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten a config to the top-level scalars both of the reader's parsers can see.

    The file is parsed twice by two functions that disagree: the run page flattens nested
    objects exactly one level and drops everything deeper, while the comparison table drops
    nested objects entirely. A hyperparameter nested two deep is therefore invisible on one
    page and a hyperparameter nested one deep is invisible on the other. Flattening here,
    with a separator this module controls, is the only shape both readers agree on.

    Sequences become comma-joined strings rather than arrays: an array flattens by numeric
    index into ``layers0``, ``layers1``, which is meaningless in a parameters table, and
    vanishes altogether in the comparison table.
    """
    flat: dict[str, Any] = {}
    _flatten_into(flat, config, prefix="")
    return flat


def _flatten_into(flat: dict[str, Any], value: Mapping[str, Any], prefix: str) -> None:
    for raw_key, raw_value in value.items():
        key = _normalise_key(f"{prefix}_{raw_key}" if prefix else str(raw_key))
        if isinstance(raw_value, Mapping):
            _flatten_into(flat, raw_value, key)
            continue
        if key in flat:
            warnings.warn(
                f"config key {key!r} is set twice after normalisation; the later value wins",
                stacklevel=4,
            )
        flat[key] = _config_scalar(raw_value)


def _normalise_key(key: str) -> str:
    """Lowercase snake_case, so the reader's lossy camelCase transform is reversible.

    ``learning_rate``, ``learning-rate``, ``learning rate`` and ``learning_RATE`` all reach
    the UI as ``learningRate`` and silently overwrite one another. Normalising here means
    two config keys that would collide in the UI collide *before* they are written, where a
    warning is possible.
    """
    return "_".join(part.lower() for part in _SEPARATORS.split(str(key)) if part)


def _config_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # A non-finite float is not legal JSON and would cost the run every parameter, not
        # just this one — the whole file fails to parse.
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(item) for item in value)
    return str(value)


# --------------------------------------------------------------------------------------
# Small conversions
# --------------------------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    """ISO 8601 with an explicit offset, always.

    The server parses these with ``new Date(...)``, which reads a bare local timestamp as
    local time *in the server's zone* — so a run recorded in one timezone and viewed in
    another silently shifts by hours.
    """
    return moment.isoformat(timespec="milliseconds")


def _as_step(step: Any) -> int:
    if isinstance(step, bool):
        raise TypeError("step must be an integer, not a bool")
    if isinstance(step, int):
        return step
    try:
        value = float(step)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"step must be an integer, got {type(step).__name__}") from exc
    if not value.is_integer():
        raise ValueError(f"step must be a whole number, got {step!r}")
    return int(value)


def _as_number(value: Any) -> float | int:
    """Keep whole numbers whole. Confusion-matrix cells are counts, and a count rendered
    as ``812.0`` in a table reads as a rounding error that is not there."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    number = float(value)
    return int(number) if number.is_integer() else number


def _slug(value: str) -> str:
    """A filesystem- and id-safe fragment for a class name that came from a dataset."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return cleaned or "class"
