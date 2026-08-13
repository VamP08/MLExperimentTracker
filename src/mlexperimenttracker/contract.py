"""Shared vocabulary for the on-disk run format.

Every constant, enum and transform here exists because the reader — the dashboard the
user actually looks at — hardcodes it. The names are lifted from the Express services
that defined the format by reading it, so this module is deliberately a port rather
than a design: where a rule looks arbitrary, it is arbitrary in the same way the reader
is, and changing it here would silently change what the UI displays.

No I/O and no imports beyond the standard library, so that a training script pays
nothing for importing it.
"""

from __future__ import annotations

import math
import re
from enum import Enum

# --------------------------------------------------------------------------------------
# Format identity
# --------------------------------------------------------------------------------------

#: Written into ``metadata.json``. The recovered contract carried no version at all,
#: which left a reader unable to tell what it was reading and a writer unable to declare
#: what it wrote. Absence therefore means "the original, unversioned format"; 1.0 was the
#: first version that said so out loud. The current reader ignores unknown keys, so
#: adding it costs nothing on the read side and is only expensive to add late.
#:
#: **1.1 (2026-08-13) adds the provenance manifest** — ``provenance.json`` and its
#: ``uncommitted.patch`` sibling. The bump is the minor half of the pair precisely because
#: nothing else changed: both files are new, both are optional, and no existing file gained
#: a required field. So a 1.0 reader handed a 1.1 directory reads it correctly and sees
#: exactly what it saw before — two files it does not open, in a directory format it
#: already understands — and a 1.1 reader handed a 1.0 directory finds no manifest, which
#: is the same state as a 1.1 run whose capture failed and is therefore already a case it
#: has to handle. The major half stays at 1 until something a reader depends on changes
#: shape, which is the distinction a bare counter could not express and the reason this is
#: a string.
FORMAT_VERSION: str = "1.1"

#: Consulted before the home-directory default, and named identically to the variable the
#: Express server reads, so both halves of the product resolve the same root.
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

#: The reproducibility manifest (format 1.1). Optional: a run whose capture failed, or
#: whose writer predates 1.1, simply has no such file, and every reader must treat absence
#: as "not recorded" rather than as "nothing had changed".
PROVENANCE_FILE: str = "provenance.json"

#: The uncommitted diff, referenced by ``provenance.json``'s ``git.diff_file``. A separate
#: file rather than a string inside the manifest for two reasons: a patch is megabytes of
#: text that would make the manifest unreadable and unparseable at a glance, and keeping it
#: separate means a user can delete the patch — which is the one part of the capture that
#: can hold a secret — without destroying the record that it existed.
PATCH_FILE: str = "uncommitted.patch"


class RunState(str, Enum):
    """The five states the reader recognises. Anything else reads as ``running``.

    Inherits from :class:`str` so a member can be written straight into JSON and
    compared against a raw string read back off disk without unwrapping.
    """

    INITIALIZED = "initialized"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class UIStatus(str, Enum):
    """What the dashboard calls a state. Note ``interrupted`` surfaces as ``archived``:
    the UI has no vocabulary for "killed", so it reuses the archive bucket."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


#: The mapping the reader applies. There is deliberately no "unknown" — see :func:`map_state`.
STATE_TO_UI_STATUS: dict[RunState, UIStatus] = {
    RunState.INITIALIZED: UIStatus.RUNNING,
    RunState.RUNNING: UIStatus.RUNNING,
    RunState.COMPLETED: UIStatus.COMPLETED,
    RunState.FAILED: UIStatus.FAILED,
    RunState.INTERRUPTED: UIStatus.ARCHIVED,
}

#: States after which nothing more is written. A run that never reaches one of these
#: reads as running forever, because the format has no heartbeat and no staleness rule.
TERMINAL_STATES: frozenset[RunState] = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.INTERRUPTED}
)

#: Keys the metrics reader treats as row metadata rather than as a series. Every other
#: key on a ``metrics.jsonl`` line becomes a chartable metric, with no type check — so a
#: stray bookkeeping field turns into a line on the chart.
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

#: The only aggregate names the reader looks for inside ``metrics_summary``. ``std``,
#: ``var``, ``count``, ``median`` and everything else are dropped without warning.
STAT_KEYS: tuple[str, ...] = ("mean", "max", "min", "stddev")

#: Object-valued config keys the run-detail reader discards outright.
CONFIG_DROP_KEYS: frozenset[str] = frozenset({"storage", "system", "logging"})

_CAMEL_SPLIT = re.compile(r"[_\s-]+")


def map_state(state: str | None) -> str:
    """Return the UI status for a raw ``state`` string.

    Anything unrecognised — a typo, a capitalised ``Completed``, or the ``None`` a run
    that died before writing a terminal state leaves behind — maps to ``running``. That
    is the reader's behaviour and it is why a crashed run looks alive.
    """
    if state is None:
        return UIStatus.RUNNING.value
    try:
        return STATE_TO_UI_STATUS[RunState(state)].value
    except ValueError:
        return UIStatus.RUNNING.value


def camel_case(s: str) -> str:
    """Port of the reader's key transform, applied to every parameter and metric name.

    Split on runs of underscore, whitespace or hyphen; lowercase the first token; for
    every later token uppercase the first character and lowercase the rest. The
    transform is lossy and the target is a plain object, so ``learning_rate`` and
    ``learning-rate`` collide — which is why the SDK emits pure lowercase snake_case.
    """
    parts = _CAMEL_SPLIT.split(s)
    out = [parts[0].lower()]
    out.extend(p[:1].upper() + p[1:].lower() for p in parts[1:])
    return "".join(out)


def _is_js_number(value: object) -> bool:
    """True for values JavaScript would call a number. ``bool`` is excluded because it
    is a distinct primitive there, and the reader's ``typeof`` guards reject it."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def format_duration(seconds: float | None) -> str:
    """Format a duration for the run-detail view: ``1h 5m 3s`` / ``5m 3s`` / ``3s``.

    Anything that is not a number — ``None``, an ISO-8601 duration string, a timedelta
    repr — is ``0s``, silently. That is the reader's rule, and it is the reason
    ``duration`` must be written as a JSON number of seconds and nothing else.

    Deliberate divergence from the Express implementation, which floors the minutes and
    rounds the seconds *independently*: 359.7 s renders there as ``5m 60s``, 3599.8 s as
    ``59m 60s``, and 3659.7 s as ``1h 0m 60s``. Rounding the total first and decomposing
    afterwards makes the carry happen once, so a duration can never display sixty seconds.
    This is visible on the dashboard rather than theoretical — average-duration tiles land
    on fractional seconds constantly.
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
    """The *other* duration formatter — the experiment endpoints carry their own copy
    that has no hours branch, so a 90-minute run reads ``90m 0s`` there and ``1h 30m 0s``
    on the run page. Reproduced rather than unified because the frontend renders both
    strings verbatim; unifying them is a reader change, not a writer change.

    Carries the same round-the-total-first fix as :func:`format_duration`, for the same
    reason — this is the formatter behind ``stats.avgDuration``, which is exactly where a
    fractional average lands.
    """
    if not _is_js_number(seconds) or not seconds or math.isnan(float(seconds)):
        return "0s"
    total = _js_round(float(seconds))
    m, s = divmod(total, 60)
    if m == 0:
        return f"{s}s"
    return f"{m}m {s}s"


def _js_round(value: float) -> int:
    """``Math.round`` semantics: halves go up, not to even. Python's :func:`round` is
    banker's rounding, which would render 30.5 seconds as ``30s`` where the reader
    renders ``31s``."""
    if math.isnan(value) or math.isinf(value):
        return 0
    return math.floor(value + 0.5)
