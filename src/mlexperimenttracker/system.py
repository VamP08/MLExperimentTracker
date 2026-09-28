"""Background sampling of host resources into ``system_metrics.json``.

Never fails the run: every read is wrapped and repeatedly failing sources are dropped.
``psutil``/``pynvml`` are optional and imported lazily. The key set is fixed at start,
since the UI picks table columns from the first sample. The file is a JSON array that's
rewritten every tick, hence the sample cap.
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

# 12h at 10s is 4320 samples, about half a MB. The UI only shows the first 20 rows and
# whole-array means, so faster sampling buys nothing.
DEFAULT_INTERVAL: float = 10.0

# Past this, every second sample is dropped: coarser history but still whole-run coverage.
DEFAULT_MAX_SAMPLES: int = 4320

# Consecutive failed ticks before giving up. Tolerates transient driver/permission errors.
_MAX_CONSECUTIVE_FAILURES: int = 5

_MISSING: Any = object()
_psutil_cache: Any = _MISSING


def _psutil() -> Any:
    """Import ``psutil`` once and cache the result, including failure."""
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
    """NVML handles for every visible GPU.

    The format has one value each, so utilisation is averaged and memory summed.
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
            # Library but no device: don't emit zero-valued GPU columns.
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
    """Writes a JSON array of same-keyed samples from a daemon thread.

    Daemon so it never keeps the interpreter alive; finish() normally stops it first.
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
        # Below 0.5s the sampling and O(n) rewrite cost more than they measure.
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
        """False when nothing on this host can be sampled."""
        return bool(self._keys)

    @property
    def sample_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def start(self) -> bool:
        """Probe sources, take the first sample synchronously, then start the thread.

        Sampling once up front means short runs still get a file and the key set is fixed
        before the thread starts. Returns False if nothing can be sampled.
        """
        if self._thread is not None:
            return self.available

        psutil = _psutil()
        self._gpu = _GpuProbe.open()
        keys: list[str] = ["timestamp"]
        if psutil is not None:
            # Prime cpu_percent; its first call with no interval always returns 0.0.
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
            # Timestamp only would render an empty table, so write nothing.
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
        """Stop sampling and flush. Safe to call twice or from a signal handler.

        The join is bounded so a thread stuck in a driver call can't block finish().
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
        """One sample with exactly the frozen key set, or ``None``.

        A source that fails this tick reuses the previous value so every sample keeps the
        same keys.
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
        """Atomic whole-file rewrite. Errors only warn."""
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
        # warnings.warn can raise during shutdown, and this runs from atexit.
        try:
            warnings.warn(message, stacklevel=3)
        except Exception:  # pragma: no cover - shutdown-only path
            pass
