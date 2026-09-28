"""Rebuild a run's code from its provenance manifest without touching the user's tree.

The commit is checked out with ``git worktree add`` into a separate directory and the patch
is applied there; the original index, HEAD and files are left alone. Restores code and
lists package versions (not a lockfile). Data, drivers and hardware are not restored.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .provenance import (
    ENV_ALLOWLIST,
    GitState,
    Provenance,
    _git_env,
    _GitUnavailable,
    _run_git,
)
from .storage import Storage
from .verify import repository_root

__all__ = [
    "REQUIREMENTS_FILE",
    "ReplayError",
    "ReplayPlan",
    "ReplayStep",
    "materialise",
    "plan",
]

# Written into the worktree by materialise(). Named so it can't clobber a tracked
# requirements.txt.
REQUIREMENTS_FILE: str = "replay-requirements.txt"

# Env vars that describe the original machine rather than configure the run; not re-exported.
_DESCRIPTIVE_ENV: frozenset[str] = frozenset({"CONDA_DEFAULT_ENV", "VIRTUAL_ENV"})

# Checkouts can be slow, so these get longer timeouts than provenance.py's metadata reads.
_WORKTREE_TIMEOUT: float = 300.0
_APPLY_TIMEOUT: float = 120.0


class ReplayError(RuntimeError):
    """Raised by :func:`materialise` before anything is written. :func:`plan` never raises it."""


@dataclass(frozen=True)
class ReplayStep:
    """One instruction. ``command`` is None for a manual step.

    ``required`` is False for steps the run can be reconstructed without.
    """

    order: int
    description: str
    command: list[str] | None = None
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "description": self.description,
            "command": list(self.command) if self.command is not None else None,
            "required": self.required,
        }


@dataclass(frozen=True)
class ReplayPlan:
    """The steps, the caveats, and the package set to rebuild.

    ``warnings`` lists what the manifest can't guarantee (truncated patch, datasets, etc.)
    plus anything that went wrong in :func:`materialise`.
    """

    run_id: str
    project: str
    steps: tuple[ReplayStep, ...] = ()
    warnings: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "project": self.project,
            "target": self.target,
            "steps": [step.to_dict() for step in self.steps],
            "warnings": list(self.warnings),
            "requirements": list(self.requirements),
        }

    def as_script(self) -> str:
        """The plan as a POSIX shell script with ``set -eu``.

        Commands are the same arg lists :func:`materialise` runs. Quoting is POSIX, so on
        Windows treat it as a transcript.
        """
        lines = [
            "#!/bin/sh",
            f"# Replay plan for run {self.run_id} (project {self.project}).",
            "# Generated from provenance.json. Read it before you run it.",
        ]
        if self.warnings:
            lines.append("#")
            lines.extend(f"# WARNING: {warning}" for warning in self.warnings)
        lines.extend(["", "set -eu", ""])

        for step in self.steps:
            suffix = "" if step.required else "   (optional)"
            lines.append(f"# {step.order}. {step.description}{suffix}")
            if self.requirements and REQUIREMENTS_FILE in step.description:
                lines.append(f"cat > {shlex.quote(REQUIREMENTS_FILE)} <<'REPLAY_EOF'")
                lines.extend(self.requirements)
                lines.append("REPLAY_EOF")
            if step.command is None:
                lines.append("#    (no command: do this by hand)")
            else:
                rendered = " ".join(shlex.quote(part) for part in step.command)
                lines.append(rendered if step.required else f"# {rendered}")
            lines.append("")
        return "\n".join(lines)


def plan(
    storage: Storage,
    project: str,
    run_id: str,
    *,
    target: str | Path | None = None,
) -> ReplayPlan:
    """Describe the reconstruction without doing any of it.

    Reads the manifest only and runs no git, so it works even if the repo isn't on this
    machine. Without ``target`` the commands use a literal ``<target>`` placeholder.
    """
    manifest_dict = storage.read_provenance(project, run_id)
    if manifest_dict is None:
        return ReplayPlan(
            run_id=run_id,
            project=project,
            warnings=(
                "no provenance manifest: this run predates format 1.1 or its capture "
                "failed, so there is no recorded world to reconstruct.",
            ),
            target=str(target) if target is not None else None,
        )

    manifest = Provenance.from_dict(manifest_dict)
    where = str(target) if target is not None else "<target>"
    steps: list[ReplayStep] = []
    warnings: list[str] = []
    if target is None:
        warnings.append(
            "no target directory was given; replace <target> with an empty directory "
            "beside the repository, never inside it."
        )

    _plan_git(manifest.git, storage, project, run_id, where, steps, warnings)
    requirements = _requirements(manifest)
    _plan_packages(requirements, where, steps, warnings)
    _plan_environment(manifest, steps)
    _plan_command(manifest, where, steps, warnings)
    _plan_caveats(manifest, warnings)

    return ReplayPlan(
        run_id=run_id,
        project=project,
        steps=tuple(steps),
        warnings=tuple(warnings),
        requirements=tuple(requirements),
        target=str(target) if target is not None else None,
    )


def materialise(
    storage: Storage,
    project: str,
    run_id: str,
    target: str | Path,
    *,
    apply_patch: bool = True,
    cwd: str | Path | None = None,
) -> ReplayPlan:
    """Create a worktree at the recorded commit and apply the recorded patch into it.

    Returns :func:`plan`'s result with what happened added to its warnings. ``cwd`` picks
    the repo; default is the recorded cwd if it still exists, else the process cwd.

    Raises :class:`ReplayError` before writing anything if the target is non-empty, sits
    inside the repo, or the commit isn't reachable. A patch that fails to apply is only a
    warning; the worktree is kept.
    """
    target_path = Path(target)
    _refuse_unusable_target(target_path)

    manifest_dict = storage.read_provenance(project, run_id)
    if manifest_dict is None:
        raise ReplayError(
            f"run {run_id!r} has no provenance manifest: there is no recorded commit to "
            "check out. Runs written before format 1.1, and runs whose capture failed, "
            "cannot be replayed."
        )
    manifest = Provenance.from_dict(manifest_dict)
    git = manifest.git
    if not git.available or not git.commit:
        raise ReplayError(
            "the manifest recorded no commit: " + (git.reason or "git state was unavailable")
        )

    directory = _base_directory(manifest, cwd)
    repo, failure = repository_root(directory)
    if repo is None:
        raise ReplayError(f"no git repository to replay from at {directory}: {failure}")
    _refuse_target_inside(repo, target_path)

    if _git(repo, "cat-file", "-e", f"{git.commit}^{{commit}}")[0] != 0:
        raise ReplayError(
            f"commit {git.commit} is not in the repository at {repo}. It may exist only on "
            "a remote — fetch it and try again — or it may have been rewritten away."
        )

    base = plan(storage, project, run_id, target=target_path)
    warnings = list(base.warnings)

    # --detach: don't leave a branch per replay. core.autocrlf=false: the Git for Windows
    # default converts line endings and the patch then fails to apply.
    code, output = _git(
        repo,
        "-c",
        "core.autocrlf=false",
        "worktree",
        "add",
        "--detach",
        str(target_path),
        git.commit,
        timeout=_WORKTREE_TIMEOUT,
    )
    if code != 0:
        raise ReplayError(f"git worktree add failed at {target_path}: {output or 'no output'}")
    warnings.append(
        f"a worktree is now registered in {repo}; remove it with "
        f"`git worktree remove {target_path}` when you are done with it."
    )

    if apply_patch and git.diff_file:
        warnings.extend(_apply_patch(storage, project, run_id, git, target_path))
    elif git.diff_file:
        warnings.append(
            "the recorded patch was not applied (apply_patch=False): this worktree is the "
            "commit, not the tree the run was executed against."
        )

    if base.requirements:
        try:
            storage.write_bytes(
                target_path / REQUIREMENTS_FILE,
                ("\n".join(base.requirements) + "\n").encode("utf-8"),
            )
        except Exception as exc:  # pragma: no cover - defensive: the worktree just succeeded
            warnings.append(f"could not write {REQUIREMENTS_FILE}: {exc}")

    return replace(base, warnings=tuple(warnings))


# --------------------------------------------------------------------------------------
# Plan construction
# --------------------------------------------------------------------------------------


def _plan_git(
    git: GitState,
    storage: Storage,
    project: str,
    run_id: str,
    where: str,
    steps: list[ReplayStep],
    warnings: list[str],
) -> None:
    if not git.available or not git.commit:
        warnings.append(
            "no commit was recorded, so the code cannot be restored: "
            + (git.reason or "git state was unavailable at capture")
        )
        return

    steps.append(
        ReplayStep(
            order=len(steps) + 1,
            description=f"From inside the repository, check the recorded commit out into "
            f"{where} — beside the working tree rather than over it",
            command=[
                "git",
                "-c",
                "core.autocrlf=false",
                "worktree",
                "add",
                "--detach",
                where,
                git.commit,
            ],
        )
    )

    if not git.diff_file:
        if git.dirty:
            warnings.append(
                "the tree was dirty at capture but no patch was recorded, so the "
                "uncommitted work is not recoverable: " + (git.reason or "reason not recorded")
            )
        return

    run_dir = storage.run_path(project, run_id)
    patch_path = str(run_dir / git.diff_file) if run_dir is not None else git.diff_file
    if git.diff_truncated:
        warnings.append(
            f"the recorded patch was truncated at {git.diff_bytes} bytes and will not "
            "apply; it is evidence of what was uncommitted, not a restore point."
        )
    steps.append(
        ReplayStep(
            order=len(steps) + 1,
            description="Apply the uncommitted changes the run was executed with",
            command=["git", "-C", where, "-c", "core.autocrlf=false", "apply", "--binary", patch_path],
            required=not git.diff_truncated,
        )
    )


def _plan_packages(
    requirements: list[str], where: str, steps: list[ReplayStep], warnings: list[str]
) -> None:
    if not requirements:
        warnings.append(
            "no package versions were recorded, so the environment cannot be rebuilt from "
            "this manifest."
        )
        return
    steps.append(
        ReplayStep(
            order=len(steps) + 1,
            description=f"Write the {len(requirements)} recorded distributions to "
            f"{REQUIREMENTS_FILE} in {where}",
            command=None,
        )
    )
    steps.append(
        ReplayStep(
            order=len(steps) + 1,
            description="Install them into a fresh environment, never into the current one",
            command=["pip", "install", "-r", REQUIREMENTS_FILE],
        )
    )


def _plan_environment(manifest: Provenance, steps: list[ReplayStep]) -> None:
    """Add a manual step exporting the env vars that affect the run (not venv/conda names)."""
    settable = {
        name: value
        for name, value in manifest.environment.items()
        if name in ENV_ALLOWLIST and name not in _DESCRIPTIVE_ENV
    }
    if not settable:
        return
    rendered = " ".join(f"{name}={value}" for name, value in sorted(settable.items()))
    steps.append(
        ReplayStep(
            order=len(steps) + 1,
            description=f"Export the environment the run was executed with: {rendered}",
            command=None,
        )
    )


def _plan_command(
    manifest: Provenance, where: str, steps: list[ReplayStep], warnings: list[str]
) -> None:
    argv = manifest.command.get("argv")
    if not isinstance(argv, list) or not argv:
        warnings.append("no command was recorded, so there is nothing to re-run.")
        return
    parts = [str(part) for part in argv]
    # argv[0] is the script, so prepend an interpreter. Use `python` from PATH; the recorded
    # executable path is from the original machine.
    command = ["python", *parts] if parts[0].endswith(".py") else parts
    steps.append(
        ReplayStep(
            order=len(steps) + 1,
            description=f"Re-run the recorded command from inside {where}",
            command=command,
        )
    )
    recorded_cwd = manifest.command.get("cwd")
    if isinstance(recorded_cwd, str) and recorded_cwd:
        warnings.append(
            f"the run was executed from {recorded_cwd}; paths it used that were outside "
            "the repository are not reconstructed by this plan."
        )


def _plan_caveats(manifest: Provenance, warnings: list[str]) -> None:
    if manifest.packages:
        warnings.append(
            "the recorded package set is resolved versions, not a lockfile: it does not "
            "record how they were resolved, and a distribution installed from a local path "
            "or withdrawn from an index cannot be reinstalled from a version string."
        )
    paths = [
        str(entry.get("path"))
        for entry in manifest.datasets
        if isinstance(entry.get("path"), str)
    ]
    if paths:
        warnings.append(
            "datasets are not restored, only recorded: check "
            + ", ".join(paths[:5])
            + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")
            + " with `verify` before trusting a replayed result."
        )
    gpus = manifest.hardware.get("gpus")
    if isinstance(gpus, list) and gpus:
        names = ", ".join(str(gpu.get("name")) for gpu in gpus if isinstance(gpu, dict))
        warnings.append(
            f"the run used {names}; hardware, driver and kernel nondeterminism are outside "
            "what any manifest can restore."
        )


def _requirements(manifest: Provenance) -> list[str]:
    """``name==version`` per recorded distribution, sorted by lowercased name.

    Entries without a version are dropped; a bare name would install the latest.
    """
    lines: list[str] = []
    for name, version in sorted(manifest.packages.items(), key=lambda item: item[0].lower()):
        if not name or not version:
            continue
        lines.append(f"{name}=={version}")
    return lines


# --------------------------------------------------------------------------------------
# Materialise internals
# --------------------------------------------------------------------------------------


def _refuse_unusable_target(target: Path) -> None:
    """Raise if the target exists and is not an empty directory."""
    if not target.exists():
        return
    if not target.is_dir():
        raise ReplayError(f"target {target} exists and is not a directory")
    try:
        occupied = any(target.iterdir())
    except OSError as exc:
        raise ReplayError(f"target {target} could not be read: {exc}") from exc
    if occupied:
        raise ReplayError(
            f"target {target} is not empty. Replay never writes into a directory that "
            "already holds something — pick a new path, or empty this one yourself."
        )


def _refuse_target_inside(repo: Path, target: Path) -> None:
    try:
        resolved_repo = repo.resolve()
        resolved_target = target.resolve()
    except OSError:  # pragma: no cover - defensive
        return
    if resolved_target == resolved_repo or resolved_repo in resolved_target.parents:
        raise ReplayError(
            f"target {target} is inside the repository at {repo}. A worktree nested in the "
            "tree it came from appears as untracked files in the original, which is the "
            "kind of change replay refuses to make. Choose a path outside it."
        )


def _apply_patch(
    storage: Storage, project: str, run_id: str, git: GitState, target: Path
) -> list[str]:
    """Apply the stored patch inside the new worktree. Returns warnings, never raises."""
    patch = storage.read_patch(project, run_id)
    if not patch:
        return [
            f"the manifest names {git.diff_file} but the file is not in the run directory; "
            "this worktree is the commit without the uncommitted work."
        ]
    if git.diff_truncated:
        return [
            "the recorded patch was truncated at capture and was not applied; this "
            "worktree is the commit without the uncommitted work."
        ]

    code, output = _git_input(
        target, patch, "-c", "core.autocrlf=false", "apply", "--binary", "-"
    )
    if code == 0:
        return []
    return [
        "THE RECORDED PATCH DID NOT APPLY, so this worktree is the commit without the "
        f"uncommitted work: {output or 'git apply gave no output'}"
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


def _git(repo: Path, *args: str, timeout: float | None = None) -> tuple[int, str]:
    """Run one git command via provenance's runner.

    Returns ``(status, output)``; status is -1 if git couldn't be run at all.
    """
    try:
        result = _run_git(list(args), repo, timeout) if timeout else _run_git(list(args), repo)
    except _GitUnavailable as exc:
        return -1, str(exc)
    return result.returncode, (result.stderr or result.stdout or "").strip()


def _git_input(repo: Path, payload: bytes, *args: str) -> tuple[int, str]:
    """Run git with bytes on stdin.

    The patch is piped instead of written to a temp file, so no second copy of it (it may
    hold secrets) lands on disk.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            input=payload,
            capture_output=True,
            timeout=_APPLY_TIMEOUT,
            check=False,
            env=_git_env(),
            shell=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    return result.returncode, stderr or stdout
