"""Does this run's world still match what was recorded?

``provenance.json`` records the commit, the uncommitted diff, the interpreter, the
platform, the resolved package set and the dataset digests that a run was executed
against. This module asks, field by field, whether each of those is still true here — and
answers in three states rather than two.

**The third state is the whole point.** A pass/fail verifier has to guess when it cannot
answer, and every guess it makes is wrong in the direction that destroys the feature: a
missing ``git`` binary reported as "drifted" teaches the user that drift means nothing,
and reported as "reproducible" teaches them that reproducible means nothing. So
:class:`CheckStatus` has ``UNKNOWN``, and the rule behind every branch below is that a
check may only say ``DRIFT`` when this module actually asked the question and got an
answer that differs. Not knowing is never drift.

Three consequences of that rule are worth stating because they look like omissions:

- **A claim the manifest never made produces no check at all.** A repository with no
  origin is not "unverified remote", it is a repository whose manifest recorded no remote,
  so there is nothing to compare and no check is emitted. Emitting an ``UNKNOWN`` for every
  field a capture legitimately left empty would make every honest run unverifiable.
- **The patch check accepts two different truths.** ``git apply --check`` succeeds against
  a clean checkout of the base commit, and fails against the very tree the patch was taken
  from — because those changes are already there. Both are the recorded world. So the
  patch is tried forwards and then in reverse, and either one means the uncommitted work is
  intact.
- **An OS patch level is not drift.** ``platform.release`` moves whenever the machine takes
  an update and moving it changes nothing about a model, so it is reported in the detail of
  the ``platform.system`` check rather than as a check that can fail. ``system`` and
  ``machine`` — a different OS, a different architecture — are drift.
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

#: ``git apply --check`` reads a patch this process already holds in memory and touches no
#: network, so a long budget only buys a hang. Longer than the metadata reads in
#: ``provenance.py`` because the check does stat every path the patch names.
_APPLY_TIMEOUT: float = 60.0

#: How many names a drift detail spells out before it summarises the rest. A package
#: comparison that prints four hundred names is a wall nobody reads; the count carries the
#: magnitude and the names carry the shape.
_NAMES_SHOWN: int = 5

_NORMALISE = re.compile(r"[-_.]+")


class CheckStatus(str, Enum):
    """``UNKNOWN`` is not a soft ``DRIFT``. It means the question was not answered.

    Inherits from :class:`str` so a status can be compared against a raw string and written
    into JSON without unwrapping, matching :class:`~mlexperimenttracker.contract.RunState`.
    """

    OK = "ok"
    DRIFT = "drift"
    UNKNOWN = "unknown"


class Verdict(str, Enum):
    """The one-word summary. ``DRIFTED`` outranks ``UNVERIFIABLE`` deliberately: a report
    holding one established difference and one unanswered question is a report about a
    difference, and burying it under "unverifiable" would hide the finding."""

    REPRODUCIBLE = "reproducible"
    DRIFTED = "drifted"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class Check:
    """One question, its answer, and both sides of the comparison.

    ``expected`` and ``actual`` are the machine-readable halves — a commit against a
    commit, a version against a version — and ``detail`` is the sentence a human reads.
    Both are populated even when the status is ``OK``, because "verified against what?" is
    the first thing anybody asks of a green result.
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
    """Every check plus the verdict derived from them.

    Frozen, and the verdict is computed once at construction time by :func:`verify` rather
    than derived on access, so a report cannot be handed around in a state where its
    verdict and its checks disagree.
    """

    run_id: str
    project: str
    checks: tuple[Check, ...] = ()
    verdict: Verdict = Verdict.UNVERIFIABLE

    def to_dict(self) -> dict[str, Any]:
        """The API shape. ``summary`` is counts rather than a second opinion — a caller
        rendering a badge needs the magnitude without walking the list."""
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
        """The check called ``name``, or ``None``. Reports are short and a caller that
        wants one field should not have to filter a list to find out it is absent."""
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
    """Compare the recorded world against this one.

    ``cwd`` is the directory whose repository is inspected. When it is omitted the
    manifest's own ``command.cwd`` is used if that directory still exists, and the process
    working directory otherwise — a run knows where it was executed, and verifying the
    wrong repository produces a confident answer to a question nobody asked. The fallback
    order is reported in the ``git.repository`` check so the answer is never anonymous.

    ``rehash_datasets`` re-reads every recorded dataset. It defaults to on because a
    dataset replaced in place is the drift a commit hash cannot see and the reason this
    feature exists — but hashing is the one part of verification that can take minutes, so
    a caller with a large corpus and a fast answer to give can turn it off.

    Never raises for a run it cannot verify: an absent manifest, an unreadable repository
    and a dataset path that has moved are all ordinary states of the world, and each
    produces a report rather than an exception.
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
    """Only when the manifest recorded one: a repository with no origin makes no claim.

    Comparing origin URLs is what catches the mistake nothing else catches — verifying
    against a *different* repository that happens to have the same branch names, where
    every other check would be answered confidently and wrongly.
    """
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
    """Does the recorded commit still exist *here*?

    A missing object is drift and not merely unknown: the question was asked and the
    repository answered. The detail carries the caveat that matters — the commit may be
    alive on a remote and one fetch away — because the fix differs completely between
    "never pushed and now garbage collected" and "not fetched yet".
    """
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
    """Clean-versus-dirty only. Whether the *same* dirt is present is the patch check.

    Reporting "dirty, as recorded" as ``OK`` is not a loophole: this check answers one
    question and says which. A run captured dirty with no patch — ``capture_diff`` off —
    genuinely cannot be checked any further, and the detail says that rather than implying
    the content was compared.
    """
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
    """Is the recorded uncommitted work still intact?

    Tried forwards and then in reverse, because both directions are the same truth seen
    from different trees: forwards succeeds against a clean checkout of the base commit,
    reverse succeeds against the tree the patch was captured from, where the changes are
    already present. A patch that does neither has diverged — but only if its base commit
    is what is checked out. Against some other commit, a failure says nothing about the
    patch, so it is ``UNKNOWN``.

    Both attempts run under the repository's own ``core.autocrlf`` rather than an override
    — see :func:`_apply_check`, where getting this backwards makes a freshly captured run
    verify as DRIFTED on any stock Windows checkout.
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
    """``(toplevel, None)`` for a work tree, ``(None, reason)`` for anything else.

    Public because :mod:`~mlexperimenttracker.replay` asks the same question of the same
    manifest and the answer must be the same one. The toplevel rather than the directory
    itself, because a patch names paths relative to the root of the repository and
    ``git apply`` resolves them against the process working directory.
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
    """``(dirty, answered)``. Untracked files count, exactly as they do at capture."""
    result = _git(repo, "status", "--porcelain")
    if result is None or result.returncode != 0:
        return False, False
    return bool(result.stdout.strip()), True


def _apply_check(repo: Path, patch: bytes, *, reverse: bool) -> int | None:
    """``git apply --check`` over stdin. ``None`` when git could not be run at all.

    The patch is piped rather than written to a temporary file because :class:`Storage` is
    the only thing in this package that opens a file, and a check has no business creating
    one. ``--binary`` because the captured diff carries literal binary hunks.

    **``core.autocrlf`` is deliberately not overridden here**, which is the opposite of what
    :mod:`~mlexperimenttracker.replay` does, and the asymmetry is the point: override the
    line-ending conversion only when you own both sides of it. Replay *creates* the worktree
    it applies into, so it pins the checkout and the apply to the same setting and the two
    cannot disagree. Verification is handed a worktree somebody else checked out, under
    configuration this module did not choose — and on Windows that configuration is
    ``core.autocrlf=true`` by system default, so the files on disk are CRLF while the patch,
    being repository content, is LF. Forcing the conversion off there tells git the working
    tree is verbatim repository content when it is not, and an intact patch is refused: the
    run verifies as DRIFTED one second after capture, naming the uncommitted work as the
    thing that changed. Left alone, git applies the repository's own conversion on both
    sides and the comparison is the one the user's checkout actually implies.
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
    """One git command, or ``None`` if it could not be run.

    Deliberately the same invocation policy as capture — explicit ``cwd``, a timeout,
    ``check=False``, never a shell — by calling the same function rather than by
    reimplementing it. A verifier that inspected a repository differently from the way it
    was captured would be comparing two things it measured with two rulers.
    """
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
    """``system`` and ``machine`` only.

    ``release`` and ``processor`` are recorded and are worth reading, but they move for
    reasons that do not change a result — a security update, a CPU string reported
    differently by two kernels — so they are reported inside the ``platform.system``
    detail rather than as checks that can fail. A check that cries wolf is a check people
    learn to ignore, and the ones that matter are in the same list.
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
    """Added, removed and changed, with the count first and the names after.

    Names are compared PEP 503-normalised — ``ruamel.yaml``, ``ruamel-yaml`` and
    ``ruamel_yaml`` are one distribution — because otherwise a manifest written by one
    tool and read by another reports the same package as both added and removed. The
    *recorded* spelling is what gets printed, since that is what the user would search for.
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
    """Re-hash each recorded dataset. A path that is not there is ``UNKNOWN``, not drift.

    Dataset paths are recorded as the user gave them, which is usually relative, so a
    missing file is at least as likely to mean "verified from a different directory" as
    "the data was deleted". Saying ``DRIFT`` there would be the exact failure this module
    is built to avoid: an accusation dressed as a measurement.
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
    """Run one group of checks, or turn its failure into an ``UNKNOWN`` for that group.

    A verifier that dies half way through tells the user nothing about the half it did
    complete. The groups are independent by construction, so a failure in one is a gap in
    the report rather than a reason to discard it.
    """
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
