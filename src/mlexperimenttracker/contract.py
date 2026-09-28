"""Constants and transforms for the on-disk run format.

Ported from the original Express reader, so odd-looking rules match what the UI expects.
No I/O, stdlib only.
"""

from __future__ import annotations

import math
import re
from enum import Enum

# --------------------------------------------------------------------------------------
# Format identity
# --------------------------------------------------------------------------------------

# Written into metadata.json. Missing means the original unversioned format.
# Minor bumps add optional files only, so older readers still work; major bumps
# change something a reader depends on.
#   1.1: provenance.json + uncommitted.patch
#   1.2: logs.jsonl
FORMAT_VERSION: str = "1.2"

# Same variable name the old Express server read, so both resolve the same root.
STORAGE_ENV_VAR: str = "EXPERIMENT_STORAGE_PATH"

DEFAULT_STORAGE_DIRNAME: str = ".experiment_tracker"

# --------------------------------------------------------------------------------------
# File and directory names
# --------------------------------------------------------------------------------------

METADATA_FILE: str = "metadata.json"
SUMMARY_FILE: str = "summary.json"
CONFIG_FILE: str = "config.json"
METRICS_FILE: str = "metrics.jsonl"
ARTIFACTS_FILE: str = "artifacts.jsonl"
SYSTEM_METRICS_FILE: str = "system_metrics.json"
CHECKPOINTS_DIR: str = "checkpoints"
ARTIFACTS_DIR: str = "artifacts"

# Provenance manifest (1.1). Optional; absence means "not recorded".
PROVENANCE_FILE: str = "provenance.json"

# Uncommitted diff, referenced by the manifest's git.diff_file. Kept separate so the
# manifest stays readable and the patch (which may hold secrets) can be deleted alone.
PATCH_FILE: str = "uncommitted.patch"

# Captured run output (1.2), append-only JSONL: time, level, source, text per line.
# Optional; absence means nothing was captured. Plain text is derived on read.
LOGS_FILE: str = "logs.jsonl"

# Closed set, least to most severe: the stdlib logging levels, lowercased, minus NOTSET.
LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error", "critical")

# stdout/stderr are teed writes, logging is a logging.Handler record, user is log_text().
LOG_SOURCES: tuple[str, ...] = ("stdout", "stderr", "logging", "user")

# Fallback for unknown or missing levels, so the record is still filterable.
DEFAULT_LOG_LEVEL: str = "info"

# Fallback for unknown sources; only an explicit call can pass one.
DEFAULT_LOG_SOURCE: str = "user"


class RunState(str, Enum):
    """The five states the reader recognises. Anything else reads as ``running``."""

    INITIALIZED = "initialized"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class UIStatus(str, Enum):
    """Dashboard status. ``interrupted`` shows as ``archived``."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


# No "unknown" entry; see map_state().
STATE_TO_UI_STATUS: dict[RunState, UIStatus] = {
    RunState.INITIALIZED: UIStatus.RUNNING,
    RunState.RUNNING: UIStatus.RUNNING,
    RunState.COMPLETED: UIStatus.COMPLETED,
    RunState.FAILED: UIStatus.FAILED,
    RunState.INTERRUPTED: UIStatus.ARCHIVED,
}

# Nothing is written after these. There's no heartbeat, so a run that never reaches one
# reads as running forever.
TERMINAL_STATES: frozenset[RunState] = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.INTERRUPTED}
)

# Row metadata in metrics.jsonl. Every other key is charted as a metric.
RESERVED_METRIC_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "absolute_timestamp",
        "step",
        "run_id",
        "run_status",
        "run_state",
        "start_timestamp",
    }
)

# The only aggregates the reader reads from metrics_summary; others are silently dropped.
STAT_KEYS: tuple[str, ...] = ("mean", "max", "min", "stddev")

# Object-valued config keys the run-detail reader discards.
CONFIG_DROP_KEYS: frozenset[str] = frozenset({"storage", "system", "logging"})

_CAMEL_SPLIT = re.compile(r"[_\s-]+")


def map_state(state: str | None) -> str:
    """Return the UI status for a raw ``state``. Unknown values and ``None`` map to running."""
    if state is None:
        return UIStatus.RUNNING.value
    try:
        return STATE_TO_UI_STATUS[RunState(state)].value
    except ValueError:
        return UIStatus.RUNNING.value


def normalise_log_level(level: object) -> str:
    """Return a member of :data:`LOG_LEVELS`, else the default. Aliases like WARN aren't mapped."""
    if isinstance(level, str):
        folded = level.strip().lower()
        if folded in LOG_LEVELS:
            return folded
    return DEFAULT_LOG_LEVEL


def normalise_log_source(source: object) -> str:
    """Return a member of :data:`LOG_SOURCES`, else the default."""
    if isinstance(source, str):
        folded = source.strip().lower()
        if folded in LOG_SOURCES:
            return folded
    return DEFAULT_LOG_SOURCE


def camel_case(s: str) -> str:
    """Port of the reader's key transform for param and metric names.

    Lossy: ``learning_rate`` and ``learning-rate`` collide, so the SDK writes snake_case.
    """
    parts = _CAMEL_SPLIT.split(s)
    out = [parts[0].lower()]
    out.extend(p[:1].upper() + p[1:].lower() for p in parts[1:])
    return "".join(out)


def _is_js_number(value: object) -> bool:
    """True for values JS would call a number (bools excluded)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def format_duration(seconds: float | None) -> str:
    """Format seconds as ``1h 5m 3s`` / ``5m 3s`` / ``3s``. Non-numbers give ``0s``.

    Rounds the total first, unlike the Express version, which could show ``5m 60s``.
    """
    if not _is_js_number(seconds) or not seconds or math.isnan(float(seconds)):
        return "0s"
    total = _js_round(float(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def format_duration_no_hours(seconds: float | None) -> str:
    """Experiment-endpoint variant with no hours (90 minutes is ``90m 0s``).

    Kept separate because the frontend shows both strings as-is. Same rounding fix.
    """
    if not _is_js_number(seconds) or not seconds or math.isnan(float(seconds)):
        return "0s"
    total = _js_round(float(seconds))
    m, s = divmod(total, 60)
    if m == 0:
        return f"{s}s"
    return f"{m}m {s}s"


def _js_round(value: float) -> int:
    """JS ``Math.round``: halves round up, not to even like Python's round()."""
    if math.isnan(value) or math.isinf(value):
        return 0
    return math.floor(value + 0.5)
