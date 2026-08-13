"""What the world looked like when the run started, recorded so it can be checked later.

A commit hash alone does not reproduce a run. The tree that trained the model is the
commit *plus* whatever was uncommitted at the time, against the packages that happened to
be importable, on the data that happened to be on disk. Recording only the hash records
the part that is already safe and drops the parts that are not: the diff exists nowhere
else, the dataset can be replaced in place without changing its name, and an environment
is rebuilt from a lockfile that may itself have moved. This module captures all four so
that a later ``verify`` can say which of them drifted, field by field, and a ``replay`` has
something to restore.

Three properties govern every line below.

**It must never raise.** Provenance is attached to somebody's training job. A capture that
throws turns a nicety into an outage, so every failure — no git binary, a directory that
is not a repository, a driver that answers nonsense — degrades to ``available=False`` and a
recorded ``reason``. :func:`capture` has no exception path at all; if it ever grows one,
that is a defect of the same severity as corrupting a run.

**It must not widen the dependency surface.** Git state comes from the ``git`` binary
through :mod:`subprocess`, package versions from :mod:`importlib.metadata`, hashes from
:mod:`hashlib`. All standard library. ``pynvml`` stays optional and lazily imported, as in
``system.py``.

**It must not exfiltrate anything.** Two rules, both load-bearing and both tested. The
environment is captured from an explicit allowlist and never wholesale, because a process
environment routinely holds API keys, and any allowlisted name that still looks like a
credential has its value replaced with :data:`REDACTED`. And the uncommitted diff is
capture of a developer's dirty tree, which is exactly where a hardcoded key or an edited
``.env`` lives — so it is bounded, its truncation is recorded rather than silent, and the
flag that controls it is explicit at the call site.
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

#: A megabyte of patch is already a diff nobody will read, and the manifest sits in a run
#: directory the user did not choose to grow. Above this the patch is cut and the cut is
#: recorded — a truncated patch is honest and useless, a silent one is dishonest and
#: useless, and only the second kind produces a failed replay nobody can explain.
DEFAULT_DIFF_LIMIT: int = 1024 * 1024

#: Untracked files are capped because an enormous list is a missing ``.gitignore``, not
#: information: it is ``node_modules`` or a checkpoint directory, and writing 40,000 paths
#: into every run's manifest costs more than the fact is worth.
MAX_UNTRACKED: int = 200

#: The only environment variables ever recorded. An allowlist rather than a denylist
#: because the failure modes are not symmetric: a variable this list forgets costs a field
#: in a diagnostic, and a variable a denylist forgets writes somebody's API key into a file
#: they will commit. Every name here is one that changes how a training run behaves —
#: device visibility, thread counts, seeding, the distributed topology, the interpreter.
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

#: Written in place of a value whose *name* looks like a credential. Nothing in
#: :data:`ENV_ALLOWLIST` matches today, which is the point: this fires the day somebody
#: appends a name to that tuple without thinking about what it holds.
REDACTED: str = "<redacted>"

_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)

#: Short, because these commands are local metadata reads and a hung one blocks a training
#: script at its first line. The diff gets its own budget: it walks the whole worktree.
_GIT_TIMEOUT: float = 15.0
_GIT_DIFF_TIMEOUT: float = 60.0

_T = TypeVar("_T")


# --------------------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GitState:
    """The ``git`` block of the manifest.

    ``available`` is false whenever the repository could not be inspected at all, and
    ``reason`` then says why. ``reason`` is *also* set on a partial success — a detached
    HEAD with no commits, a diff that hit the cap, a ``git diff`` that exited non-zero —
    because "we captured this much and here is what we missed" is the only report that
    lets a later verification distinguish drift from a gap in the record.
    """

    available: bool = False
    reason: str | None = None
    commit: str | None = None
    branch: str | None = None
    remote: str | None = None
    dirty: bool = False
    untracked: tuple[str, ...] = ()
    #: Set when the untracked list hit :data:`MAX_UNTRACKED`. Not in the original field
    #: sketch; added because a capped list that does not say it is capped reads as a
    #: complete list, and a reader would conclude exactly 200 files were untracked.
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
        """Rebuild from whatever is actually on disk, which may be anything.

        This parses a file a user can edit and an older writer may have produced, so every
        field is coerced rather than trusted; a manifest that fails to load is a manifest
        that cannot be verified against, which is worse than one loaded conservatively.
        """
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
    """The whole ``provenance.json`` manifest.

    Frozen because a manifest describes a moment: mutating one after capture produces a
    record of a world that never existed. Every block is a plain ``dict`` rather than a
    nested dataclass — they are passthrough JSON with no invariants of their own, and a
    class per block would buy validation this module deliberately does not do.
    """

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
        """Inverse of :meth:`to_dict`, tolerant of anything a reader might be handed."""
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
    """Capture the manifest and the raw patch bytes.

    Returns ``(provenance, patch)``; ``patch`` is ``None`` when the tree is clean, when
    ``capture_diff`` is false, or when the diff could not be taken. The patch is returned
    rather than written because this module owns no filesystem policy — ``storage.py`` is
    the only thing in the package that opens a file, and it writes the patch before the
    manifest that names it.

    ``datasets`` are entries produced by :func:`hash_path`; hashing is the caller's
    decision because it is the one part of capture that can take minutes. ``command``
    defaults to the interpreter's own ``argv`` and working directory, which is the command
    a replay would have to re-run.

    **This function does not raise.** Every block is captured independently and every
    failure degrades to a recorded reason or an empty block, because the caller is a
    training script that must not die at its first line over a missing driver.
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
    """Content hash of a file, or of a directory tree, as a ``datasets`` entry.

    A directory is hashed deterministically: files are walked and sorted by their POSIX
    relative path, and both the path and the bytes go into the digest. Feeding the path in
    is what makes a rename change the hash — a dataset whose ``train`` and ``val`` splits
    were swapped is a different dataset, and a bytes-only digest would call it identical.
    Each file's length is fed in too, so that concatenation cannot be ambiguous: without
    it, two files of ``ab`` + ``c`` and ``a`` + ``bc`` hash the same.

    Symlinks are not followed, because a dataset directory containing a link to somewhere
    outside it is a dataset whose hash would depend on a path this manifest does not
    record — and following links invites a walk that never terminates.

    A missing or unreadable path returns an entry carrying ``error`` instead of a digest.
    Refusing to raise matters more here than anywhere else in the module: this is called
    with a path a user typed, and a typo must cost the dataset field, not the run.
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
    # Always present, so a reader does not have to know which algorithm was used to find
    # the digest. The `sha256` key is emitted only when it is true — a blake2b hash filed
    # under `sha256` is the kind of lie that survives until someone tries to verify it.
    entry["digest"] = hexdigest
    entry["bytes"] = total_bytes
    entry["files"] = file_count
    return entry


def collect_packages() -> dict[str, str]:
    """Installed distributions and their versions, sorted by lowercased name.

    ``importlib.metadata`` rather than ``pip freeze``: no subprocess, no pip, and it sees
    the environment this interpreter actually imports from — which is the environment that
    trained the model, even when ``pip`` on the PATH points somewhere else. It is not a
    lockfile and is not claimed to be: it records resolved versions, not the constraints
    that produced them, and it cannot see a package installed from a local path any
    differently from one from an index.

    Distributions are deduplicated by name with the first occurrence winning, matching
    import resolution: when two ``sys.path`` entries provide the same package, the first
    one is what gets imported.
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
    """Allowlisted environment variables, with credential-shaped names redacted.

    Reads :data:`ENV_ALLOWLIST` at call time rather than closing over it, so a caller that
    extends the tuple gets the redaction pass applied to the additions too — which is the
    only case in which the redaction can ever fire.
    """
    captured: dict[str, str] = {}
    for name in ENV_ALLOWLIST:
        value = os.environ.get(name)
        if value is None:
            continue
        captured[name] = REDACTED if _SECRET_NAME.search(name) else str(value)
    return captured


def collect_hardware() -> dict:
    """CPU count always; GPU details when NVML happens to be there.

    ``cpu_count`` is what the OS reports, not what the process may use — a cgroup quota or
    an affinity mask can make the usable count smaller, and the difference is exactly the
    kind of thing that makes a replay slower than the original without changing a number
    anyone records. It is reported unadjusted because the alternative is a platform-
    specific guess presented as a fact.
    """
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
    """Inspect the repository at ``cwd``, returning the state and the patch bytes.

    Every git invocation is a list — never a shell string — with an explicit ``cwd``, a
    timeout, and ``check=False``. A shell would make a repository path with a space or a
    quote in it into a command, the explicit ``cwd`` is what stops the answer depending on
    where the training script happened to be started from, and a timeout is what stops a
    filesystem that has gone away from hanging the run forever.
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
    """Git could not be consulted at all. Carries the reason recorded in the manifest."""


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
        # Detached HEAD. Null rather than the literal "HEAD" the plumbing prints, because
        # a reader comparing branches would otherwise see two unrelated runs "on HEAD".
        branch = None

    remote = _first_line(_ok(_run_git(["config", "--get", "remote.origin.url"], directory)))

    status = _run_git(["status", "--porcelain"], directory)
    if status.returncode != 0:
        notes.append("git status failed: " + (_first_line(status.stderr) or "unknown error"))
        dirty = False
    else:
        # Untracked files count as dirty: a tree with a new, unignored file is not the
        # tree the commit describes, and the diff below will not show it.
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
    """Untracked, unignored paths — NUL-separated so a newline in a filename cannot lie.

    ``--exclude-standard`` applies the same ignore rules the user already curated. Without
    it every build output and virtualenv in the tree lands in the manifest.
    """
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
    """``(patch, total_bytes, truncated, failure)``.

    ``git diff HEAD`` rather than a bare ``git diff``, so that staged and unstaged changes
    land in one patch — a replay that restored only half of them would silently train
    something else. Three flags matter and each closes a way the output stops being a
    patch: ``--no-ext-diff`` refuses any external diff driver the user configured
    (a configured ``difftool`` otherwise emits prose that ``git apply`` cannot read),
    ``--no-textconv`` refuses the ``.gitattributes`` filters that turn a PDF into text for
    human reading, and ``--binary`` emits the literal binary hunks that make a patch
    touching a non-text file applicable rather than a stub saying the file differs.
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
    """The caller's environment with three settings forced.

    ``GIT_TERMINAL_PROMPT`` stops git asking for credentials on a terminal a training job
    may not have. ``GIT_OPTIONAL_LOCKS`` stops a status refresh taking the index lock out
    from under whatever else the developer has open. ``GIT_PAGER`` stops a configured
    pager from being spawned into a pipe that will never be read.
    """
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

    The output is streamed rather than buffered by :func:`subprocess.run` because the
    thing being read is a diff, and a diff of a tree where somebody committed a dataset is
    unbounded — buffering it to decide it is too large is the failure mode this cap exists
    to prevent. Everything past the cap is read and discarded rather than left unread, so
    the true size can be reported and git never blocks writing into a pipe nobody drains.

    ``stderr`` goes to the void deliberately: reading two pipes from one thread deadlocks
    the moment the unread one fills, and a status code is enough to know a diff failed.
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
    """Sort by POSIX relative path so that the digest does not depend on the filesystem.

    Directory order is arbitrary and differs between ext4, NTFS and APFS, so a walk in
    natural order would hash the same tree to different values on two machines — which is
    precisely the comparison this hash exists to support.
    """
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
    """``argv`` and the working directory — the command a replay has to re-run.

    ``argv`` can contain a secret passed as ``--api-key``; it is recorded anyway, because
    a command line with the flags removed is not the command that ran and would make a
    replay wrong rather than incomplete. The manifest is written into the user's own run
    directory, and the ``.gitignore`` guidance in the data contract is where that is
    handled.
    """
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    return {"argv": [str(arg) for arg in sys.argv], "cwd": cwd}


def _collect_gpus() -> list[dict[str, Any]]:
    """Per-device details through NVML, or an empty list.

    Lazily imported and wrapped exactly as in ``system.py``: a machine without NVIDIA
    tooling is the common case, not an error, and a driver that answers an unexpected
    struct must cost this field and nothing else.
    """
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
    """Call ``fn``, or return ``fallback``. The reason capture cannot fail a run."""
    try:
        return fn()
    except Exception:
        return fallback


def _now_iso() -> str:
    """Local time with an explicit offset, matching every other timestamp in the format.

    An offset is not decoration here: a manifest is compared against a later capture on
    another machine, and a bare local timestamp is read as the reader's own zone.
    """
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
