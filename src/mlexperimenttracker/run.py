"""Writer side: one :class:`Run` per training script.

Metrics go to metrics.jsonl and into running stats in summary.json (the UI computes none).
Terminal state is written on four exit paths; provenance capture never raises. Stdlib only.
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
import traceback
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
from .logs import DEFAULT_MAX_BYTES, LogWriter
from .logs import flush_pending as _flush_pending_logs
from .logs import install as _install_logs
from .storage import Storage
from .system import DEFAULT_INTERVAL, SystemSampler

__all__ = ["Run", "init"]

logger = logging.getLogger("mlexperimenttracker")

# Allowed project/run dir names. Anything else is unsafe in a URL or would 404 in the
# server's containment check, so reject it at init() instead.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# Names that survive the reader's camelCase transform predictably.
_SNAKE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

_SEPARATORS = re.compile(r"[_\s-]+")

# `loss_mean` and the mean of `loss` both become `lossMean` in the UI.
_COLLIDING_SUFFIXES = tuple(f"_{stat}" for stat in STAT_KEYS)

# summary.json keys this module rewrites. Other keys (e.g. the server's updated_at) are
# preserved.
_MANAGED_SUMMARY_KEYS = frozenset({"state", "duration", "end_time", "notes", "metrics_summary"})

_DEFAULT_SUMMARY_INTERVAL: float = 10.0


# --------------------------------------------------------------------------------------
# Streaming statistics
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Accumulator:
    """Running stats for one metric, using Welford's online variance.

    Sum-of-squares loses precision on large values with small variance (a converged loss).
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
        """The four stat keys the reader knows, plus ``latest`` (required to show at all).

        ``stddev`` is the population standard deviation of the logged points.
        """
        variance = self.m2 / self.count if self.count else 0.0
        return {
            "latest": self.latest,
            "mean": self.mean,
            "min": self.minimum,
            "max": self.maximum,
            # m2 can drift just below zero on a constant series.
            "stddev": math.sqrt(variance) if variance > 0.0 else 0.0,
        }


# --------------------------------------------------------------------------------------
# Process-wide terminal-state hooks
# --------------------------------------------------------------------------------------

# Live runs by id(). Single dict ops only, no lock: signal handlers run on the main thread
# and would deadlock on a lock the interrupted code already holds.
_ACTIVE: dict[int, Run] = {}

_INSTALL_LOCK = threading.Lock()
_HOOKS_INSTALLED = False
_PREVIOUS_EXCEPTHOOK: Any = None
_PREVIOUS_SIGNAL_HANDLERS: dict[int, Any] = {}


def _install_hooks(capture_signals: bool) -> None:
    """Install exit hooks once per process, chaining to any existing handlers.

    Never uninstalled: they do nothing with no active runs, and something else may have
    replaced them since.
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
                # Only works on the main thread, and some platforms refuse some signals.
                # atexit and the context manager still cover those runs.
                continue
            _PREVIOUS_SIGNAL_HANDLERS[int(signum)] = previous


def _finish_active(state: RunState) -> None:
    """Finish every live run. Never raises, since it's called from exit hooks."""
    for run in list(_ACTIVE.values()):
        try:
            run.finish(state)
        except BaseException:  # noqa: BLE001 - a hook may not raise, ever
            try:
                warnings.warn(f"could not record terminal state for run {run.id}", stacklevel=2)
            except Exception:  # pragma: no cover - warnings machinery may be gone
                pass


def _record_traceback(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None, state: RunState
) -> None:
    """Write the traceback into every live run's log as one record.

    Done explicitly because the streams are restored before the previous excepthook prints.
    """
    for run in list(_ACTIVE.values()):
        run._record_exception(exc_type, exc, tb, state)


def _excepthook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    """Unhandled exception: mark runs failed (interrupted for KeyboardInterrupt)."""
    state = RunState.INTERRUPTED if issubclass(exc_type, KeyboardInterrupt) else RunState.FAILED
    _record_traceback(exc_type, exc, tb, state)
    _finish_active(state)
    hook = _PREVIOUS_EXCEPTHOOK or sys.__excepthook__
    hook(exc_type, exc, tb)


def _signal_handler(signum: int, frame: FrameType | None) -> None:
    """Mark runs ``interrupted``, then pass the signal to the previous handler."""
    _finish_active(RunState.INTERRUPTED)

    previous = _PREVIOUS_SIGNAL_HANDLERS.get(signum, signal.SIG_DFL)
    if callable(previous):
        # Includes Python's default SIGINT handler (raises KeyboardInterrupt).
        previous(signum, frame)
        return
    if previous == signal.SIG_IGN:
        return
    # SIG_DFL: restore it and re-raise so the process exits the way the signal implies.
    try:
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)
    except (ValueError, OSError, RuntimeError):  # pragma: no cover - platform dependent
        pass


def _atexit_hook() -> None:
    """Last resort: mark anything still live ``completed``.

    Can't tell a clean exit from ``sys.exit(1)``. Only reached if nothing else finished the
    run. kill -9 is not covered by any hook.
    """
    _finish_active(RunState.COMPLETED)


# --------------------------------------------------------------------------------------
# The Run
# --------------------------------------------------------------------------------------


class Run:
    """A single training run. Create it with :func:`init`.

    The constructor writes ``metadata.json``, so any Run object is already visible in the UI.
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
        capture_output: bool = True,
        capture_logging: bool = True,
        log_limit_bytes: int = DEFAULT_MAX_BYTES,
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
        self._log_limit_bytes = int(log_limit_bytes)
        self._log_writer: LogWriter | None = None
        self._uninstall_logs: Any = None
        # Mirrors provenance.json on disk; None if capture was off or failed.
        self._manifest: dict[str, Any] | None = None

        # Wall clock for timestamps, monotonic for durations (NTP steps can make wall-clock
        # durations negative).
        self._created_at = datetime.now(timezone.utc).astimezone()
        self._epoch_start = self._created_at.timestamp()
        self._monotonic_start = time.monotonic()
        self._last_summary_flush = self._monotonic_start

        metadata = {
            "format_version": FORMAT_VERSION,
            "created_at": _iso(self._created_at),
            "name": self._name,
            "state": self._state.value,
            # Must be a list; a string here breaks the whole dashboard.
            "tags": [str(tag) for tag in (tags or [])],
            "notes": self._notes,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "working_directory": os.getcwd(),
        }
        self._dir = storage.create_run(project, run_id, metadata)

        if config is not None:
            self._storage.write_json(self._dir / CONFIG_FILE, flatten_config(config))

        # Before provenance so its warnings land in the run log. Always created so
        # log_text() works even with capture off.
        self._log_writer = LogWriter(
            storage,
            project,
            run_id,
            start_time=self._epoch_start,
            max_bytes=self._log_limit_bytes,
        )
        self._uninstall_logs = _install_logs(
            self, capture_output=capture_output, capture_logging=capture_logging
        )

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
        """The run directory."""
        return self._dir

    @property
    def state(self) -> RunState:
        return self._state

    @property
    def provenance(self) -> dict[str, Any] | None:
        """A copy of the ``provenance.json`` manifest, or ``None`` if none was written."""
        return None if self._manifest is None else copy.deepcopy(self._manifest)

    # ----------------------------------------------------------------------------------
    # Logging
    # ----------------------------------------------------------------------------------

    def log(self, metrics: Mapping[str, float], step: int | None = None) -> None:
        """Append one row with all given metrics to ``metrics.jsonl`` and update the summary.

        Rows can be sparse. ``step`` defaults to the previous step + 1. A metric logged once
        won't chart (single point) but still shows in the summary.
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
                # Seconds since start. (system_metrics.json uses `timestamp` for epoch.)
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
                # Throttled: summary.json is rewritten whole. finish() writes final numbers.
                self._write_summary(self._state)

    def _clean(self, metrics: Mapping[str, float]) -> list[tuple[str, float]]:
        """Validate names and coerce values with float() (handles NumPy/torch scalars).

        Non-finite values are dropped with a one-time warning.
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
                # Reject strings even if numeric; usually a label logged by mistake.
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
                # NaN/Infinity isn't valid JSON and the reader would drop the whole row.
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
        """Warn once per name if it would collide after the reader's camelCase transform."""
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

    def log_text(self, message: str, *, level: str = "info") -> None:
        """Write one line to the run's log (source ``user``) without printing it.

        An unknown ``level`` falls back to info. Raises after ``finish()``.
        """
        with self._lock:
            self._require_live()
            writer = self._log_writer
        if writer is not None:
            writer.write(message, level=level, source="user")

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

        ``metadata`` is the payload itself (there's no file behind it) and reaches the UI
        components without any key transform.
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
                    # Part of the id `${name}_${version}`; str so 1 and "1" match.
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
        """Log a confusion matrix in the shape ``ConfusionMatrix.tsx`` expects.

        ``matrix`` rows are true classes, columns predicted, in ``labels`` order.
        ``f1_score`` is written as ``f1Score`` because the component reads keys as-is.
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
        """Log one ROC curve. For multi-class, call once per class.

        The default name includes the class so artifact ids stay unique.
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
        """Log feature importances, sorted descending.

        Accepts a ``{name: importance}`` mapping, dicts with ``name``/``importance``/``std``,
        or ``(name, importance[, std])`` tuples.
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

        The UI only lists the JSON sidecars, not weight files. If ``path`` is given, the
        weights' real size is recorded (the server currently shows the sidecar's size).
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
            # The UI sorts on this.
            "created_at": _iso(datetime.now(timezone.utc).astimezone()),
            "step": _as_step(step),
        }
        if path is not None:
            weights = Path(path)
            payload["path"] = str(weights)
            try:
                # Not via Storage: the weights live outside the run tree.
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
        """Hash a file or directory, add it to the manifest, and return the entry.

        Re-reads the manifest from disk first so concurrent edits survive. Entries are keyed
        by ``name`` (or ``path``); a repeat replaces the old entry. An unreadable path gives
        an entry with ``error`` instead of raising. Raises after ``finish()``.
        """
        # Hash outside the lock; big directories take minutes and would block log().
        entry = _provenance.hash_path(path)
        if name is not None:
            entry["name"] = str(name)

        with self._lock:
            self._require_live()
            manifest = self._storage.read_provenance(self._project, self._run_id)
            if manifest is None:
                manifest = self._manifest
            if manifest is None:
                # Capture was off or failed. Don't create a partial manifest that verify
                # would misread as "not a repository".
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

            # patch=None keeps the uncommitted.patch captured at init().
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
        """Capture and write the manifest. Swallows all errors.

        capture() already degrades per block; this catches anything unexpected so
        provenance can never raise into the training job.
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
        """Write the terminal state to ``summary.json``. Idempotent and signal-safe.

        The dashboard counters read ``summary.state``, not metadata. Idempotent because the
        four exit paths can overlap (a Ctrl-C fires at least two).
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
        try:
            self._write_summary(terminal)
        finally:
            # Always restore stdout/stderr, even if the summary write failed.
            self._close_logs()
        logger.info("run %s finished: %s", self._run_id, terminal.value)

    def __enter__(self) -> Run:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Finish with a state based on how the block exited. Never suppresses exceptions."""
        if exc_type is None:
            self.finish(RunState.COMPLETED)
            return False

        if issubclass(exc_type, KeyboardInterrupt):
            state = RunState.INTERRUPTED
        elif issubclass(exc_type, SystemExit):
            code = getattr(exc, "code", 0)
            state = RunState.COMPLETED if code in (None, 0) else RunState.FAILED
        else:
            state = RunState.FAILED

        # Before finish() closes the log. A caught exception never reaches the excepthook,
        # so this is the only place to record it.
        if state is not RunState.COMPLETED:
            self._record_exception(exc_type, exc, tb, state)
        self.finish(state)
        return False

    # ----------------------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------------------

    def _elapsed(self) -> float:
        return time.monotonic() - self._monotonic_start

    def _record_exception(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
        state: RunState,
    ) -> None:
        """Record the traceback that ended the run. Swallows everything."""
        writer = self._log_writer
        if writer is None or exc_type is None:
            return
        # Flush any partial print line so it lands before the traceback.
        _flush_pending_logs(self)
        try:
            text = "".join(traceback.format_exception(exc_type, exc, tb)).rstrip("\n")
        except Exception:  # noqa: BLE001 - an exit path may not raise
            return
        # Ctrl-C isn't an error.
        level = "warning" if state is RunState.INTERRUPTED else "error"
        writer.write(text, level=level, source="stderr")

    def _close_logs(self) -> None:
        """Restore the streams and stop the writer. Never raises (called from exit hooks)."""
        uninstall, self._uninstall_logs = self._uninstall_logs, None
        if uninstall is None:
            return
        try:
            uninstall()
        except BaseException:  # noqa: BLE001 - an exit path may not raise, ever
            try:
                warnings.warn(
                    f"could not restore the captured streams for run {self._run_id}",
                    stacklevel=2,
                )
            except Exception:  # pragma: no cover - warnings machinery may be gone
                pass

    def _require_live(self) -> None:
        if self._finished:
            raise RuntimeError(
                f"run {self._run_id} has finished ({self._state.value}) and cannot be written to"
            )

    def _mark_running(self) -> None:
        """Set state to ``running`` on the first logged step.

        Re-reads metadata before writing so tags edited in the UI aren't lost (narrows the
        race; there's no file locking).
        """
        self._state = RunState.RUNNING
        path = self._dir / METADATA_FILE
        metadata = self._storage.read_json(path)
        if isinstance(metadata, dict):
            metadata["state"] = RunState.RUNNING.value
            self._storage.write_json(path, metadata)
        self._write_summary(RunState.RUNNING)

    def _write_summary(self, state: RunState) -> None:
        """Rewrite ``summary.json``, keeping keys the server added.

        ``notes`` from disk (edited in the UI) wins unless the caller set notes.
        """
        path = self._dir / SUMMARY_FILE
        existing = self._storage.read_json(path)
        existing = existing if isinstance(existing, dict) else {}

        summary: dict[str, Any] = {
            "state": state.value,
            # Seconds as a number; the UI shows anything else as "0s".
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
    capture_output: bool = True,
    capture_logging: bool = True,
    log_limit_bytes: int = DEFAULT_MAX_BYTES,
) -> Run:
    """Start a run: create its directory, write ``metadata.json``, install exit hooks.

    ``notes`` is both the run's display name and its description in the UI; empty shows
    ``Run <id>``. System metrics are off by default (a thread plus a file rewrite per tick).

    Provenance is on by default and captured from the current working directory: commit,
    uncommitted patch, packages, environment, command line. ``datasets`` are content-hashed,
    which can be slow; more can be added later with :meth:`Run.log_dataset`.
    ``capture_diff=False`` skips the patch, which can contain secrets from a dirty tree.
    ``provenance=False`` writes no manifest.

    Output capture is on by default: stdout/stderr are teed and the root logger gets a
    handler, all restored on finish. ``capture_output`` and ``capture_logging`` turn these
    off. ``log_limit_bytes`` caps the log file; hitting it writes one final notice record.
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
        capture_output=capture_output,
        capture_logging=capture_logging,
        log_limit_bytes=log_limit_bytes,
    )


def _resolve_project(storage: Storage, project: str) -> str:
    """Validate the project name and reuse an existing project's casing if one matches.

    Keeps behaviour the same on case-insensitive (Windows/macOS) and Linux filesystems.
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

    IDs must be unique across all projects since the server looks runs up by ID alone.
    Including the project name guarantees that and keeps the ID readable.
    """
    # Truncating the prefix is safe (uniqueness comes from the suffix) and helps with
    # Windows path limits.
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
    """Flatten a config to top-level snake_case scalars.

    The run page and comparison table handle nesting differently, so only flat keys show
    up in both. Nested keys join with ``_``; sequences become comma-joined strings.
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
    """Lowercase snake_case, so keys that would collide in the UI collide here and warn."""
    return "_".join(part.lower() for part in _SEPARATORS.split(str(key)) if part)


def _config_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # Non-finite floats would make the whole file invalid JSON.
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(item) for item in value)
    return str(value)


# --------------------------------------------------------------------------------------
# Small conversions
# --------------------------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    """ISO 8601 with an explicit offset, so readers in other timezones don't shift it."""
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
    """Keep whole numbers as ints so counts don't render as ``812.0``."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    number = float(value)
    return int(number) if number.is_integer() else number


def _slug(value: str) -> str:
    """Filesystem- and id-safe version of a class name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return cleaned or "class"
