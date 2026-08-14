"""Capture of a training run's output into ``logs.jsonl``.

The format had no log destination at all until now (DATA-CONTRACT §7), which made the one
question a user asks of a failed run — *what did it print before it died?* — unanswerable
by the product that recorded it. This module is the writer half of closing that: a size-
bounded record writer, a tee that wraps ``sys.stdout``/``sys.stderr`` without taking them
away from the terminal, a :mod:`logging` handler, and one installer that arms all three
reversibly.

Three rules shape every line below, and each one is a way this module could ruin the run it
is attached to rather than merely fail to record it.

**It must still print.** A tracker that swallows a user's terminal output has broken their
program to write a file they cannot see yet. Every teed write goes to the original stream
first and is captured second, so the worst a capture failure can cost is the record.

**It must never raise.** ``print()`` is not a call anyone expects to fail, and a tracker
that turns it into one is a tracker that ends training runs. Every capture path here
swallows, and swallows silently: reporting a logging failure through :mod:`logging` or
``stderr`` while sitting inside the very handler and stream that failed is how a tracker
recurses into a stack overflow.

**It must be bounded.** A training loop printing one line per batch writes gigabytes over a
weekend, into a directory the user believes holds metadata. The budget is enforced with a
final record that says capture stopped and why — a log that silently ends part-way through
is worse than no log, because the reader believes it is looking at the end of the run.

Standard library only, like the rest of the SDK half.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from typing import Any, Callable

from .contract import normalise_log_level, normalise_log_source

__all__ = [
    "DEFAULT_MAX_BYTES",
    "LogWriter",
    "RunLogHandler",
    "StreamTee",
    "flush_pending",
    "install",
]

#: 8 MiB. Enough for a long run's worth of honest progress lines — roughly eighty thousand
#: — and small enough that a runaway loop costs a directory listing rather than a disk.
DEFAULT_MAX_BYTES: int = 8 * 1024 * 1024

#: How much text may accumulate with no newline in it before it is recorded anyway. A
#: program that writes a megabyte and never terminates the line is not going to, and
#: holding it all in memory to preserve a line boundary that never arrives is the wrong
#: trade.
_PENDING_LIMIT: int = 64 * 1024


# --------------------------------------------------------------------------------------
# The writer
# --------------------------------------------------------------------------------------


class LogWriter:
    """Owns one run's log destination and its size budget.

    Records go through :meth:`Storage.append_log` rather than through a file handle this
    object keeps open, which is a deliberate departure from the obvious design: every
    filesystem access in this package goes through ``Storage``, and that rule is what makes
    the containment check on user-supplied names impossible to forget. The cost is one
    ``open``/``close`` per record, which is what ``metrics.jsonl`` already pays per step and
    is dominated by the write itself.

    ``start_time`` is the run's start as **epoch seconds** — the same number
    ``absolute_timestamp`` is measured in. Elapsed time is then derived from the monotonic
    clock and anchored to it, so a mid-run NTP step cannot make a log line appear before the
    run started, and ``timestamp`` means exactly what it means in ``metrics.jsonl``:
    seconds since the run began (DATA-CONTRACT §4.4).
    """

    def __init__(
        self,
        storage: Any,
        project: str,
        run_id: str,
        *,
        start_time: float,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._storage = storage
        self._project = project
        self._run_id = run_id
        self._max_bytes = max(0, int(max_bytes))

        self._epoch_start = float(start_time)
        self._monotonic_base = time.monotonic()
        # Normally zero: the writer is built a few statements after the run starts. It is
        # not assumed to be, because `install()` may be called on a run that is already
        # under way, and a negative elapsed is not a thing that can have happened.
        self._elapsed_at_base = max(0.0, time.time() - self._epoch_start)

        # An RLock rather than a Lock, and for the same reason `run.py` keeps its active-run
        # registry lock-free: a signal handler runs on the main thread between bytecodes, so
        # a Ctrl-C landing inside `write()` would deadlock a non-reentrant lock against the
        # terminal-state write the handler exists to perform.
        self._lock = threading.RLock()
        self._written = 0
        self._records = 0
        self._truncated = False
        self._closed = False

    def __repr__(self) -> str:
        return (
            f"LogWriter(run={self._run_id!r}, records={self._records}, "
            f"bytes={self._written}, truncated={self._truncated})"
        )

    # ----------------------------------------------------------------------------------
    # State, for callers that want to assert on it
    # ----------------------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def truncated(self) -> bool:
        """True once the budget was reached and capture stopped."""
        return self._truncated

    @property
    def bytes_written(self) -> int:
        """Bytes of JSONL this writer has appended, as counted against the budget."""
        return self._written

    @property
    def record_count(self) -> int:
        return self._records

    # ----------------------------------------------------------------------------------
    # Writing
    # ----------------------------------------------------------------------------------

    def write(self, message: str, *, level: str = "info", source: str = "user") -> None:
        """Append one record. Silent about every failure, by design.

        ``level`` and ``source`` are normalised into the closed vocabularies in
        ``contract`` rather than validated: this is called from inside ``print``, and
        raising a ``ValueError`` at a user's logging call because a level was spelled
        ``WARN`` would be the tracker breaking the program it is measuring.
        """
        if not isinstance(message, str):
            message = "" if message is None else str(message)

        try:
            with self._lock:
                if self._closed or self._truncated:
                    return

                elapsed = self._elapsed()
                record = {
                    "timestamp": round(elapsed, 3),
                    "absolute_timestamp": round(self._epoch_start + elapsed, 3),
                    "level": normalise_log_level(level),
                    "message": message,
                    "source": normalise_log_source(source),
                }

                size = _encoded_size(record)
                if self._written + size > self._max_bytes:
                    self._record_truncation()
                    return
                if self._storage.append_log(self._project, self._run_id, record):
                    self._written += size
                    self._records += 1
        except Exception:  # noqa: BLE001 - a capture path may not raise into a training run
            pass

    def close(self) -> None:
        """Stop accepting records. Idempotent, and never raises.

        There is nothing to flush: each record is appended as it arrives, so a run killed
        between two ``print`` calls keeps everything up to the first of them. The buffering
        that does exist lives in :class:`StreamTee`, which flushes into this writer before
        this is called.
        """
        with self._lock:
            self._closed = True

    # ----------------------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------------------

    def _elapsed(self) -> float:
        return self._elapsed_at_base + (time.monotonic() - self._monotonic_base)

    def _record_truncation(self) -> None:
        """Append the one record that says capture stopped, and stop.

        Written past the budget on purpose — the budget bounds the output, and one more
        line is the price of the file admitting what it is. ``source`` is ``user`` because
        the vocabulary has no value for "the tracker itself", and widening a closed enum
        that a reader maps exactly would cost more than the imprecision does.
        """
        self._truncated = True
        notice = {
            "timestamp": round(self._elapsed(), 3),
            "absolute_timestamp": round(self._epoch_start + self._elapsed(), 3),
            "level": "warning",
            "message": (
                f"log capture stopped after {self._records} records "
                f"({self._written} bytes): this run's {self._max_bytes}-byte log budget "
                "was reached and later output is not recorded. Raise it with "
                "met.init(log_limit_bytes=...) or turn capture off with "
                "capture_output=False."
            ),
            "source": "user",
        }
        if self._storage.append_log(self._project, self._run_id, notice):
            self._written += _encoded_size(notice)
            self._records += 1


def _encoded_size(record: dict) -> int:
    """Bytes this record will occupy, counted the way ``append_jsonl`` writes it.

    Re-encoding here costs one ``json.dumps`` per record that the storage layer then
    repeats. The alternative — estimating from the message length — drifts on every
    non-ASCII character and on every escape, and a budget that is wrong in the direction of
    "larger than you asked for" is not a budget.
    """
    try:
        return len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 1
    except (TypeError, ValueError):  # pragma: no cover - every field here is a str or float
        return len(str(record).encode("utf-8", errors="replace")) + 1


# --------------------------------------------------------------------------------------
# The tee
# --------------------------------------------------------------------------------------


class StreamTee:
    """A stand-in for ``sys.stdout`` / ``sys.stderr`` that writes to both.

    The original stream is written to **first**, so a user watching a terminal sees their
    output at the moment they would have without the tracker, and a capture that fails
    costs the record rather than the print.

    Text is buffered until a newline, because a record is a line: ``print("a", end="")``
    followed by ``print("b")`` is one line of output and must be one record, not two.
    Carriage returns are treated the way a terminal treats them — everything before the
    last ``\\r`` in a pending line has been overwritten on screen and is dropped — which is
    what keeps a progress bar from writing a record per repaint, or from growing the buffer
    without bound.

    Attribute lookups that are not defined here fall through to the wrapped stream, so
    ``isatty()``, ``encoding``, ``fileno()`` and ``buffer`` answer for the real stream and
    a progress bar still believes it is talking to a terminal. Note the consequence of
    ``fileno()`` being honest: anything writing to the file descriptor directly — a C
    extension, a subprocess inheriting the handle — bypasses this object entirely and is
    not captured. That is the same boundary every in-process tee has.
    """

    def __init__(
        self,
        stream: Any,
        writer: LogWriter,
        source: str = "stdout",
        *,
        level: str = "info",
    ) -> None:
        self._stream = stream
        self._writer = writer
        self._source = source
        self._level = level
        self._pending = ""
        self._detached = False
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return f"StreamTee(source={self._source!r}, stream={self._stream!r})"

    @property
    def stream(self) -> Any:
        """The object this tee replaced. Restoring it is the whole of uninstallation."""
        return self._stream

    @property
    def source(self) -> str:
        return self._source

    # ----------------------------------------------------------------------------------
    # The file protocol, as much of it as matters
    # ----------------------------------------------------------------------------------

    def write(self, data: Any) -> int:
        written = self._stream.write(data)
        try:
            self._capture(data)
        except Exception:  # noqa: BLE001 - printing may not fail because recording did
            pass
        if isinstance(written, int):
            return written
        # A stream that returns None from write() — several test doubles do — still has to
        # give `print` a number back, and the number it expects is a character count.
        return len(data) if hasattr(data, "__len__") else 0

    def writelines(self, lines: Any) -> None:
        """Implemented rather than delegated: a delegated ``writelines`` would reach the
        terminal and never reach the log, which is the exact failure this class exists to
        prevent."""
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        """Passes through, and deliberately does **not** flush the pending line.

        A progress bar flushes after every repaint. Treating a flush as a line boundary
        would turn one line of output into a record per frame, which is both the largest
        volume of junk this module could produce and a misreading of what a flush means: it
        is about bytes reaching a device, not about a line being finished.
        """
        self._stream.flush()

    def flush_pending(self) -> None:
        """Record the partial line held in the buffer, and keep capturing.

        Called before anything else is written to the same run out of band — a traceback,
        say — so that the file reads in the order the events happened. A ``print`` with no
        newline followed by a crash is exactly that case, and it is the last thing many
        failed runs printed.
        """
        with self._lock:
            pending, self._pending = self._pending, ""
        if pending:
            self._emit(pending)

    def detach(self) -> None:
        """Stop capturing, recording whatever partial line is buffered. Idempotent.

        This is what uninstallation calls. It does not touch the wrapped stream — closing a
        user's ``stdout`` because a run ended would be a spectacular overreach.
        """
        with self._lock:
            if self._detached:
                return
            self._detached = True
        self.flush_pending()

    def close(self) -> None:
        """Detach, then close the wrapped stream — because code that closes ``sys.stdout``
        means the real one, not this wrapper."""
        self.detach()
        self._stream.close()

    def isatty(self) -> bool:
        """Defined rather than delegated only so that a stream without the method — a
        ``StringIO`` from a test harness, a null device stand-in — answers ``False``
        instead of raising into the caller's terminal detection."""
        isatty = getattr(self._stream, "isatty", None)
        if isatty is None:
            return False
        try:
            return bool(isatty())
        except Exception:  # noqa: BLE001 - a detached stream may refuse the question
            return False

    def __getattr__(self, name: str) -> Any:
        try:
            stream = self.__dict__["_stream"]
        except KeyError:  # pragma: no cover - only reachable before __init__ finishes
            raise AttributeError(name) from None
        return getattr(stream, name)

    # ----------------------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------------------

    def _capture(self, data: Any) -> None:
        if self._detached:
            return
        text = _as_text(data, getattr(self._stream, "encoding", None))
        if not text:
            return

        lines: list[str] = []
        with self._lock:
            if self._detached:
                return
            buffer = self._pending + text
            *complete, buffer = buffer.split("\n")
            lines.extend(complete)
            # What is left has no newline in it. Collapse anything a carriage return
            # overwrote, then bound what remains: a line that never ends must not become a
            # memory leak that grows for the length of the run.
            buffer = _after_last_return(buffer)
            if len(buffer) > _PENDING_LIMIT:
                lines.append(buffer)
                buffer = ""
            self._pending = buffer

        for line in lines:
            self._emit(_visible(line))

    def _emit(self, line: str) -> None:
        self._writer.write(line, level=self._level, source=self._source)


def _as_text(data: Any, encoding: str | None) -> str:
    """Whatever was written, as text.

    Bytes reach a text stream more often than they should — a library writing through
    ``sys.stdout`` instead of ``sys.stdout.buffer``, a payload that was never decoded — and
    a tee that raised on them would convert somebody else's sloppiness into a crash inside
    ``print``. Undecodable bytes become replacement characters, because a record with a
    mangled character in it is worth more than no record.
    """
    if isinstance(data, str):
        return data
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data).decode(encoding or "utf-8", errors="replace")
    return str(data)


def _visible(line: str) -> str:
    """A completed line as the terminal would have shown it.

    A trailing ``\\r`` is the CRLF half and carries no meaning; any earlier one means the
    text before it was overwritten in place.
    """
    if line.endswith("\r"):
        line = line[:-1]
    return _after_last_return(line)


def _after_last_return(text: str) -> str:
    index = text.rfind("\r")
    return text if index < 0 else text[index + 1 :]


# --------------------------------------------------------------------------------------
# The logging handler
# --------------------------------------------------------------------------------------


class RunLogHandler(logging.Handler):
    """Routes :mod:`logging` records into the run.

    Most training scripts report progress with ``logging.info`` rather than ``print``, and
    a handler is the only way to see those: the logging machinery writes through its own
    handlers' streams, which it captured at configuration time, so a tee installed
    afterwards never sees them.
    """

    def __init__(self, writer: LogWriter, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self._writer = writer

    def emit(self, record: logging.LogRecord) -> None:
        """Formats and records. Swallows, and does not call :meth:`handleError`.

        The default error handler prints to ``sys.stderr`` — which, with output capture on,
        is a tee feeding this same writer. A failure that reported itself that way would be
        a loop, and the first thing it would consume is the budget.
        """
        try:
            self._writer.write(
                self.format(record),
                level=_level_name(record.levelno),
                source="logging",
            )
        except Exception:  # noqa: BLE001 - see the docstring
            pass


def _level_name(levelno: int) -> str:
    """Map a numeric level onto the five-word vocabulary, rounding **down**.

    A custom level of 25 — the ``SUCCESS`` or ``NOTICE`` that half the logging recipes on
    the internet install — is recorded as ``info`` rather than ``warning``: filing an
    application's private level as more severe than it is puts noise in the bucket a user
    searches when something has gone wrong.
    """
    if levelno >= logging.CRITICAL:
        return "critical"
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warning"
    if levelno >= logging.INFO:
        return "info"
    return "debug"


# --------------------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------------------


class _Installation:
    """What was changed, so that exactly that much can be changed back."""

    __slots__ = ("_done", "_handler", "_lock", "_restore_level", "_run_key", "_tees", "_writer")

    def __init__(self, run_key: int, writer: LogWriter) -> None:
        self._run_key = run_key
        self._writer = writer
        self._tees: list[StreamTee] = []
        self._handler: RunLogHandler | None = None
        self._restore_level: int | None = None
        self._done = False
        self._lock = threading.RLock()

    def add_tee(self, tee: StreamTee) -> None:
        self._tees.append(tee)

    def flush(self) -> None:
        for tee in self._tees:
            try:
                tee.flush_pending()
            except Exception:  # noqa: BLE001 - a flush may not raise into an exit path
                pass

    def set_handler(self, handler: RunLogHandler, restore_level: int | None) -> None:
        self._handler = handler
        self._restore_level = restore_level

    def uninstall(self) -> None:
        """Undo the installation. Idempotent, ordered, and it may not raise.

        It is called from ``finish()``, from an excepthook, from a signal handler and from
        ``atexit``, at least two of which fire for a single Ctrl-C. Every step is
        independently guarded so that a failure in one still lets the others run — leaving
        ``sys.stdout`` replaced after a run has finished is the worst outcome this module
        has, because every later ``print`` in the process would then be writing into a
        directory that belongs to a run that ended.
        """
        with self._lock:
            if self._done:
                return
            self._done = True
        _REGISTRY.pop(self._run_key, None)

        for tee in self._tees:
            try:
                _restore_stream(tee)
            except Exception:  # noqa: BLE001 - never raise out of an exit path
                pass

        if self._handler is not None:
            root = logging.getLogger()
            try:
                root.removeHandler(self._handler)
                # Only if nothing else has moved it since: restoring a level a later
                # basicConfig() chose would silence the program on the way out.
                if self._restore_level is not None and root.level == logging.INFO:
                    root.setLevel(self._restore_level)
            except Exception:  # noqa: BLE001
                pass

        try:
            self._writer.close()
        except Exception:  # noqa: BLE001
            pass


def _restore_stream(tee: StreamTee) -> None:
    """Put back the object the tee replaced, if the tee is still the one installed.

    The identity check is the whole rule. If something else has since replaced
    ``sys.stdout`` — a second tracker, a capture library, a notebook kernel — then this
    tee is no longer what the process is printing through, and assigning our saved original
    over the top would delete somebody else's wrapper and their output with it. Detaching
    is still correct in that case: the tee stops recording into a finished run either way.
    """
    if getattr(sys, "stdout", None) is tee:
        sys.stdout = tee.stream
    elif getattr(sys, "stderr", None) is tee:
        sys.stderr = tee.stream
    tee.detach()


#: One installation per live run, keyed by identity. Small, mutated under the installation
#: lock, and emptied by ``uninstall``.
_REGISTRY: dict[int, _Installation] = {}

_INSTALL_LOCK = threading.RLock()


def install(run: Any, *, capture_output: bool, capture_logging: bool) -> Callable[[], None]:
    """Arm capture for ``run`` and return the callable that disarms it.

    Idempotent per run: a second call for a run that is already installed returns the same
    uninstall callable rather than wrapping the streams twice. That matters because a
    double wrap is not merely wasteful — it records every line twice and, if the two
    installations are unwound out of order, restores a stale ``sys.stdout``.

    Two sequential runs in one process are the common case and are clean: the first
    restores the exact objects it replaced before the second wraps them.

    ``capture_logging`` also raises the root logger's level to ``INFO``, but **only when
    logging is unconfigured** — no handlers and the default ``WARNING`` level. Without
    that, the most common line in a training script, ``logging.info(...)``, is discarded by
    the logging machinery before any handler sees it, and capture would silently record
    nothing. When the program *has* configured logging it has said what it wants recorded,
    and this leaves that alone; the level is restored on uninstall if nothing else has
    changed it since.
    """
    key = id(run)
    with _INSTALL_LOCK:
        existing = _REGISTRY.get(key)
        if existing is not None:
            return existing.uninstall

        writer = _writer_for(run)
        installation = _Installation(key, writer)

        if capture_output:
            for name in ("stdout", "stderr"):
                stream = getattr(sys, name, None)
                if stream is None or not hasattr(stream, "write"):
                    # `pythonw` and some embedded interpreters hand you None here. Nothing
                    # is printing, so there is nothing to tee.
                    continue
                level = "info" if name == "stdout" else "warning"
                tee = StreamTee(stream, writer, source=name, level=level)
                setattr(sys, name, tee)
                installation.add_tee(tee)

        if capture_logging:
            root = logging.getLogger()
            restore_level: int | None = None
            if root.level == logging.WARNING and not root.handlers:
                restore_level = root.level
                root.setLevel(logging.INFO)
            handler = RunLogHandler(writer)
            installation.set_handler(handler, restore_level)
            root.addHandler(handler)

        _REGISTRY[key] = installation
        return installation.uninstall


def flush_pending(run: Any) -> None:
    """Record whatever partial line the run's teed streams are holding.

    A ``print`` without a newline is not a record yet, which is right while the program is
    still writing the line and wrong the moment something else writes to the same file.
    ``Run`` calls this before recording a traceback so that the last thing the program
    printed appears before the thing that killed it, rather than after it.
    """
    installation = _REGISTRY.get(id(run))
    if installation is not None:
        installation.flush()


def _writer_for(run: Any) -> LogWriter:
    """The run's writer, built here if the run has not built one.

    ``Run`` constructs its own so that ``log_text()`` works with both capture flags off;
    this branch exists for a caller that installs capture on a run object directly, which
    is what a test does and what a future ``attach to a running job`` would do.
    """
    writer = getattr(run, "_log_writer", None)
    if isinstance(writer, LogWriter):
        return writer
    writer = LogWriter(
        run._storage,
        run.project,
        run.id,
        start_time=getattr(run, "_epoch_start", time.time()),
        max_bytes=getattr(run, "_log_limit_bytes", DEFAULT_MAX_BYTES),
    )
    run._log_writer = writer
    return writer
