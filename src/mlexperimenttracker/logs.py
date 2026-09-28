"""Capture a run's stdout/stderr and :mod:`logging` output into ``logs.jsonl``.

Output still reaches the terminal first. Capture never raises (failures are swallowed
silently to avoid recursing through the tee), and a size budget ends the log with a
record saying capture stopped. Stdlib only.
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

# Roughly 80k progress lines.
DEFAULT_MAX_BYTES: int = 8 * 1024 * 1024

# Unterminated text longer than this is recorded anyway instead of buffered forever.
_PENDING_LIMIT: int = 64 * 1024


# --------------------------------------------------------------------------------------
# The writer
# --------------------------------------------------------------------------------------


class LogWriter:
    """Writes one run's log records and enforces its size budget.

    Each record goes through ``Storage.append_log`` (one open per record) so all file
    access stays behind Storage's containment check. ``start_time`` is epoch seconds;
    elapsed time uses the monotonic clock so clock jumps don't reorder lines.
    ``timestamp`` is seconds since run start, same as in metrics.jsonl.
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
        # Usually ~0, but install() can be called on a run that's already going.
        self._elapsed_at_base = max(0.0, time.time() - self._epoch_start)

        # RLock: a Ctrl-C handler can run on the main thread mid-write() and write again.
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
    # State
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
        """Bytes appended so far, as counted against the budget."""
        return self._written

    @property
    def record_count(self) -> int:
        return self._records

    # ----------------------------------------------------------------------------------
    # Writing
    # ----------------------------------------------------------------------------------

    def write(self, message: str, *, level: str = "info", source: str = "user") -> None:
        """Append one record. Never raises.

        ``level`` and ``source`` are normalised (not validated) via ``contract``, since
        this runs inside ``print``.
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
        """Stop accepting records. Idempotent. Nothing to flush; records are written immediately."""
        with self._lock:
            self._closed = True

    # ----------------------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------------------

    def _elapsed(self) -> float:
        return self._elapsed_at_base + (time.monotonic() - self._monotonic_base)

    def _record_truncation(self) -> None:
        """Append a final notice that capture stopped, then stop.

        The notice may exceed the budget. ``source`` is ``user`` because the vocabulary has
        no value for the tracker itself.
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
    """Encoded size of the record as ``append_jsonl`` writes it (exact, not estimated)."""
    try:
        return len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 1
    except (TypeError, ValueError):  # pragma: no cover - every field here is a str or float
        return len(str(record).encode("utf-8", errors="replace")) + 1


# --------------------------------------------------------------------------------------
# The tee
# --------------------------------------------------------------------------------------


class StreamTee:
    """Wraps ``sys.stdout``/``sys.stderr``: writes to the real stream first, then records.

    One record per line. Text before the last ``\\r`` is dropped, like a terminal would, so
    progress bars don't produce a record per repaint. Other attributes delegate to the real
    stream. Writes straight to the file descriptor (C extensions, subprocesses) aren't seen.
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
        """The stream this tee replaced."""
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
        # Some streams (test doubles) return None; print expects a char count.
        return len(data) if hasattr(data, "__len__") else 0

    def writelines(self, lines: Any) -> None:
        """Not delegated, or the lines would skip the log."""
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        """Flush the real stream only. The pending line is kept, since progress bars flush a lot."""
        self._stream.flush()

    def flush_pending(self) -> None:
        """Record the buffered partial line and keep capturing.

        Called before out-of-band writes like a traceback so the log stays in order.
        """
        with self._lock:
            pending, self._pending = self._pending, ""
        if pending:
            self._emit(pending)

    def detach(self) -> None:
        """Stop capturing after recording any buffered line. Idempotent. Leaves the stream open."""
        with self._lock:
            if self._detached:
                return
            self._detached = True
        self.flush_pending()

    def close(self) -> None:
        """Detach, then close the wrapped stream."""
        self.detach()
        self._stream.close()

    def isatty(self) -> bool:
        """False, instead of raising, when the wrapped stream has no working isatty()."""
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
            # Leftover has no newline. Drop text overwritten by \r, then cap its size.
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
    """Whatever was written, as text. Bytes are decoded with replacement, never raising."""
    if isinstance(data, str):
        return data
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data).decode(encoding or "utf-8", errors="replace")
    return str(data)


def _visible(line: str) -> str:
    """A finished line as a terminal would show it (trailing CR from CRLF ignored)."""
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

    Needed because logging handlers keep the stream they were configured with, so the tee
    doesn't see them.
    """

    def __init__(self, writer: LogWriter, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self._writer = writer

    def emit(self, record: logging.LogRecord) -> None:
        """Format and record. Errors are swallowed, not sent to handleError.

        handleError prints to stderr, which may be our own tee, so it would loop.
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
    """Map a numeric level to the five level names, rounding down (custom 25 -> info)."""
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
    """Tracks what install() changed so uninstall() can undo exactly that."""

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
        """Undo the installation. Idempotent and never raises.

        Called from finish(), the excepthook, signal handler and atexit, so it can run more
        than once. Each step is guarded separately so sys.stdout always gets restored.
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
                # Only if nothing else changed it since (e.g. a later basicConfig()).
                if self._restore_level is not None and root.level == logging.INFO:
                    root.setLevel(self._restore_level)
            except Exception:  # noqa: BLE001
                pass

        try:
            self._writer.close()
        except Exception:  # noqa: BLE001
            pass


def _restore_stream(tee: StreamTee) -> None:
    """Put back the original stream if the tee is still installed, then detach.

    If something else has wrapped sys.stdout since, leave it alone rather than clobber it.
    """
    if getattr(sys, "stdout", None) is tee:
        sys.stdout = tee.stream
    elif getattr(sys, "stderr", None) is tee:
        sys.stderr = tee.stream
    tee.detach()


# One installation per live run, keyed by id(run).
_REGISTRY: dict[int, _Installation] = {}

_INSTALL_LOCK = threading.RLock()


def install(run: Any, *, capture_output: bool, capture_logging: bool) -> Callable[[], None]:
    """Start capture for ``run`` and return a callable that stops it.

    Idempotent per run: a second call returns the same uninstall callable instead of
    wrapping the streams twice. ``capture_logging`` also sets the root logger to INFO, but
    only if logging is unconfigured (no handlers, level WARNING); otherwise logging.info()
    would be dropped. The level is restored on uninstall.
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
                    # pythonw and some embedded interpreters have no stdout/stderr.
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
    """Record any partial line the run's tees are holding.

    ``Run`` calls this before writing a traceback so the last output lands before it.
    """
    installation = _REGISTRY.get(id(run))
    if installation is not None:
        installation.flush()


def _writer_for(run: Any) -> LogWriter:
    """The run's writer, created here if the run doesn't have one (e.g. in tests)."""
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
