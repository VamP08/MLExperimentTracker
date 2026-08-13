"""Verification and replay, against real repositories rather than mocks.

Every git assertion below runs against a throwaway repository built with the real ``git``
binary in ``tmp_path``, for the reason ``test_provenance.py`` gives: what breaks in this
half of the feature is never the command that was typed, it is what git actually answers —
a patch that will not apply to the tree it was captured from, a commit that survives a
``gc`` because a stale ref still points at it, a worktree that git refuses because the
target is inside the repository.

Two tests carry more weight than the rest and are worth naming here. One asserts that a run
verified immediately after capture is **reproducible** — if that ever goes red the feature
is worthless, because it is the baseline every other answer is measured from. The other
asserts that after ``materialise`` the original working tree is **byte for byte what it
was**: that is the promise that makes this safe to point at a repository somebody is
working in, and it is the promise a checkout-based implementation would break.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mlexperimenttracker import contract
from mlexperimenttracker.provenance import capture, collect_packages, hash_path
from mlexperimenttracker.replay import (
    REQUIREMENTS_FILE,
    ReplayError,
    materialise,
    plan,
)
from mlexperimenttracker.storage import Storage
from mlexperimenttracker.verify import CheckStatus, Verdict, verify

BINARY_BLOB = bytes(range(256)) * 8
DIRTY_TEXT = "def main():\n    # uncommitted work\n    return 42\n"


# --------------------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    """Run git for test setup. Unlike the modules under test, this one is allowed to fail."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=120,
    )
    return result.stdout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _configure(repo: Path) -> None:
    # Local config only, overriding the developer's global config. ``core.autocrlf`` is the
    # one that matters: with the Git for Windows default of ``true`` the checkout and the
    # patch disagree about line endings and every apply in this file would fail.
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Test Runner")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one commit on ``main``: a text file, a binary file, a gitignore."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _configure(repo)
    _write(repo / "train.py", "def main():\n    return 0\n")
    _write(repo / ".gitignore", "ignored/\n*.log\n")
    (repo / "model.bin").write_bytes(BINARY_BLOB)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    return repo


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "store")


def _dirty(repo: Path) -> None:
    """Uncommitted work of all three kinds the patch has to carry."""
    _write(repo / "train.py", DIRTY_TEXT)
    (repo / "model.bin").write_bytes(BINARY_BLOB[::-1])
    _write(repo / "staged.py", "STAGED = True\n")
    _git(repo, "add", "staged.py")


def _record(
    storage: Storage,
    repo: Path,
    *,
    run_id: str = "proj_run_0001",
    datasets: list[dict] | None = None,
    capture_diff: bool = True,
) -> tuple[str, str]:
    """Capture provenance for ``repo`` and file it under a real run directory."""
    storage.create_run("proj", run_id, {"created_at": "2026-08-13T10:00:00+05:30"})
    manifest, patch = capture(repo, capture_diff=capture_diff, datasets=datasets)
    assert storage.write_provenance("proj", run_id, manifest.to_dict(), patch) is True
    return "proj", run_id


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every tracked-or-not file under ``root`` except git's own bookkeeping.

    ``.git`` is excluded deliberately and is the one thing ``materialise`` is allowed to
    change: registering a worktree writes there. Everything a user would call "my work" is
    in this mapping.
    """
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        if path.is_file():
            files[relative.as_posix()] = path.read_bytes()
    return files


# --------------------------------------------------------------------------------------
# verify — the baseline
# --------------------------------------------------------------------------------------


def test_a_clean_run_verified_immediately_after_capture_is_reproducible(
    storage: Storage, repo: Path
) -> None:
    project, run_id = _record(storage, repo)

    report = verify(storage, project, run_id, cwd=repo)

    assert report.verdict is Verdict.REPRODUCIBLE, [c.to_dict() for c in report.checks]
    assert all(check.status is CheckStatus.OK for check in report.checks)
    assert report.by_name("git.head") is not None
    assert report.by_name("git.commit").status is CheckStatus.OK
    assert report.by_name("packages").status is CheckStatus.OK
    assert report.by_name("python.version").status is CheckStatus.OK
    # No patch was captured from a clean tree, so there is no claim to check and no check.
    assert report.by_name("git.patch") is None


def test_a_dirty_run_verified_immediately_after_capture_is_reproducible(
    storage: Storage, repo: Path
) -> None:
    """The case a forward-only ``git apply --check`` gets wrong.

    The patch is the tree's own uncommitted work, so it cannot be applied *again* on top of
    itself — a verifier that only tried forwards would call an untouched tree drifted, one
    second after capture, and nobody would ever trust the word again.
    """
    _dirty(repo)
    project, run_id = _record(storage, repo)

    report = verify(storage, project, run_id, cwd=repo)

    patch_check = report.by_name("git.patch")
    assert patch_check.status is CheckStatus.OK, patch_check.detail
    assert "already contains" in patch_check.detail
    assert report.by_name("git.worktree").status is CheckStatus.OK
    assert report.verdict is Verdict.REPRODUCIBLE, [c.to_dict() for c in report.checks]


def test_a_dirty_run_verifies_on_a_crlf_checkout(
    storage: Storage, tmp_path: Path
) -> None:
    """The same baseline, on the configuration every stock Windows machine actually has.

    ``core.autocrlf=true`` is the Git for Windows *system* default, and under it the working
    tree holds CRLF while the index — and therefore the captured patch, which is repository
    content — holds LF. Every other repository in this file pins ``core.autocrlf=false``, so
    their working trees are LF and the distinction never shows up; a verifier that forced the
    conversion off passed all of them and still called a freshly captured run DRIFTED on a
    developer's own laptop, naming the uncommitted work as the thing that had changed.

    Written with explicit ``\\r\\n`` rather than by trusting a checkout, so the tree is CRLF
    on every platform and this test fails on Linux too if the override comes back.
    """
    repo = tmp_path / "crlf-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _configure(repo)
    _git(repo, "config", "core.autocrlf", "true")
    (repo / "train.py").write_bytes(b"def main():\r\n    return 0\r\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    (repo / "train.py").write_bytes(b"def main():\r\n    # uncommitted\r\n    return 42\r\n")

    project, run_id = _record(storage, repo)
    report = verify(storage, project, run_id, cwd=repo)

    patch_check = report.by_name("git.patch")
    assert patch_check.status is CheckStatus.OK, patch_check.detail
    assert report.verdict is Verdict.REPRODUCIBLE, [c.to_dict() for c in report.checks]


def test_the_patch_check_passes_against_a_clean_checkout_of_the_base_commit(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    """The other direction of the same truth: forwards, onto a tree without the changes."""
    _dirty(repo)
    project, run_id = _record(storage, repo)
    _git(repo, "stash", "--include-untracked")

    report = verify(storage, project, run_id, cwd=repo)

    patch_check = report.by_name("git.patch")
    assert patch_check.status is CheckStatus.OK, patch_check.detail
    assert "applies to the tree" in patch_check.detail
    # The tree is clean now and the manifest says it was dirty: that much did drift, and
    # the report says so on the check that asks about it rather than on the patch check.
    assert report.by_name("git.worktree").status is CheckStatus.DRIFT
    assert report.verdict is Verdict.DRIFTED


# --------------------------------------------------------------------------------------
# verify — drift
# --------------------------------------------------------------------------------------


def test_a_new_commit_drifts_head_without_disturbing_the_recorded_commit(
    storage: Storage, repo: Path
) -> None:
    project, run_id = _record(storage, repo)
    recorded = storage.read_provenance(project, run_id)["git"]["commit"]
    _write(repo / "train.py", "def main():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "second")

    report = verify(storage, project, run_id, cwd=repo)

    head = report.by_name("git.head")
    assert head.status is CheckStatus.DRIFT
    assert head.expected == recorded
    assert head.actual != recorded and len(head.actual) == 40
    # The commit itself is still in the repository — a new commit on top of it does not
    # remove it, and reporting both as drift would make the report useless for deciding
    # whether the code is still recoverable.
    assert report.by_name("git.commit").status is CheckStatus.OK
    assert report.verdict is Verdict.DRIFTED


def test_a_commit_missing_from_this_repository_is_drift_and_never_a_crash(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    """Verifying against the wrong repository, which is what a fresh clone of a fork is."""
    project, run_id = _record(storage, repo)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    _configure(other)
    _write(other / "unrelated.py", "x = 1\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "unrelated")

    report = verify(storage, project, run_id, cwd=other)

    commit_check = report.by_name("git.commit")
    assert commit_check.status is CheckStatus.DRIFT
    assert "may still exist on a remote" in commit_check.detail
    assert report.by_name("git.head").status is CheckStatus.DRIFT
    assert report.verdict is Verdict.DRIFTED


def test_a_garbage_collected_commit_is_drift_and_never_a_crash(
    storage: Storage, repo: Path
) -> None:
    """The real deletion: the commit is unreferenced and pruned out of the object store."""
    _write(repo / "train.py", "def main():\n    return 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "second")
    project, run_id = _record(storage, repo)
    _git(repo, "reset", "--hard", "HEAD~1")
    # ORIG_HEAD is a ref like any other and keeps the object alive through a prune, which
    # is exactly why "I reset, so it is gone" is wrong often enough to be worth testing.
    (repo / ".git" / "ORIG_HEAD").unlink(missing_ok=True)
    _git(repo, "reflog", "expire", "--expire=now", "--all")
    _git(repo, "gc", "--prune=now")

    report = verify(storage, project, run_id, cwd=repo)

    assert report.by_name("git.commit").status is CheckStatus.DRIFT
    assert report.verdict is Verdict.DRIFTED


def test_package_drift_is_detected_and_counted(storage: Storage, repo: Path) -> None:
    project, run_id = _record(storage, repo)
    manifest = storage.read_provenance(project, run_id)
    packages = dict(manifest["packages"])
    assert len(packages) >= 2, "the test environment has too few distributions to compare"

    uninstalled = sorted(packages)[0]
    packages.pop(uninstalled)              # installed but not recorded -> added
    packages["not-a-real-distribution"] = "9.9.9"  # recorded but not installed -> removed
    downgraded = sorted(packages)[0]
    packages[downgraded] = "0.0.0.not-a-real-version"  # -> changed
    manifest["packages"] = packages
    storage.write_provenance(project, run_id, manifest, storage.read_patch(project, run_id))

    report = verify(storage, project, run_id, cwd=repo)

    check = report.by_name("packages")
    assert check.status is CheckStatus.DRIFT
    assert check.expected == len(packages)
    assert check.actual == len(collect_packages())
    assert "1 added" in check.detail and uninstalled in check.detail
    assert "1 removed" in check.detail and "not-a-real-distribution" in check.detail
    assert "1 changed" in check.detail and "0.0.0.not-a-real-version ->" in check.detail
    assert report.verdict is Verdict.DRIFTED


def test_package_names_are_compared_normalised(storage: Storage, repo: Path) -> None:
    """``ruamel.yaml`` and ``ruamel-yaml`` are one distribution, not one added and one
    removed. Without normalisation a manifest written by one tool and read by another
    reports drift in packages nobody touched."""
    project, run_id = _record(storage, repo)
    manifest = storage.read_provenance(project, run_id)
    manifest["packages"] = {
        name.upper().replace("-", "_"): version for name, version in manifest["packages"].items()
    }
    storage.write_provenance(project, run_id, manifest, None)

    report = verify(storage, project, run_id, cwd=repo)

    assert report.by_name("packages").status is CheckStatus.OK


def test_a_replaced_dataset_is_drift_and_a_missing_one_is_not(
    storage: Storage, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(repo / "data" / "train.csv", "a,b\n1,2\n")
    monkeypatch.chdir(repo)
    entry = hash_path("data/train.csv")
    assert "error" not in entry
    project, run_id = _record(storage, repo, datasets=[entry])

    assert verify(storage, project, run_id, cwd=repo).by_name(
        "dataset:data/train.csv"
    ).status is CheckStatus.OK

    _write(repo / "data" / "train.csv", "a,b\n1,3\n")
    replaced = verify(storage, project, run_id, cwd=repo).by_name("dataset:data/train.csv")
    assert replaced.status is CheckStatus.DRIFT
    assert replaced.expected != replaced.actual

    (repo / "data" / "train.csv").unlink()
    missing = verify(storage, project, run_id, cwd=repo).by_name("dataset:data/train.csv")
    # Not drift: a relative path that is not there is at least as likely to mean the
    # verification ran from the wrong directory as it is to mean the data was deleted.
    assert missing.status is CheckStatus.UNKNOWN
    assert "wrong directory" in missing.detail


def test_rehashing_can_be_turned_off(
    storage: Storage, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(repo / "data" / "train.csv", "a,b\n1,2\n")
    monkeypatch.chdir(repo)
    project, run_id = _record(storage, repo, datasets=[hash_path("data/train.csv")])
    _write(repo / "data" / "train.csv", "a,b\n9,9\n")

    report = verify(storage, project, run_id, cwd=repo, rehash_datasets=False)

    assert report.by_name("dataset:data/train.csv") is None
    assert report.verdict is Verdict.REPRODUCIBLE


# --------------------------------------------------------------------------------------
# verify — the unanswerable
# --------------------------------------------------------------------------------------


def test_a_run_with_no_manifest_is_unverifiable_and_raises_nothing(storage: Storage) -> None:
    storage.create_run("proj", "proj_run_0009", {"created_at": "2026-08-13T10:00:00+05:30"})

    report = verify(storage, "proj", "proj_run_0009")

    assert report.verdict is Verdict.UNVERIFIABLE
    assert len(report.checks) == 1
    assert report.checks[0].name == "manifest"
    assert report.checks[0].status is CheckStatus.UNKNOWN
    assert "predates format 1.1" in report.checks[0].detail


def test_an_unaddressable_name_is_unverifiable_rather_than_an_error(storage: Storage) -> None:
    report = verify(storage, "..", "run")

    assert report.verdict is Verdict.UNVERIFIABLE
    assert report.checks[0].name == "manifest"


def test_verifying_outside_a_repository_is_unknown_not_drift(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    project, run_id = _record(storage, repo)
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()

    report = verify(storage, project, run_id, cwd=elsewhere)

    repository = report.by_name("git.repository")
    assert repository.status is CheckStatus.UNKNOWN
    assert report.by_name("git.commit") is None
    assert report.verdict is Verdict.UNVERIFIABLE
    # The half of the manifest that has nothing to do with git is still answered.
    assert report.by_name("python.version").status is CheckStatus.OK


def test_a_manifest_with_no_git_state_is_unknown_not_drift(storage: Storage) -> None:
    storage.create_run("proj", "proj_run_0010", {"created_at": "2026-08-13T10:00:00+05:30"})
    storage.write_provenance(
        "proj",
        "proj_run_0010",
        {
            "captured_at": "2026-08-13T10:00:00+05:30",
            "git": {"available": False, "reason": "not a git work tree"},
            "python": {},
            "platform": {},
            "packages": {},
            "hardware": {},
            "environment": {},
            "datasets": [],
            "command": {},
        },
        None,
    )

    report = verify(storage, "proj", "proj_run_0010")

    assert report.by_name("git.repository").status is CheckStatus.UNKNOWN
    assert "not a git work tree" in report.by_name("git.repository").detail
    assert report.verdict is Verdict.UNVERIFIABLE


def test_a_deleted_patch_is_unknown_not_drift(storage: Storage, repo: Path) -> None:
    """The patch is the one file a user is told they may delete, so its absence must not
    be reported as if the tree had changed."""
    _dirty(repo)
    project, run_id = _record(storage, repo)
    (storage.run_path(project, run_id) / contract.PATCH_FILE).unlink()

    check = verify(storage, project, run_id, cwd=repo).by_name("git.patch")

    assert check.status is CheckStatus.UNKNOWN
    assert "record is incomplete" in check.detail


def test_an_edited_patch_is_unknown_not_drift(storage: Storage, repo: Path) -> None:
    _dirty(repo)
    project, run_id = _record(storage, repo)
    (storage.run_path(project, run_id) / contract.PATCH_FILE).write_bytes(b"not the patch\n")

    check = verify(storage, project, run_id, cwd=repo).by_name("git.patch")

    assert check.status is CheckStatus.UNKNOWN
    assert "not the patch that was captured" in check.detail


def test_a_truncated_patch_is_unknown_because_it_cannot_apply(
    storage: Storage, repo: Path
) -> None:
    _dirty(repo)
    storage.create_run("proj", "proj_run_0011", {"created_at": "2026-08-13T10:00:00+05:30"})
    manifest, patch = capture(repo, diff_limit=64)
    assert manifest.git.diff_truncated is True
    storage.write_provenance("proj", "proj_run_0011", manifest.to_dict(), patch)

    check = verify(storage, "proj", "proj_run_0011", cwd=repo).by_name("git.patch")

    assert check.status is CheckStatus.UNKNOWN
    assert "evidence rather than a restore point" in check.detail


def test_a_changed_patch_at_the_recorded_commit_is_drift(storage: Storage, repo: Path) -> None:
    """The one case where a failed apply *is* established drift: same commit checked out,
    and the tree neither contains the patch nor accepts it."""
    _dirty(repo)
    project, run_id = _record(storage, repo)
    _write(repo / "train.py", "def main():\n    return 'something else entirely'\n")

    check = verify(storage, project, run_id, cwd=repo).by_name("git.patch")

    assert check.status is CheckStatus.DRIFT
    assert "the uncommitted work has changed" in check.detail


def test_a_different_origin_is_drift(storage: Storage, repo: Path) -> None:
    _git(repo, "remote", "add", "origin", "https://example.invalid/one.git")
    project, run_id = _record(storage, repo)
    _git(repo, "remote", "set-url", "origin", "https://example.invalid/two.git")

    check = verify(storage, project, run_id, cwd=repo).by_name("git.remote")

    assert check.status is CheckStatus.DRIFT
    assert check.expected == "https://example.invalid/one.git"
    assert check.actual == "https://example.invalid/two.git"


def test_the_report_serialises_to_plain_json_types(storage: Storage, repo: Path) -> None:
    import json

    project, run_id = _record(storage, repo)

    payload = verify(storage, project, run_id, cwd=repo).to_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["verdict"] == "reproducible"
    assert payload["summary"]["ok"] == len(payload["checks"])
    assert {check["status"] for check in payload["checks"]} == {"ok"}


# --------------------------------------------------------------------------------------
# replay — the plan
# --------------------------------------------------------------------------------------


def test_a_plan_without_a_target_is_explicit_about_it(storage: Storage, repo: Path) -> None:
    _dirty(repo)
    project, run_id = _record(storage, repo)

    replay_plan = plan(storage, project, run_id)

    assert replay_plan.target is None
    assert any("<target>" in warning for warning in replay_plan.warnings)
    assert any("<target>" in (step.command or []) for step in replay_plan.steps)
    assert replay_plan.requirements, "the manifest recorded packages"
    assert all("==" in line for line in replay_plan.requirements)


def test_a_plan_carries_the_worktree_the_patch_and_the_command(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    _dirty(repo)
    project, run_id = _record(storage, repo)
    commit = storage.read_provenance(project, run_id)["git"]["commit"]

    replay_plan = plan(storage, project, run_id, target=tmp_path / "wt")

    commands = [step.command for step in replay_plan.steps if step.command]
    worktree = next(c for c in commands if "worktree" in c)
    assert worktree[:6] == ["git", "-c", "core.autocrlf=false", "worktree", "add", "--detach"]
    assert worktree[-1] == commit
    apply_command = next(c for c in commands if "apply" in c)
    assert apply_command[-1].endswith(contract.PATCH_FILE)
    assert ["pip", "install", "-r", REQUIREMENTS_FILE] in commands
    assert [step.order for step in replay_plan.steps] == list(
        range(1, len(replay_plan.steps) + 1)
    )


def test_a_plan_states_the_boundary_of_what_it_can_restore(
    storage: Storage, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The honest-boundary requirement, asserted rather than left to a README."""
    _write(repo / "data" / "train.csv", "a,b\n1,2\n")
    monkeypatch.chdir(repo)
    project, run_id = _record(storage, repo, datasets=[hash_path("data/train.csv")])

    warnings = " ".join(plan(storage, project, run_id).warnings)

    assert "not a lockfile" in warnings
    assert "datasets are not restored" in warnings
    assert "data/train.csv" in warnings


def test_a_plan_for_a_run_with_no_manifest_says_so_instead_of_failing(
    storage: Storage,
) -> None:
    storage.create_run("proj", "proj_run_0012", {"created_at": "2026-08-13T10:00:00+05:30"})

    replay_plan = plan(storage, "proj", "proj_run_0012")

    assert replay_plan.steps == ()
    assert "no provenance manifest" in replay_plan.warnings[0]


def test_the_script_carries_the_commands_the_warnings_and_the_requirements(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    _dirty(repo)
    project, run_id = _record(storage, repo)

    script = plan(storage, project, run_id, target=tmp_path / "wt").as_script()

    assert script.startswith("#!/bin/sh")
    assert "set -eu" in script
    assert "git -c core.autocrlf=false worktree add --detach" in script
    assert f"cat > {REQUIREMENTS_FILE} <<'REPLAY_EOF'" in script
    assert "REPLAY_EOF" in script
    assert "# WARNING:" in script
    # Every line of a step that is not required is commented out rather than dropped, so
    # reading the script still shows what a full reconstruction would have done.
    assert "pip install -r" in script


def test_the_plan_serialises_to_plain_json_types(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    import json

    _dirty(repo)
    project, run_id = _record(storage, repo)

    payload = plan(storage, project, run_id, target=tmp_path / "wt").to_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["steps"][0]["order"] == 1


# --------------------------------------------------------------------------------------
# replay — materialise
# --------------------------------------------------------------------------------------


def test_materialise_builds_a_worktree_at_the_commit_with_the_patch_applied(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    _dirty(repo)
    project, run_id = _record(storage, repo)
    commit = storage.read_provenance(project, run_id)["git"]["commit"]
    target = tmp_path / "replayed"

    result = materialise(storage, project, run_id, target, cwd=repo)

    assert _git(target, "rev-parse", "HEAD").strip() == commit
    assert (target / "train.py").read_text(encoding="utf-8") == DIRTY_TEXT
    assert (target / "model.bin").read_bytes() == BINARY_BLOB[::-1]
    assert (target / "staged.py").read_text(encoding="utf-8") == "STAGED = True\n"
    assert (target / REQUIREMENTS_FILE).read_text(encoding="utf-8").endswith("\n")
    assert not any("DID NOT APPLY" in warning for warning in result.warnings)
    assert any("git worktree remove" in warning for warning in result.warnings)


def test_materialise_leaves_the_original_working_tree_byte_identical(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    """The promise that makes this safe to run against a repository somebody is working in.

    A ``git checkout`` implementation of the same feature passes every other test in this
    file and fails this one by destroying the uncommitted work it was built to preserve.
    """
    _dirty(repo)
    project, run_id = _record(storage, repo)
    before = _snapshot(repo)
    head_before = _git(repo, "rev-parse", "HEAD")
    status_before = _git(repo, "status", "--porcelain")

    materialise(storage, project, run_id, tmp_path / "replayed", cwd=repo)

    assert _snapshot(repo) == before
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "status", "--porcelain") == status_before


def test_materialise_refuses_a_non_empty_target(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    project, run_id = _record(storage, repo)
    target = tmp_path / "occupied"
    target.mkdir()
    _write(target / "someones_work.py", "important = True\n")

    with pytest.raises(ReplayError, match="not empty"):
        materialise(storage, project, run_id, target, cwd=repo)

    assert (target / "someones_work.py").read_text(encoding="utf-8") == "important = True\n"
    assert list(target.iterdir()) == [target / "someones_work.py"]


def test_materialise_refuses_a_target_inside_the_repository(
    storage: Storage, repo: Path
) -> None:
    project, run_id = _record(storage, repo)

    with pytest.raises(ReplayError, match="inside the repository"):
        materialise(storage, project, run_id, repo / "nested", cwd=repo)

    assert not (repo / "nested").exists()


def test_materialise_refuses_a_target_that_is_a_file(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    project, run_id = _record(storage, repo)
    target = tmp_path / "a-file"
    target.write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(ReplayError, match="not a directory"):
        materialise(storage, project, run_id, target, cwd=repo)


def test_materialise_refuses_a_run_with_no_manifest(storage: Storage, tmp_path: Path) -> None:
    storage.create_run("proj", "proj_run_0013", {"created_at": "2026-08-13T10:00:00+05:30"})

    with pytest.raises(ReplayError, match="no provenance manifest"):
        materialise(storage, "proj", "proj_run_0013", tmp_path / "target")


def test_materialise_says_when_the_commit_is_unreachable(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    project, run_id = _record(storage, repo)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    _configure(other)
    _write(other / "unrelated.py", "x = 1\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "unrelated")

    with pytest.raises(ReplayError, match="may exist only on"):
        materialise(storage, project, run_id, tmp_path / "target", cwd=other)

    assert not (tmp_path / "target").exists()


def test_materialise_can_skip_the_patch_and_says_that_it_did(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    _dirty(repo)
    project, run_id = _record(storage, repo)
    target = tmp_path / "committed-only"

    result = materialise(storage, project, run_id, target, apply_patch=False, cwd=repo)

    assert (target / "train.py").read_text(encoding="utf-8") == "def main():\n    return 0\n"
    assert not (target / "staged.py").exists()
    assert any("apply_patch=False" in warning for warning in result.warnings)


def test_a_patch_that_no_longer_applies_warns_and_keeps_the_worktree(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    """Failing loudly beats failing destructively: the checkout is still the best artefact
    available, so it survives, and the warning leads with the failure."""
    _dirty(repo)
    project, run_id = _record(storage, repo)
    run_dir = storage.run_path(project, run_id)
    corrupted = (
        b"diff --git a/absent.py b/absent.py\n"
        b"--- a/absent.py\n+++ b/absent.py\n"
        b"@@ -1 +1 @@\n-was here\n+is here\n"
    )
    manifest = storage.read_provenance(project, run_id)
    manifest["git"]["diff_sha256"] = None
    storage.write_provenance(project, run_id, manifest, corrupted)
    assert (run_dir / contract.PATCH_FILE).read_bytes() == corrupted
    target = tmp_path / "replayed"

    result = materialise(storage, project, run_id, target, cwd=repo)

    assert (target / "train.py").exists(), "the worktree must survive a failed patch"
    assert any(w.startswith("THE RECORDED PATCH DID NOT APPLY") for w in result.warnings)


def test_a_verified_replay_of_a_materialised_worktree_is_reproducible(
    storage: Storage, repo: Path, tmp_path: Path
) -> None:
    """End to end, and the milestone gate in one assertion: capture here, reconstruct
    there, and the reconstruction verifies against the same manifest."""
    _dirty(repo)
    project, run_id = _record(storage, repo)
    target = tmp_path / "replayed"

    materialise(storage, project, run_id, target, cwd=repo)
    report = verify(storage, project, run_id, cwd=target)

    assert report.by_name("git.head").status is CheckStatus.OK
    assert report.by_name("git.commit").status is CheckStatus.OK
    assert report.by_name("git.patch").status is CheckStatus.OK, report.by_name(
        "git.patch"
    ).detail
    # Dirty, as the manifest recorded — the untracked requirements file replay wrote is
    # enough to make the worktree dirty on its own, which is exactly why this check is not
    # allowed to stand in for a content comparison. The patch check above is that.
    assert report.by_name("git.worktree").status is CheckStatus.OK
