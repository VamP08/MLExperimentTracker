"""Check whether the environment recorded in ``provenance.json`` still holds here.

Each check is OK, DRIFT or UNKNOWN. DRIFT only when we asked and got a different answer;
not being able to ask is UNKNOWN. Fields the manifest left empty produce no check.
"""

from __future__ import annotations

import hashlib
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .provenance import (
    GitState,
    Provenance,
    _first_line,
    _git_env,
    _GitUnavailable,
    _run_git,
    collect_packages,
    hash_path,
)
from .storage import Storage

__all__ = [
    "Check",
    "CheckStatus",
    "Verdict",
    "VerifyReport",
    "repository_root",
    "verify",
]

# Local only, no network. Longer than provenance.py's git timeout since it stats every
# path in the patch.
_APPLY_TIMEOUT: float = 60.0

# Names listed in a drift detail before "and N more".
_NAMES_SHOWN: int = 5

_NORMALISE = re.compile(r"[-_.]+")


class CheckStatus(str, Enum):
    """Result of one check. ``UNKNOWN`` means the question couldn't be answered.

    A ``str`` enum, like ``RunState``, so it compares to and serialises as a plain string.
    """

    OK = "ok"
    DRIFT = "drift"
    UNKNOWN = "unknown"


class Verdict(str, Enum):
    """Overall result. Any DRIFT makes it ``DRIFTED``, even if other checks are UNKNOWN."""

    REPRODUCIBLE = "reproducible"
    DRIFTED = "drifted"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class Check:
    """One check: status, recorded vs current value, and a human-readable detail.

    ``expected``/``actual`` are filled in even on ``OK``.
    """

    name: str
    status: CheckStatus
    expected: Any = None
    actual: Any = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "expected": _jsonable(self.expected),
            "actual": _jsonable(self.actual),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class VerifyReport:
    """All checks plus the verdict, which :func:`verify` computes once at construction."""

    run_id: str
    project: str
    checks: tuple[Check, ...] = ()
    verdict: Verdict = Verdict.UNVERIFIABLE

    def to_dict(self) -> dict[str, Any]:
        """API shape. ``summary`` is a count per status."""
        return {
            "run_id": self.run_id,
            "project": self.project,
            "verdict": self.verdict.value,
            "summary": {
                status.value: sum(1 for check in self.checks if check.status is status)
                for status in CheckStatus
            },
            "checks": [check.to_dict() for check in self.checks],
        }

    def by_name(self, name: str) -> Check | None:
        """The check called ``name``, or ``None``."""
        for check in self.checks:
            if check.name == name:
                return check
        return None


def verify(
    storage: Storage,
    project: str,
    run_id: str,
    *,
    cwd: str | Path | None = None,
    rehash_datasets: bool = True,
) -> VerifyReport:
    """Compare a run's recorded provenance against the current environment.

    ``cwd`` is the repo to inspect; defaults to the manifest's ``command.cwd`` if it still
    exists, else the process cwd. ``rehash_datasets=False`` skips dataset hashing, which
    can be slow. Doesn't raise for unverifiable runs; those get UNKNOWN checks instead.
    """
    manifest_dict = storage.read_provenance(project, run_id)
    if manifest_dict is None:
        return VerifyReport(
            run_id=run_id,
            project=project,
            checks=(
                Check(
                    name="manifest",
                    status=CheckStatus.UNKNOWN,
                    expected="provenance.json",
                    actual=None,
                    detail=(
                        "no provenance manifest: the run predates format 1.1, or its "
                        "capture failed. The world it ran in was never recorded and "
                        "cannot be reconstructed after the fact."
                    ),
                ),
            ),
            verdict=Verdict.UNVERIFIABLE,
        )

    manifest = Provenance.from_dict(manifest_dict)
    checks: list[Check] = [
        Check(
            name="manifest",
            status=CheckStatus.OK,
            expected="provenance.json",
            actual="provenance.json",
            detail=f"captured at {manifest.captured_at}" if manifest.captured_at else None,
        )
    ]

    directory = _base_directory(manifest, cwd)
    checks.extend(_guard("git", lambda: _git_checks(storage, project, run_id, manifest, directory)))
    checks.extend(_guard("python", lambda: _python_checks(manifest)))
    checks.extend(_guard("platform", lambda: _platform_checks(manifest)))
    checks.extend(_guard("packages", lambda: _package_checks(manifest)))
    if rehash_datasets:
        checks.extend(_guard("datasets", lambda: _dataset_checks(manifest, directory)))

    return VerifyReport(
        run_id=run_id,
        project=project,
        checks=tuple(checks),
        verdict=_verdict(checks),
    )


# --------------------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------------------


def _git_checks(
    storage: Storage, project: str, run_id: str, manifest: Provenance, directory: Path
) -> list[Check]:
    git = manifest.git
    if not git.available:
        return [
            Check(
                name="git.repository",
                status=CheckStatus.UNKNOWN,
                expected=None,
                actual=None,
                detail="the run recorded no git state: " + (git.reason or "reason not recorded"),
            )
        ]

    repo, failure = repository_root(directory)
    if repo is None:
        return [
            Check(
                name="git.repository",
                status=CheckStatus.UNKNOWN,
                expected=git.commit,
                actual=None,
                detail=f"{failure}; nothing about the recorded repository can be checked",
            )
        ]

    checks: list[Check] = [
        Check(
            name="git.repository",
            status=CheckStatus.OK,
            expected=None,
            actual=str(repo),
            detail=f"inspected the work tree at {repo}",
        )
    ]

    remote_check = _remote_check(git, repo)
    if remote_check is not None:
        checks.append(remote_check)

    head = _first_line(_stdout(_git(repo, "rev-parse", "HEAD")))

    if git.commit:
        checks.append(_commit_check(git.commit, repo))
        checks.append(_head_check(git.commit, head))

    dirty_now, dirty_known = _dirty(repo)
    checks.append(_worktree_check(git, dirty_now, dirty_known))

    patch_check = _patch_check(storage, project, run_id, git, repo, head)
    if patch_check is not None:
        checks.append(patch_check)

    return checks


def _remote_check(git: GitState, repo: Path) -> Check | None:
    """Compare origin URLs, if one was recorded. Catches verifying against the wrong repo."""
    if not git.remote:
        return None
    current = _first_line(_stdout(_git(repo, "config", "--get", "remote.origin.url")))
    if current is None:
        return Check(
            name="git.remote",
            status=CheckStatus.UNKNOWN,
            expected=git.remote,
            actual=None,
            detail="this work tree has no origin remote, so it cannot be identified",
        )
    if current == git.remote:
        return Check(
            name="git.remote",
            status=CheckStatus.OK,
            expected=git.remote,
            actual=current,
            detail="the same origin as the run recorded",
        )
    return Check(
        name="git.remote",
        status=CheckStatus.DRIFT,
        expected=git.remote,
        actual=current,
        detail="a different repository: every git check below is against another origin",
    )


def _commit_check(commit: str, repo: Path) -> Check:
    """Is the recorded commit in this repo? Missing is DRIFT (it may just need a fetch)."""
    result = _git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    if result is not None and result.returncode == 0:
        return Check(
            name="git.commit",
            status=CheckStatus.OK,
            expected=commit,
            actual=commit,
            detail="the recorded commit is present in this repository",
        )
    if result is None:
        return Check(
            name="git.commit",
            status=CheckStatus.UNKNOWN,
            expected=commit,
            actual=None,
            detail="git could not be run to look the commit up",
        )
    return Check(
        name="git.commit",
        status=CheckStatus.DRIFT,
        expected=commit,
        actual=None,
        detail=(
            "the recorded commit is not in this repository. It may still exist on a "
            "remote that has not been fetched, or it may have been rewritten away."
        ),
    )


def _head_check(commit: str, head: str | None) -> Check:
    if head is None:
        return Check(
            name="git.head",
            status=CheckStatus.UNKNOWN,
            expected=commit,
            actual=None,
            detail="HEAD does not point at a commit here",
        )
    if head == commit:
        return Check(
            name="git.head",
            status=CheckStatus.OK,
            expected=commit,
            actual=head,
            detail="the recorded commit is what is checked out",
        )
    return Check(
        name="git.head",
        status=CheckStatus.DRIFT,
        expected=commit,
        actual=head,
        detail="a different commit is checked out than the one the run was executed at",
    )


def _worktree_check(git: GitState, dirty_now: bool, known: bool) -> Check:
    """Clean vs dirty only; whether the changes match is the patch check's job."""
    if not known:
        return Check(
            name="git.worktree",
            status=CheckStatus.UNKNOWN,
            expected="dirty" if git.dirty else "clean",
            actual=None,
            detail="git status could not be read",
        )
    expected = "dirty" if git.dirty else "clean"
    actual = "dirty" if dirty_now else "clean"
    if git.dirty == dirty_now:
        detail = (
            "the tree has uncommitted changes, as it did at capture; the patch check "
            "compares their content"
            if dirty_now
            else "the tree is clean, as it was at capture"
        )
        return Check("git.worktree", CheckStatus.OK, expected, actual, detail)
    detail = (
        "the tree now carries uncommitted changes that the run did not have"
        if dirty_now
        else "the uncommitted changes the run was executed with are no longer in the tree"
    )
    return Check("git.worktree", CheckStatus.DRIFT, expected, actual, detail)


def _patch_check(
    storage: Storage,
    project: str,
    run_id: str,
    git: GitState,
    repo: Path,
    head: str | None,
) -> Check | None:
    """Is the recorded uncommitted diff still intact?

    Tried forwards (clean checkout of the base commit) then in reverse (changes already
    in the tree). Neither applying is DRIFT only if the base commit is checked out;
    otherwise UNKNOWN.
    """
    if not git.diff_file:
        return None

    patch = storage.read_patch(project, run_id)
    if patch is None:
        return Check(
            name="git.patch",
            status=CheckStatus.UNKNOWN,
            expected=git.diff_file,
            actual=None,
            detail=(
                f"the manifest names {git.diff_file} but the file is not in the run "
                "directory; the record is incomplete, which is not the same as drift"
            ),
        )

    digest = hashlib.sha256(patch).hexdigest()
    if git.diff_sha256 and digest != git.diff_sha256:
        return Check(
            name="git.patch",
            status=CheckStatus.UNKNOWN,
            expected=git.diff_sha256,
            actual=digest,
            detail="the patch on disk is not the patch that was captured; it was edited or truncated",
        )

    if git.diff_truncated:
        return Check(
            name="git.patch",
            status=CheckStatus.UNKNOWN,
            expected=git.diff_file,
            actual=f"{git.diff_bytes} bytes, truncated at capture",
            detail=(
                "the patch was cut at the capture limit and is evidence rather than a "
                "restore point; it cannot apply, and its failure would say nothing"
            ),
        )

    forward = _apply_check(repo, patch, reverse=False)
    if forward == 0:
        return Check(
            name="git.patch",
            status=CheckStatus.OK,
            expected=git.diff_sha256 or git.diff_file,
            actual=digest,
            detail="the recorded patch applies to the tree as it stands",
        )

    reverse = _apply_check(repo, patch, reverse=True)
    if reverse == 0:
        return Check(
            name="git.patch",
            status=CheckStatus.OK,
            expected=git.diff_sha256 or git.diff_file,
            actual=digest,
            detail="the tree already contains exactly the recorded uncommitted changes",
        )

    if forward is None and reverse is None:
        return Check(
            name="git.patch",
            status=CheckStatus.UNKNOWN,
            expected=git.diff_file,
            actual=None,
            detail="git apply could not be run",
        )

    if git.commit and head != git.commit:
        return Check(
            name="git.patch",
            status=CheckStatus.UNKNOWN,
            expected=git.diff_file,
            actual=None,
            detail=(
                f"the patch was taken against {git.commit[:12]}, which is not what is "
                "checked out; whether it still applies cannot be decided from here"
            ),
        )

    return Check(
        name="git.patch",
        status=CheckStatus.DRIFT,
        expected=git.diff_sha256 or git.diff_file,
        actual=digest,
        detail=(
            "the recorded patch neither applies to this tree nor is already contained in "
            "it, at the commit it was captured against: the uncommitted work has changed"
        ),
    )


def repository_root(directory: str | Path) -> tuple[Path | None, str | None]:
    """``(toplevel, None)`` for a git work tree, else ``(None, reason)``.

    Also used by replay. Returns the toplevel since patch paths are relative to it.
    """
    target = Path(directory)
    if shutil.which("git") is None:
        return None, "git executable not found on PATH"
    if not target.is_dir():
        return None, f"directory does not exist: {target}"
    result = _git(target, "rev-parse", "--show-toplevel")
    if result is None:
        return None, "git could not be run"
    if result.returncode != 0:
        return None, _first_line(result.stderr) or f"not a git work tree: {target}"
    top = _first_line(result.stdout)
    if not top:
        return None, f"not a git work tree: {target}"
    return Path(top), None


def _dirty(repo: Path) -> tuple[bool, bool]:
    """``(dirty, answered)``. Untracked files count, same as at capture."""
    result = _git(repo, "status", "--porcelain")
    if result is None or result.returncode != 0:
        return False, False
    return bool(result.stdout.strip()), True


def _apply_check(repo: Path, patch: bytes, *, reverse: bool) -> int | None:
    """``git apply --check`` with the patch on stdin. ``None`` if git couldn't be run.

    Piped, not a temp file. ``--binary`` because the diff has binary hunks.

    Unlike replay, don't override ``core.autocrlf``: replay owns its worktree, we don't.
    On Windows (autocrlf=true) forcing it off rejects an intact patch as DRIFT.
    """
    args = ["git", "apply", "--check", "--binary"]
    if reverse:
        args.append("--reverse")
    args.append("-")
    try:
        result = subprocess.run(
            args,
            cwd=str(repo),
            input=patch,
            capture_output=True,
            timeout=_APPLY_TIMEOUT,
            check=False,
            env=_git_env(),
            shell=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return result.returncode


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run one git command via capture's own runner, or ``None`` if it couldn't run."""
    try:
        return _run_git(list(args), cwd)
    except _GitUnavailable:
        return None


def _stdout(result: subprocess.CompletedProcess[str] | None) -> str:
    if result is None or result.returncode != 0:
        return ""
    return result.stdout


# --------------------------------------------------------------------------------------
# Interpreter, platform, packages, datasets
# --------------------------------------------------------------------------------------


def _python_checks(manifest: Provenance) -> list[Check]:
    checks: list[Check] = []
    recorded_version = _text(manifest.python.get("version"))
    if recorded_version:
        current = platform.python_version()
        checks.append(
            Check(
                name="python.version",
                status=CheckStatus.OK if current == recorded_version else CheckStatus.DRIFT,
                expected=recorded_version,
                actual=current,
                detail=None
                if current == recorded_version
                else "a different interpreter version is running this check",
            )
        )
    recorded_impl = _text(manifest.python.get("implementation"))
    if recorded_impl:
        current_impl = platform.python_implementation()
        checks.append(
            Check(
                name="python.implementation",
                status=CheckStatus.OK if current_impl == recorded_impl else CheckStatus.DRIFT,
                expected=recorded_impl,
                actual=current_impl,
            )
        )
    return checks


def _platform_checks(manifest: Provenance) -> list[Check]:
    """Check ``system`` and ``machine`` only.

    An OS release change moves with routine updates, so it goes in the detail, not DRIFT.
    """
    checks: list[Check] = []
    recorded_system = _text(manifest.platform.get("system"))
    if recorded_system:
        current = platform.system()
        notes: list[str] = []
        recorded_release = _text(manifest.platform.get("release"))
        if recorded_release and recorded_release != platform.release():
            notes.append(
                f"the OS release moved from {recorded_release} to {platform.release()}, "
                "which is recorded but is not treated as drift"
            )
        checks.append(
            Check(
                name="platform.system",
                status=CheckStatus.OK if current == recorded_system else CheckStatus.DRIFT,
                expected=recorded_system,
                actual=current,
                detail="; ".join(notes) if notes else None,
            )
        )
    recorded_machine = _text(manifest.platform.get("machine"))
    if recorded_machine:
        current_machine = platform.machine()
        checks.append(
            Check(
                name="platform.machine",
                status=CheckStatus.OK if current_machine == recorded_machine else CheckStatus.DRIFT,
                expected=recorded_machine,
                actual=current_machine,
                detail=None
                if current_machine == recorded_machine
                else "a different architecture: numerical results may differ for that alone",
            )
        )
    return checks


def _package_checks(manifest: Provenance) -> list[Check]:
    """Added, removed and changed packages.

    Names are PEP 503-normalised for comparison; the recorded spelling is shown.
    """
    if not manifest.packages:
        return []

    recorded = {_normalise(name): (name, version) for name, version in manifest.packages.items()}
    current = {_normalise(name): (name, version) for name, version in collect_packages().items()}

    added = sorted(current[key][0] for key in current.keys() - recorded.keys())
    removed = sorted(recorded[key][0] for key in recorded.keys() - current.keys())
    changed = sorted(
        f"{recorded[key][0]} {recorded[key][1]} -> {current[key][1]}"
        for key in recorded.keys() & current.keys()
        if recorded[key][1] != current[key][1]
    )

    if not (added or removed or changed):
        return [
            Check(
                name="packages",
                status=CheckStatus.OK,
                expected=len(recorded),
                actual=len(current),
                detail=f"all {len(recorded)} recorded distributions are installed at the "
                "recorded versions",
            )
        ]

    parts: list[str] = []
    if added:
        parts.append(f"{len(added)} added ({_names(added)})")
    if removed:
        parts.append(f"{len(removed)} removed ({_names(removed)})")
    if changed:
        parts.append(f"{len(changed)} changed ({_names(changed)})")
    return [
        Check(
            name="packages",
            status=CheckStatus.DRIFT,
            expected=len(recorded),
            actual=len(current),
            detail="; ".join(parts),
        )
    ]


def _dataset_checks(manifest: Provenance, directory: Path) -> list[Check]:
    """Re-hash each recorded dataset.

    A missing path is UNKNOWN, not DRIFT: paths are often relative, so we may just be in
    the wrong directory.
    """
    checks: list[Check] = []
    for entry in manifest.datasets:
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        name = f"dataset:{raw_path}"
        recorded_digest = _text(entry.get("digest")) or _text(entry.get("sha256"))
        if not recorded_digest:
            checks.append(
                Check(
                    name=name,
                    status=CheckStatus.UNKNOWN,
                    expected=None,
                    actual=None,
                    detail="no digest was recorded: " + (_text(entry.get("error")) or "unknown"),
                )
            )
            continue

        algorithm = _text(entry.get("algorithm")) or "sha256"
        target = Path(raw_path)
        if not target.is_absolute():
            target = directory / target
        if not target.exists():
            checks.append(
                Check(
                    name=name,
                    status=CheckStatus.UNKNOWN,
                    expected=recorded_digest,
                    actual=None,
                    detail=f"nothing at {target}; the dataset may have moved, or this may "
                    "be the wrong directory to verify from",
                )
            )
            continue

        fresh = hash_path(target, algorithm=algorithm)
        current_digest = _text(fresh.get("digest"))
        if current_digest is None:
            checks.append(
                Check(
                    name=name,
                    status=CheckStatus.UNKNOWN,
                    expected=recorded_digest,
                    actual=None,
                    detail=_text(fresh.get("error")) or "the dataset could not be read",
                )
            )
            continue

        if current_digest == recorded_digest:
            checks.append(
                Check(
                    name=name,
                    status=CheckStatus.OK,
                    expected=recorded_digest,
                    actual=current_digest,
                    detail=f"{fresh.get('files')} file(s), {fresh.get('bytes')} bytes, unchanged",
                )
            )
        else:
            checks.append(
                Check(
                    name=name,
                    status=CheckStatus.DRIFT,
                    expected=recorded_digest,
                    actual=current_digest,
                    detail="the data at this path is not the data the run was trained on",
                )
            )
    return checks


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _verdict(checks: list[Check]) -> Verdict:
    if not checks:
        return Verdict.UNVERIFIABLE
    if any(check.status is CheckStatus.DRIFT for check in checks):
        return Verdict.DRIFTED
    if any(check.status is CheckStatus.UNKNOWN for check in checks):
        return Verdict.UNVERIFIABLE
    return Verdict.REPRODUCIBLE


def _guard(name: str, produce: Any) -> list[Check]:
    """Run one group of checks; if it raises, report a single UNKNOWN for that group."""
    try:
        return list(produce())
    except Exception as exc:  # pragma: no cover - defensive: each group swallows its own
        return [
            Check(
                name=name,
                status=CheckStatus.UNKNOWN,
                expected=None,
                actual=None,
                detail=f"this group of checks failed: {type(exc).__name__}: {exc}",
            )
        ]


def _base_directory(manifest: Provenance, cwd: str | Path | None) -> Path:
    if cwd is not None:
        return Path(cwd)
    recorded = manifest.command.get("cwd")
    if isinstance(recorded, str) and recorded:
        candidate = Path(recorded)
        try:
            if candidate.is_dir():
                return candidate
        except OSError:  # pragma: no cover - an unreadable path is simply not usable
            pass
    try:
        return Path.cwd()
    except OSError:  # pragma: no cover - defensive
        return Path(".")


def _normalise(name: str) -> str:
    return _NORMALISE.sub("-", name).lower()


def _names(values: list[str]) -> str:
    if len(values) <= _NAMES_SHOWN:
        return ", ".join(values)
    shown = ", ".join(values[:_NAMES_SHOWN])
    return f"{shown} and {len(values) - _NAMES_SHOWN} more"


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
