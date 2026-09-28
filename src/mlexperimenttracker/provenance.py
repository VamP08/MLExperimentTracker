"""Snapshot of git state, uncommitted diff, packages, hardware, env and datasets at run start.

Used later by ``verify`` (what drifted) and ``replay`` (what to restore). Capture never
raises; failures become ``available=False`` plus a ``reason``. Stdlib only (pynvml optional).
Env vars come from an allowlist, and the diff is size-capped since it may contain secrets.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

from .contract import PATCH_FILE

__all__ = [
    "DEFAULT_DIFF_LIMIT",
    "ENV_ALLOWLIST",
    "GitState",
    "MAX_UNTRACKED",
    "Provenance",
    "REDACTED",
    "capture",
    "collect_environment",
    "collect_hardware",
    "collect_packages",
    "git_state",
    "hash_path",
]

# --------------------------------------------------------------------------------------
# Limits and policy
# --------------------------------------------------------------------------------------

# Patches above this are cut, and the cut is recorded in the manifest.
DEFAULT_DIFF_LIMIT: int = 1024 * 1024

# A huge untracked list usually means a missing .gitignore, so cap it.
MAX_UNTRACKED: int = 200

# Only these env vars are recorded. Allowlist, not denylist, so a new var can't leak a
# secret. Each one affects how training behaves.
ENV_ALLOWLIST: tuple[str, ...] = (
    "CUDA_VISIBLE_DEVICES",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "PYTHONHASHSEED",
    "TOKENIZERS_PARALLELISM",
    "WORLD_SIZE",
    "RANK",
    "LOCAL_RANK",
    "MASTER_ADDR",
    "MASTER_PORT",
    "SLURM_JOB_ID",
    "SLURM_PROCID",
    "CONDA_DEFAULT_ENV",
    "VIRTUAL_ENV",
)

# Replaces values whose name looks like a credential. Nothing in the allowlist matches
# today; it's a guard for future additions.
REDACTED: str = "<redacted>"

_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)

# Metadata reads should be quick. The diff walks the whole worktree, so it gets longer.
_GIT_TIMEOUT: float = 15.0
_GIT_DIFF_TIMEOUT: float = 60.0

_T = TypeVar("_T")


# --------------------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GitState:
    """The ``git`` block of the manifest.

    ``available`` is false if the repo couldn't be inspected. ``reason`` explains that, and
    is also set on partial captures (no commits yet, diff truncated, git diff failed).
    """

    available: bool = False
    reason: str | None = None
    commit: str | None = None
    branch: str | None = None
    remote: str | None = None
    dirty: bool = False
    untracked: tuple[str, ...] = ()
    # True when the untracked list hit MAX_UNTRACKED.
    untracked_truncated: bool = False
    diff_file: str | None = None
    diff_sha256: str | None = None
    diff_bytes: int = 0
    diff_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "commit": self.commit,
            "branch": self.branch,
            "remote": self.remote,
            "dirty": self.dirty,
            "untracked": list(self.untracked),
            "untracked_truncated": self.untracked_truncated,
            "diff_file": self.diff_file,
            "diff_sha256": self.diff_sha256,
            "diff_bytes": self.diff_bytes,
            "diff_truncated": self.diff_truncated,
        }

    @classmethod
    def from_dict(cls, data: Any) -> GitState:
        """Rebuild from disk. Fields are coerced, not trusted, since the file may be edited."""
        if not isinstance(data, dict):
            return cls()
        untracked = data.get("untracked")
        return cls(
            available=bool(data.get("available")),
            reason=_as_str(data.get("reason")),
            commit=_as_str(data.get("commit")),
            branch=_as_str(data.get("branch")),
            remote=_as_str(data.get("remote")),
            dirty=bool(data.get("dirty")),
            untracked=tuple(str(p) for p in untracked) if isinstance(untracked, list) else (),
            untracked_truncated=bool(data.get("untracked_truncated")),
            diff_file=_as_str(data.get("diff_file")),
            diff_sha256=_as_str(data.get("diff_sha256")),
            diff_bytes=_as_int(data.get("diff_bytes")),
            diff_truncated=bool(data.get("diff_truncated")),
        )


@dataclass(frozen=True)
class Provenance:
    """The whole ``provenance.json`` manifest. Blocks other than git are plain dicts."""

    captured_at: str
    git: GitState = field(default_factory=GitState)
    python: dict[str, Any] = field(default_factory=dict)
    platform: dict[str, Any] = field(default_factory=dict)
    packages: dict[str, str] = field(default_factory=dict)
    hardware: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    datasets: tuple[dict[str, Any], ...] = ()
    command: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "git": self.git.to_dict(),
            "python": dict(self.python),
            "platform": dict(self.platform),
            "packages": dict(self.packages),
            "hardware": dict(self.hardware),
            "environment": dict(self.environment),
            "datasets": [dict(entry) for entry in self.datasets],
            "command": dict(self.command),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Provenance:
        """Inverse of :meth:`to_dict`, tolerant of bad input."""
        if not isinstance(data, dict):
            data = {}
        datasets = data.get("datasets")
        return cls(
            captured_at=_as_str(data.get("captured_at")) or "",
            git=GitState.from_dict(data.get("git")),
            python=_as_dict(data.get("python")),
            platform=_as_dict(data.get("platform")),
            packages={str(k): str(v) for k, v in _as_dict(data.get("packages")).items()},
            hardware=_as_dict(data.get("hardware")),
            environment={str(k): str(v) for k, v in _as_dict(data.get("environment")).items()},
            datasets=tuple(entry for entry in datasets if isinstance(entry, dict))
            if isinstance(datasets, list)
            else (),
            command=_as_dict(data.get("command")),
        )


# --------------------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------------------


def capture(
    cwd: str | Path | None = None,
    *,
    capture_diff: bool = True,
    diff_limit: int = DEFAULT_DIFF_LIMIT,
    datasets: Sequence[dict] | None = None,
    command: dict | None = None,
) -> tuple[Provenance, bytes | None]:
    """Capture the manifest and the raw patch bytes. Never raises.

    Returns ``(provenance, patch)``. ``patch`` is None if the tree is clean, diff capture
    is off, or the diff failed. The caller (storage) writes it. ``datasets`` are entries
    from :func:`hash_path`. ``command`` defaults to the current argv and cwd.
    """
    try:
        state, patch = git_state(cwd, capture_diff=capture_diff, diff_limit=diff_limit)
    except Exception as exc:  # pragma: no cover - git_state already swallows everything
        state, patch = GitState(available=False, reason=_describe(exc)), None

    try:
        entries = tuple(dict(entry) for entry in (datasets or ()) if isinstance(entry, dict))
    except Exception as exc:  # pragma: no cover - defensive: datasets is caller-supplied
        entries = ()
        state = _with_reason(state, f"datasets could not be recorded: {_describe(exc)}")

    return (
        Provenance(
            captured_at=_safe(_now_iso, ""),
            git=state,
            python=_safe(_collect_python, {}),
            platform=_safe(_collect_platform, {}),
            packages=_safe(collect_packages, {}),
            hardware=_safe(collect_hardware, {"cpu_count": None, "gpus": []}),
            environment=_safe(collect_environment, {}),
            datasets=entries,
            command=command if command is not None else _safe(_collect_command, {}),
        ),
        patch,
    )


def hash_path(path: str | Path, *, algorithm: str = "sha256", chunk: int = 1 << 20) -> dict:
    """Content hash of a file or directory tree, as a ``datasets`` entry.

    Directories hash each file's relative path, size and bytes in sorted order, so renames
    change the hash. Symlinks are skipped. Bad paths give an entry with ``error``, no raise.
    """
    entry: dict[str, Any] = {
        "path": str(path),
        "algorithm": algorithm,
        "hashed_at": _safe(_now_iso, ""),
    }
    try:
        digest = hashlib.new(algorithm)
    except Exception as exc:
        entry["error"] = f"unsupported hash algorithm {algorithm!r}: {_describe(exc)}"
        return entry

    read_size = max(1, int(chunk))
    target = Path(path)
    try:
        if target.is_dir():
            total_bytes, file_count = _hash_directory(target, digest, read_size)
        elif target.is_file():
            total_bytes = _hash_file(target, digest, read_size)
            file_count = 1
        else:
            entry["error"] = f"path does not exist or is not a regular file: {target}"
            return entry
    except OSError as exc:
        entry["error"] = f"could not read {target}: {_describe(exc)}"
        return entry
    except Exception as exc:  # pragma: no cover - defensive
        entry["error"] = f"could not hash {target}: {_describe(exc)}"
        return entry

    hexdigest = digest.hexdigest()
    if algorithm == "sha256":
        entry["sha256"] = hexdigest
    # `digest` is always set; `sha256` only when that's actually the algorithm.
    entry["digest"] = hexdigest
    entry["bytes"] = total_bytes
    entry["files"] = file_count
    return entry


def collect_packages() -> dict[str, str]:
    """Installed distributions and versions, sorted by lowercased name.

    Uses importlib.metadata so it sees this interpreter's env, not whatever pip is on PATH.
    First occurrence of a name wins, matching import order. Not a lockfile.
    """
    packages: dict[str, str] = {}
    try:
        from importlib.metadata import distributions
    except Exception:  # pragma: no cover - importlib.metadata is stdlib since 3.8
        return packages
    try:
        found = list(distributions())
    except Exception:  # pragma: no cover - a broken sys.path entry can fail the scan
        return packages
    for dist in found:
        try:
            name = dist.metadata["Name"]
            version = dist.version
        except Exception:
            continue
        if not name or name in packages:
            continue
        packages[str(name)] = str(version or "")
    return dict(sorted(packages.items(), key=lambda item: item[0].lower()))


def collect_environment() -> dict[str, str]:
    """Allowlisted environment variables, with credential-shaped names redacted."""
    captured: dict[str, str] = {}
    for name in ENV_ALLOWLIST:
        value = os.environ.get(name)
        if value is None:
            continue
        captured[name] = REDACTED if _SECRET_NAME.search(name) else str(value)
    return captured


def collect_hardware() -> dict:
    """CPU count (as the OS reports it, ignoring cgroups/affinity), plus GPUs if NVML is there."""
    hardware: dict[str, Any] = {"cpu_count": None, "gpus": []}
    try:
        hardware["cpu_count"] = os.cpu_count()
    except Exception:  # pragma: no cover - defensive
        pass
    hardware["gpus"] = _safe(_collect_gpus, [])
    return hardware


def git_state(
    cwd: str | Path | None = None,
    *,
    capture_diff: bool = True,
    diff_limit: int = DEFAULT_DIFF_LIMIT,
) -> tuple[GitState, bytes | None]:
    """Inspect the repo at ``cwd``, returning the state and the patch bytes.

    git is run as an arg list (no shell) with an explicit cwd and a timeout.
    """
    try:
        return _git_state(cwd, capture_diff=capture_diff, diff_limit=diff_limit)
    except _GitUnavailable as exc:
        return GitState(available=False, reason=str(exc)), None
    except Exception as exc:  # pragma: no cover - defensive: nothing below should escape
        return GitState(available=False, reason=_describe(exc)), None


# --------------------------------------------------------------------------------------
# Git internals
# --------------------------------------------------------------------------------------


class _GitUnavailable(Exception):
    """Git could not be run at all. The message becomes the manifest's reason."""


def _git_state(
    cwd: str | Path | None,
    *,
    capture_diff: bool,
    diff_limit: int,
) -> tuple[GitState, bytes | None]:
    directory = _resolve_cwd(cwd)
    if shutil.which("git") is None:
        raise _GitUnavailable("git executable not found on PATH")
    if not directory.is_dir():
        raise _GitUnavailable(f"working directory does not exist: {directory}")

    inside = _run_git(["rev-parse", "--is-inside-work-tree"], directory)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise _GitUnavailable(
            _first_line(inside.stderr) or f"not a git work tree: {directory}"
        )

    notes: list[str] = []

    commit = _first_line(_ok(_run_git(["rev-parse", "HEAD"], directory)))
    if commit is None:
        notes.append("HEAD does not point at a commit yet")

    branch = _first_line(_ok(_run_git(["rev-parse", "--abbrev-ref", "HEAD"], directory)))
    if branch == "HEAD":
        # Detached HEAD. Record None, not "HEAD", so unrelated runs don't look like one branch.
        branch = None

    remote = _first_line(_ok(_run_git(["config", "--get", "remote.origin.url"], directory)))

    status = _run_git(["status", "--porcelain"], directory)
    if status.returncode != 0:
        notes.append("git status failed: " + (_first_line(status.stderr) or "unknown error"))
        dirty = False
    else:
        # Untracked files count as dirty even though the diff won't include them.
        dirty = bool(status.stdout.strip())

    untracked, untracked_truncated = _untracked(directory, notes)

    patch: bytes | None = None
    diff_file: str | None = None
    diff_sha256: str | None = None
    diff_bytes = 0
    diff_truncated = False

    if capture_diff:
        patch, total, truncated, failure = _diff(directory, commit is not None, diff_limit)
        if failure is not None:
            notes.append(failure)
        elif patch:
            diff_file = PATCH_FILE
            diff_bytes = len(patch)
            diff_sha256 = hashlib.sha256(patch).hexdigest()
            diff_truncated = truncated
            if truncated:
                notes.append(
                    f"uncommitted diff truncated at {diff_bytes} of {total} bytes "
                    f"(limit {diff_limit}); the patch will not apply cleanly"
                )
        if patch is not None and not patch:
            patch = None
    else:
        notes.append("diff capture disabled")

    return (
        GitState(
            available=True,
            reason="; ".join(notes) if notes else None,
            commit=commit,
            branch=branch,
            remote=remote,
            dirty=dirty,
            untracked=untracked,
            untracked_truncated=untracked_truncated,
            diff_file=diff_file,
            diff_sha256=diff_sha256,
            diff_bytes=diff_bytes,
            diff_truncated=diff_truncated,
        ),
        patch,
    )


def _untracked(directory: Path, notes: list[str]) -> tuple[tuple[str, ...], bool]:
    """Untracked, non-ignored paths. NUL-separated so newlines in names are safe."""
    result = _run_git(["ls-files", "-z", "--others", "--exclude-standard"], directory)
    if result.returncode != 0:
        notes.append(
            "untracked file listing failed: " + (_first_line(result.stderr) or "unknown error")
        )
        return (), False
    paths = [entry for entry in result.stdout.split("\0") if entry]
    if len(paths) > MAX_UNTRACKED:
        notes.append(
            f"untracked file list truncated to {MAX_UNTRACKED} of {len(paths)} entries"
        )
        return tuple(paths[:MAX_UNTRACKED]), True
    return tuple(paths), False


def _diff(
    directory: Path, have_commit: bool, diff_limit: int
) -> tuple[bytes | None, int, bool, str | None]:
    """Return ``(patch, total_bytes, truncated, failure)``.

    Diffs against HEAD so staged and unstaged changes land in one patch. The flags keep
    the output applicable: no external diff drivers, no textconv, real binary hunks.
    """
    args = ["diff", "--no-ext-diff", "--no-textconv", "--binary"]
    if have_commit:
        args.append("HEAD")
    try:
        data, total, returncode = _run_git_capped(args, directory, diff_limit)
    except _GitUnavailable as exc:
        return None, 0, False, str(exc)
    if returncode != 0:
        return None, 0, False, f"git diff exited with status {returncode}"
    return data, total, total > len(data), None


def _git_env() -> dict[str, str]:
    """Current env with no credential prompts, no optional index locks, and no pager."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_PAGER"] = "cat"
    return env


def _run_git(
    args: list[str], cwd: Path, timeout: float = _GIT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """One git command. Raises :class:`_GitUnavailable` only when it could not be run."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=_git_env(),
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise _GitUnavailable(f"git {args[0]} timed out after {timeout:g}s") from None
    except (OSError, ValueError) as exc:
        raise _GitUnavailable(f"could not run git: {_describe(exc)}") from None


def _run_git_capped(args: list[str], cwd: Path, limit: int) -> tuple[bytes, int, int]:
    """Run git, keep at most ``limit`` bytes of stdout, and count all of them.

    Streamed so a huge diff is never fully buffered. Output past the cap is drained so the
    true size is known and git doesn't block. stderr is discarded to avoid a two-pipe
    deadlock; the exit code is enough.
    """
    keep = max(0, int(limit))
    try:
        proc = subprocess.Popen(
            ["git", *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_env(),
            shell=False,
        )
    except (OSError, ValueError) as exc:
        raise _GitUnavailable(f"could not run git: {_describe(exc)}") from None

    expired = threading.Event()

    def _expire() -> None:
        expired.set()
        try:
            proc.kill()
        except Exception:  # pragma: no cover - the process may already be gone
            pass

    watchdog = threading.Timer(_GIT_DIFF_TIMEOUT, _expire)
    watchdog.daemon = True
    watchdog.start()
    try:
        chunks: list[bytes] = []
        kept = 0
        total = 0
        stream = proc.stdout
        assert stream is not None
        while True:
            block = stream.read(1 << 16)
            if not block:
                break
            total += len(block)
            if kept < keep:
                room = keep - kept
                chunks.append(block[:room])
                kept += min(room, len(block))
        stream.close()
        returncode = proc.wait()
    except OSError as exc:
        _expire()
        raise _GitUnavailable(f"could not read git output: {_describe(exc)}") from None
    finally:
        watchdog.cancel()

    if expired.is_set():
        raise _GitUnavailable(f"git diff timed out after {_GIT_DIFF_TIMEOUT:g}s")
    return b"".join(chunks), total, returncode


# --------------------------------------------------------------------------------------
# Hashing internals
# --------------------------------------------------------------------------------------


def _hash_file(path: Path, digest: Any, chunk: int) -> int:
    total = 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
            total += len(block)
    return total


def _hash_directory(root: Path, digest: Any, chunk: int) -> tuple[int, int]:
    """Sorted by POSIX relative path so the digest is the same on every filesystem."""
    relatives: list[str] = []
    for parent, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in filenames:
            full = Path(parent, name)
            if full.is_symlink() or not full.is_file():
                continue
            relatives.append(full.relative_to(root).as_posix())
    relatives.sort()

    total = 0
    for relative in relatives:
        target = root / relative
        size = target.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        total += _hash_file(target, digest, chunk)
    return total, len(relatives)


# --------------------------------------------------------------------------------------
# Environment blocks
# --------------------------------------------------------------------------------------


def _collect_python() -> dict[str, Any]:
    return {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
    }


def _collect_platform() -> dict[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }


def _collect_command() -> dict[str, Any]:
    """``argv`` and cwd, for replay.

    argv may hold secrets (e.g. ``--api-key``) but is kept as-is; stripping flags would make
    the replay wrong. Keep run directories out of version control.
    """
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    return {"argv": [str(arg) for arg in sys.argv], "cwd": cwd}


def _collect_gpus() -> list[dict[str, Any]]:
    """Per-device details through NVML, or an empty list. Same lazy import as system.py."""
    try:
        import pynvml  # noqa: PLC0415 - optional extra, deliberately lazy
    except Exception:
        return []

    gpus: list[dict[str, Any]] = []
    try:
        pynvml.nvmlInit()
    except Exception:
        return []
    try:
        driver = _safe(lambda: _nvml_text(pynvml.nvmlSystemGetDriverVersion()), None)
        cuda = _cuda_version(pynvml)
        count = _safe(pynvml.nvmlDeviceGetCount, 0)
        for index in range(count):
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                name = _nvml_text(pynvml.nvmlDeviceGetName(handle))
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                total_mb = int(round(float(memory.total) / (1024 * 1024)))
            except Exception:
                continue
            gpus.append(
                {
                    "name": name,
                    "memory_total_mb": total_mb,
                    "driver_version": driver,
                    "cuda_version": cuda,
                }
            )
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:  # pragma: no cover - shutdown of a failed init
            pass
    return gpus


def _cuda_version(pynvml: Any) -> str | None:
    """NVML reports the driver's CUDA version as an integer: 12040 means 12.4."""
    try:
        raw = int(pynvml.nvmlSystemGetCudaDriverVersion())
    except Exception:
        return None
    if raw <= 0:
        return None
    return f"{raw // 1000}.{(raw % 1000) // 10}"


def _nvml_text(value: Any) -> str | None:
    """NVML returns ``bytes`` on older bindings and ``str`` on newer ones."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value) if value is not None else None


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _safe(fn: Callable[[], _T], fallback: _T) -> _T:
    """Call ``fn``, or return ``fallback`` if it raises."""
    try:
        return fn()
    except Exception:
        return fallback


def _now_iso() -> str:
    """Local time with an explicit UTC offset, like the other timestamps in the format."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _resolve_cwd(cwd: str | Path | None) -> Path:
    if cwd is not None:
        return Path(cwd)
    try:
        return Path.cwd()
    except OSError as exc:
        raise _GitUnavailable(f"could not resolve working directory: {_describe(exc)}") from None


def _ok(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout if result.returncode == 0 else ""


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _with_reason(state: GitState, note: str) -> GitState:
    joined = f"{state.reason}; {note}" if state.reason else note
    return GitState(
        available=state.available,
        reason=joined,
        commit=state.commit,
        branch=state.branch,
        remote=state.remote,
        dirty=state.dirty,
        untracked=state.untracked,
        untracked_truncated=state.untracked_truncated,
        diff_file=state.diff_file,
        diff_sha256=state.diff_sha256,
        diff_bytes=state.diff_bytes,
        diff_truncated=state.diff_truncated,
    )


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_dict(value: Any) -> dict[str, Any]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}
