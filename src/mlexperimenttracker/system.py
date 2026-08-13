"""Background sampling of host resources into ``system_metrics.json``.

Three properties drive every decision in this module.

It must never fail a run. Resource sampling is a nicety; a training job that dies because
a GPU query returned an unexpected struct is a worse product than one that shows an empty
System tab. Every read is wrapped, every source can disappear mid-run, and a source that
fails repeatedly is dropped rather than retried forever.

It must not require anything to be installed. ``psutil`` and ``pynvml`` are optional
extras, imported lazily and probed once, so importing the SDK in a training script costs
nothing and adds no dependency resolution to the user's environment.

Its output must be homogeneous. The reader decides which table columns to render by
looking at the keys of the **first** sample only (``SystemMetrics.tsx:179-183``), so the
key set is fixed at :meth:`SystemSampler.start` and every later sample carries exactly
those keys — a GPU that appears half way through a run would otherwise be invisible, and
a GPU that vanishes would leave holes in a table that has already been laid out.

The file is strict JSON rather than JSONL (DATA-CONTRACT 3.8), which means the whole
array is rewritten on every tick. That is the reason for the sample cap below.
"""

from __future__ import annotations

import threading
import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from .storage import Storage

__all__ = ["SystemSampler", "DEFAULT_INTERVAL", "DEFAULT_MAX_SAMPLES"]

#: Ten seconds over a twelve-hour run is 4,320 samples — roughly half a megabyte, which
#: is cheap to rewrite. Faster sampling buys nothing: only the first twenty rows are ever
#: displayed and the stat cards are means over the whole array.
DEFAULT_INTERVAL: float = 10.0

#: Above this many samples the history is halved by dropping every second sample, which
#: keeps whole-run coverage at a coarser resolution instead of either truncating the tail
#: or rewriting a file that grows without bound for a week-long run. Sampling itself
#: continues at the configured interval; only the retained history is thinned.
DEFAULT_MAX_SAMPLES: int = 4320

#: After this many consecutive failed ticks the sampler gives up. A transient failure —
#: a driver reload, a momentary permission error — should not stop sampling; a permanent
#: one should not burn a thread and a warning every interval for the rest of the run.
_MAX_CONSECUTIVE_FAILURES: int = 5

_MISSING: Any = object()
_psutil_cache: Any = _MISSING


def _psutil() -> Any:
    """Import ``psutil`` once and remember the answer, including the negative one.

    Repeating a failed import on every tick is measurably expensive — the import system
    walks the whole path each time — and the answer cannot change during a run.
    """
    global _psutil_cache
    if _psutil_cache is _MISSING:
        try:
            import psutil  # noqa: PLC0415 - deliberately lazy, this is an optional extra
        except Exception:  # pragma: no cover - depends on the host environment
            _psutil_cache = None
        else:
            _psutil_cache = psutil
    return _psutil_cache


class _GpuProbe:
    """NVML handles for every visible device, or nothing at all.

    Utilisation is averaged across devices and memory is summed, because the contract has
    one scalar for each and a single-GPU box — the common case — must read exactly right.
    On a multi-GPU box the average is the honest summary of "how busy is the accelerator".
    """

    __slots__ = ("_nvml", "_handles")

    def __init__(self, nvml: Any, handles: list[Any]) -> None:
        self._nvml = nvml
        self._handles = handles

    @classmethod
    def open(cls) -> _GpuProbe | None:
        try:
            import pynvml  # noqa: PLC0415 - optional extra
        except Exception:
            return None
        try:
            pynvml.nvmlInit()
            handles = [
                pynvml.nvmlDeviceGetHandleByIndex(index)
                for index in range(pynvml.nvmlDeviceGetCount())
            ]
        except Exception:
            return None
        if not handles:
            # A machine with the library installed and no device is not a GPU machine;
            # emitting zero-valued GPU columns there would be a lie the UI cannot qualify.
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            return None
        return cls(pynvml, handles)

    def read(self) -> tuple[float, float] | None:
        """``(utilisation percent, memory used MB)``, or ``None`` if any device failed."""
        utilisations: list[float] = []
        memory_mb = 0.0
        for handle in self._handles:
            try:
                utilisations.append(float(self._nvml.nvmlDeviceGetUtilizationRates(handle).gpu))
                memory_mb += float(self._nvml.nvmlDeviceGetMemoryInfo(handle).used) / (1024 * 1024)
            except Exception:
                return None
        if not utilisations:
            return None
        return sum(utilisations) / len(utilisations), memory_mb

    def close(self) -> None:
        try:
            self._nvml.nvmlShutdown()
        except Exception:
            pass


class SystemSampler:
    """Writes Shape A — a JSON array of homogeneous samples — on a daemon thread.

    Daemon, because a sampler must never be the reason a finished training script keeps
    the interpreter alive; the run's terminal-state hook stops it explicitly before exit,
    so the daemon flag only matters for exits that skip every hook.
    """

    def __init__(
        self,
        storage: Storage,
        path: Path,
        *,
        interval: float = DEFAULT_INTERVAL,
        max_samples: int = DEFAULT_MAX_SAMPLES,
        disk_path: str | Path | None = None,
    ) -> None:
        self._storage = storage
        self._path = path
        # Below half a second the sampler costs more than it measures: psutil's own
        # memory and disk reads are syscall-bound and the file rewrite is O(samples).
        self._interval = max(0.5, float(interval))
        self._max_samples = max(2, int(max_samples))
        self._disk_path = str(disk_path) if disk_path is not None else None
        self._samples: list[dict[str, float]] = []
        self._keys: tuple[str, ...] = ()
        self._gpu: _GpuProbe | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._failures = 0

    @property
    def available(self) -> bool:
        """False when nothing on this host can be sampled, which is not an error."""
        return bool(self._keys)

    @property
    def sample_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def start(self) -> bool:
        """Probe, take the first sample synchronously, then run.

        The first sample is taken here rather than after the first interval so that a run
        shorter than one interval still produces a file, and so that the key set — which
        the reader freezes from sample one — is decided before any concurrency exists.
        """
        if self._thread is not None:
            return self.available

        psutil = _psutil()
        self._gpu = _GpuProbe.open()
        keys: list[str] = ["timestamp"]
        if psutil is not None:
            # Primes the internal counter: the first cpu_percent call with no interval
            # always returns 0.0 because it has no previous reading to difference against.
            try:
                psutil.cpu_percent(interval=None)
                keys.extend(
                    ["cpu_percent", "memory_percent", "memory_used_mb", "memory_available_mb"]
                )
                if self._read_disk_percent() is not None:
                    keys.append("disk_usage_percent")
            except Exception:  # pragma: no cover - a psutil that imports but cannot read
                keys = ["timestamp"]
        if self._gpu is not None:
            keys.extend(["gpu_utilization", "gpu_memory_used_mb"])

        if len(keys) <= 1:
            # Only a timestamp is available, which renders an empty table. Say nothing and
            # write nothing rather than leaving a file that means "sampling was on and
            # found nothing" — absent and empty are indistinguishable to the reader anyway.
            self._keys = ()
            self._close_gpu()
            return False

        self._keys = tuple(keys)
        self._tick()
        thread = threading.Thread(
            target=self._loop, name="mlexp-system-metrics", daemon=True
        )
        self._thread = thread
        thread.start()
        return True

    def stop(self, *, join_timeout: float = 2.0) -> None:
        """Stop sampling and flush. Safe to call from a signal handler and twice.

        The join is bounded because this runs on the crash path: a sampler wedged inside a
        driver call must not stop the run from recording its terminal state, and the
        thread is a daemon, so abandoning it is survivable.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)
        self._thread = None
        self._flush()
        self._close_gpu()

    # ----------------------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            if not self._tick():
                return

    def _tick(self) -> bool:
        """One sample plus one whole-file rewrite. Returns False when the sampler gives up."""
        try:
            sample = self._sample()
        except Exception:
            sample = None
        if sample is None:
            self._failures += 1
            if self._failures >= _MAX_CONSECUTIVE_FAILURES:
                self._warn("system metrics sampling stopped after repeated read failures")
                return False
            return True
        self._failures = 0
        with self._lock:
            self._samples.append(sample)
            if len(self._samples) > self._max_samples:
                self._samples = self._samples[::2]
        self._flush()
        return True

    def _sample(self) -> dict[str, float] | None:
        """One sample carrying exactly the frozen key set, or ``None`` if nothing read.

        A field whose source fails on this tick is filled from the previous sample rather
        than omitted, because omitting it would make the array heterogeneous and the
        reader's column layout — taken from sample one — would render a blank cell with no
        explanation. Carrying the last known value is the smaller lie and the visible one.
        """
        if not self._keys:
            return None
        previous = self._samples[-1] if self._samples else {}
        sample: dict[str, float] = {"timestamp": round(time.time(), 3)}
        psutil = _psutil()

        if "cpu_percent" in self._keys and psutil is not None:
            try:
                memory = psutil.virtual_memory()
                sample["cpu_percent"] = round(float(psutil.cpu_percent(interval=None)), 1)
                sample["memory_percent"] = round(float(memory.percent), 1)
                sample["memory_used_mb"] = int(round(float(memory.used) / (1024 * 1024)))
                sample["memory_available_mb"] = int(
                    round(float(memory.available) / (1024 * 1024))
                )
            except Exception:
                for key in ("cpu_percent", "memory_percent", "memory_used_mb", "memory_available_mb"):
                    sample[key] = previous.get(key, 0.0)

        if "disk_usage_percent" in self._keys:
            percent = self._read_disk_percent()
            sample["disk_usage_percent"] = (
                round(percent, 1) if percent is not None
                else previous.get("disk_usage_percent", 0.0)
            )

        if "gpu_utilization" in self._keys:
            reading = self._gpu.read() if self._gpu is not None else None
            if reading is None:
                sample["gpu_utilization"] = previous.get("gpu_utilization", 0.0)
                sample["gpu_memory_used_mb"] = previous.get("gpu_memory_used_mb", 0)
            else:
                utilisation, memory_mb = reading
                sample["gpu_utilization"] = round(utilisation, 1)
                sample["gpu_memory_used_mb"] = int(round(memory_mb))

        return {key: sample[key] for key in self._keys if key in sample}

    def _read_disk_percent(self) -> float | None:
        psutil = _psutil()
        if psutil is None or self._disk_path is None:
            return None
        try:
            return float(psutil.disk_usage(self._disk_path).percent)
        except Exception:
            return None

    def _flush(self) -> None:
        """Whole-file atomic rewrite. Failure here is never allowed to reach the run."""
        with self._lock:
            snapshot = list(self._samples)
        if not snapshot:
            return
        try:
            self._storage.write_json(self._path, snapshot)
        except Exception:
            self._warn("could not write system metrics")

    def _close_gpu(self) -> None:
        if self._gpu is not None:
            self._gpu.close()
            self._gpu = None

    @staticmethod
    def _warn(message: str) -> None:
        # Warnings raise during interpreter shutdown once the warnings machinery has been
        # torn down, and this class is stopped from an atexit hook.
        try:
            warnings.warn(message, stacklevel=3)
        except Exception:  # pragma: no cover - shutdown-only path
            pass
