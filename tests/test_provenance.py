"""Provenance capture tests, run against real throwaway git repos instead of mocks.

Mocks can't catch real git quirks (detached HEAD output, diff drivers, quoted paths). The
patch test clones the repo and applies the captured patch to check it restores the tree.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mlexperimenttracker import contract, provenance
from mlexperimenttracker.provenance import (
    DEFAULT_DIFF_LIMIT,
    ENV_ALLOWLIST,
    MAX_UNTRACKED,
    REDACTED,
    GitState,
    Provenance,
    capture,
    collect_environment,
    collect_hardware,
    collect_packages,
    git_state,
    hash_path,
)
from mlexperimenttracker.storage import Storage

BINARY_BLOB = bytes(range(256)) * 8


def _git(cwd: Path, *args: str) -> str:
    """Run git for test setup; raises on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=60,
    )
    return result.stdout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _configure(repo: Path) -> None:
    # Local config overrides the developer's. autocrlf=true on Windows breaks git apply.
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


# --------------------------------------------------------------------------------------
# Git state
# --------------------------------------------------------------------------------------


def test_a_clean_repository_reports_commit_branch_and_no_patch(repo: Path) -> None:
    state, patch = git_state(repo)

    assert state.available is True
    assert state.reason is None
    assert state.commit is not None and len(state.commit) == 40
    assert set(state.commit) <= set("0123456789abcdef")
    assert state.branch == "main"
    assert state.dirty is False
    assert state.untracked == ()
    assert state.untracked_truncated is False
    assert patch is None
    assert state.diff_file is None
    assert state.diff_sha256 is None
    assert state.diff_bytes == 0
    assert state.diff_truncated is False


def test_the_captured_patch_applies_to_a_clean_clone(repo: Path, tmp_path: Path) -> None:
    """Applying the patch to a clean clone reproduces the dirty tree."""
    dirty_text = "def main():\n    # uncommitted work\n    return 42\n"
    _write(repo / "train.py", dirty_text)
    dirty_blob = BINARY_BLOB[::-1]
    (repo / "model.bin").write_bytes(dirty_blob)
    # Staged changes too; plain `git diff` would miss them.
    _write(repo / "staged.py", "STAGED = True\n")
    _git(repo, "add", "staged.py")

    state, patch = git_state(repo)

    assert state.dirty is True
    assert patch is not None
    assert state.diff_file == contract.PATCH_FILE == "uncommitted.patch"
    assert state.diff_bytes == len(patch)
    assert state.diff_sha256 == hashlib.sha256(patch).hexdigest()
    assert state.diff_truncated is False

    clone = tmp_path / "clone"
    # autocrlf must be off during the clone itself: Git for Windows enables it system-wide,
    # the checkout would write CRLF, and the LF patch would not apply.
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "clone", str(repo), str(clone)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    _configure(clone)
    patch_file = tmp_path / "captured.patch"
    patch_file.write_bytes(patch)
    _git(clone, "apply", str(patch_file))

    assert (clone / "train.py").read_text(encoding="utf-8") == dirty_text
    assert (clone / "model.bin").read_bytes() == dirty_blob
    assert (clone / "staged.py").read_text(encoding="utf-8") == "STAGED = True\n"


def test_untracked_files_are_listed_and_ignored_ones_are_not(repo: Path) -> None:
    _write(repo / "notes.txt", "scratch\n")
    _write(repo / "nested" / "extra.py", "x = 1\n")
    _write(repo / "debug.log", "noise\n")
    _write(repo / "ignored" / "weights.pt", "big\n")

    state, patch = git_state(repo)

    assert state.available is True
    assert set(state.untracked) == {"notes.txt", "nested/extra.py"}
    assert state.untracked_truncated is False
    # Untracked files make the tree dirty even with no patch.
    assert state.dirty is True
    assert patch is None
    assert state.diff_file is None


def test_the_untracked_list_is_capped_and_says_so(repo: Path) -> None:
    for index in range(MAX_UNTRACKED + 25):
        _write(repo / f"scratch_{index:04d}.txt", "x\n")

    state, _ = git_state(repo, capture_diff=False)

    assert len(state.untracked) == MAX_UNTRACKED
    assert state.untracked_truncated is True
    assert state.reason is not None and "truncated" in state.reason
    assert str(MAX_UNTRACKED + 25) in state.reason


def test_a_detached_head_has_a_commit_and_no_branch(repo: Path) -> None:
    _write(repo / "train.py", "def main():\n    return 1\n")
    _git(repo, "commit", "-am", "second")
    first = _git(repo, "rev-parse", "HEAD~1").strip()
    _git(repo, "checkout", "--detach", first)

    state, _ = git_state(repo)

    assert state.available is True
    assert state.commit == first
    # None, not "HEAD", or unrelated detached runs would look like the same branch.
    assert state.branch is None


def test_the_origin_remote_is_recorded(repo: Path) -> None:
    _git(repo, "remote", "add", "origin", "https://github.com/example/repo.git")

    state, _ = git_state(repo, capture_diff=False)

    assert state.remote == "https://github.com/example/repo.git"


def test_a_directory_that_is_not_a_repository_degrades(tmp_path: Path) -> None:
    plain = tmp_path / "not_a_repo"
    plain.mkdir()

    state, patch = git_state(plain)

    assert state.available is False
    assert state.reason
    assert state.commit is None
    assert patch is None


def test_git_missing_from_path_degrades(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")

    state, patch = git_state(repo)

    assert state.available is False
    assert state.reason is not None and "git" in state.reason.lower()
    assert patch is None


def test_a_repository_with_no_commits_still_reports_available(tmp_path: Path) -> None:
    empty = tmp_path / "unborn"
    empty.mkdir()
    _git(empty, "init", "-b", "main")
    _configure(empty)

    state, _ = git_state(empty)

    assert state.available is True
    assert state.commit is None
    assert state.reason is not None and "commit" in state.reason


# --------------------------------------------------------------------------------------
# The diff cap
# --------------------------------------------------------------------------------------


def test_the_diff_cap_is_recorded_rather_than_applied_silently(repo: Path) -> None:
    _write(repo / "train.py", "".join(f"line {index}\n" for index in range(20000)))

    state, patch = git_state(repo, diff_limit=1024)

    assert patch is not None
    assert len(patch) == 1024
    assert state.diff_bytes == 1024
    assert state.diff_truncated is True
    assert state.diff_sha256 == hashlib.sha256(patch).hexdigest()
    # The reason must mention the truncation so a failed replay is explainable.
    assert state.reason is not None and "truncated" in state.reason
    assert "1024" in state.reason


def test_diff_capture_can_be_turned_off(repo: Path) -> None:
    _write(repo / "train.py", "def main():\n    return 99\n")

    state, patch = git_state(repo, capture_diff=False)

    assert state.dirty is True
    assert patch is None
    assert state.diff_file is None
    assert state.diff_bytes == 0
    assert state.reason is not None and "disabled" in state.reason


def test_the_default_diff_limit_is_a_mebibyte() -> None:
    assert DEFAULT_DIFF_LIMIT == 1024 * 1024


# --------------------------------------------------------------------------------------
# Environment: the allowlist and the redaction
# --------------------------------------------------------------------------------------


def test_the_environment_allowlist_excludes_a_planted_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-a-real-secret-9f3a")
    monkeypatch.setenv("HUGGINGFACE_HUB_TOKEN", "hf_notarealtoken")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host/db")

    captured = collect_environment()

    assert captured["CUDA_VISIBLE_DEVICES"] == "0,1"
    assert set(captured) <= set(ENV_ALLOWLIST)
    assert "AWS_SECRET_ACCESS_KEY" not in captured
    assert "HUGGINGFACE_HUB_TOKEN" not in captured
    assert "DATABASE_URL" not in captured
    # The value must not appear anywhere either.
    assert "not-a-real-secret-9f3a" not in json.dumps(captured)
    assert "hf_notarealtoken" not in json.dumps(captured)


@pytest.mark.parametrize(
    "name", ["HF_TOKEN", "TRAINING_API_KEY", "S3_SECRET", "DB_PASSWORD", "GCP_CREDENTIAL_FILE"]
)
def test_redaction_fires_for_a_credential_shaped_allowlisted_name(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backstop in case a credential-like name is ever added to the allowlist."""
    monkeypatch.setattr(provenance, "ENV_ALLOWLIST", (*ENV_ALLOWLIST, name))
    monkeypatch.setenv(name, "value-that-must-not-be-recorded")

    captured = collect_environment()

    assert captured[name] == REDACTED
    assert "value-that-must-not-be-recorded" not in json.dumps(captured)


def test_an_unset_allowlisted_variable_is_omitted_not_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)

    assert "SLURM_JOB_ID" not in collect_environment()


# --------------------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------------------


def test_a_file_hash_is_the_plain_content_hash_and_is_stable(tmp_path: Path) -> None:
    target = tmp_path / "train.csv"
    payload = b"feature,label\n1.0,0\n2.0,1\n"
    target.write_bytes(payload)

    first = hash_path(target)
    second = hash_path(target, chunk=3)

    assert first["sha256"] == hashlib.sha256(payload).hexdigest()
    assert first["sha256"] == second["sha256"]
    assert first["digest"] == first["sha256"]
    assert first["bytes"] == len(payload)
    assert first["files"] == 1
    assert first["algorithm"] == "sha256"
    assert first["hashed_at"]
    assert "error" not in first


def test_a_directory_hash_does_not_depend_on_creation_order(tmp_path: Path) -> None:
    names = ["a.csv", "b.csv", "nested/c.csv", "nested/deep/d.csv"]
    forward = tmp_path / "forward"
    backward = tmp_path / "backward"
    for name in names:
        _write(forward / name, f"contents of {name}\n")
    for name in reversed(names):
        _write(backward / name, f"contents of {name}\n")

    first = hash_path(forward)
    second = hash_path(backward)

    assert first["sha256"] == second["sha256"]
    assert first["files"] == second["files"] == len(names)
    assert first["bytes"] == second["bytes"]
    assert hash_path(forward)["sha256"] == first["sha256"]


def test_renaming_a_file_changes_the_directory_hash(tmp_path: Path) -> None:
    """File names are part of the digest, so a swapped train/val split is detected."""
    root = tmp_path / "data"
    _write(root / "train.csv", "same bytes\n")
    _write(root / "val.csv", "other bytes\n")
    before = hash_path(root)["sha256"]

    (root / "train.csv").rename(root / "renamed.csv")
    after = hash_path(root)["sha256"]

    assert before != after


def test_a_changed_byte_changes_the_directory_hash(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _write(root / "train.csv", "1,2,3\n")
    before = hash_path(root)["sha256"]
    _write(root / "train.csv", "1,2,4\n")

    assert hash_path(root)["sha256"] != before


def test_a_missing_path_records_an_error_instead_of_raising(tmp_path: Path) -> None:
    entry = hash_path(tmp_path / "absent.csv")

    assert "error" in entry
    assert "sha256" not in entry
    assert entry["path"].endswith("absent.csv")
    assert entry["algorithm"] == "sha256"


def test_an_unknown_algorithm_records_an_error(tmp_path: Path) -> None:
    target = tmp_path / "x.bin"
    target.write_bytes(b"payload")

    entry = hash_path(target, algorithm="not-a-hash")

    assert "error" in entry
    assert "sha256" not in entry


def test_a_non_sha256_algorithm_is_not_filed_under_sha256(tmp_path: Path) -> None:
    target = tmp_path / "x.bin"
    target.write_bytes(b"payload")

    entry = hash_path(target, algorithm="sha1")

    assert entry["algorithm"] == "sha1"
    assert entry["digest"] == hashlib.sha1(b"payload").hexdigest()
    assert "sha256" not in entry


# --------------------------------------------------------------------------------------
# The manifest as a whole
# --------------------------------------------------------------------------------------


def test_capture_produces_every_block_of_the_manifest(repo: Path) -> None:
    manifest = capture(repo)[0].to_dict()

    assert set(manifest) == {
        "captured_at",
        "git",
        "python",
        "platform",
        "packages",
        "hardware",
        "environment",
        "datasets",
        "command",
    }
    assert manifest["captured_at"]
    assert manifest["git"]["available"] is True
    assert manifest["python"]["version"] == ".".join(str(p) for p in sys.version_info[:3])
    assert manifest["python"]["executable"] == sys.executable
    assert manifest["platform"]["system"]
    assert manifest["hardware"]["cpu_count"]
    assert isinstance(manifest["hardware"]["gpus"], list)
    assert manifest["command"]["argv"]
    assert manifest["command"]["cwd"]
    assert manifest["datasets"] == []
    # Storage's encoder rejects NaN and non-JSON types.
    assert json.loads(json.dumps(manifest, allow_nan=False)) == manifest


def test_capture_records_datasets_and_an_explicit_command(repo: Path, tmp_path: Path) -> None:
    dataset = tmp_path / "train.csv"
    dataset.write_bytes(b"a,b\n1,2\n")
    entry = hash_path(dataset)

    manifest = capture(
        repo,
        datasets=[entry],
        command={"argv": ["train.py", "--lr", "3e-4"], "cwd": str(repo)},
    )[0].to_dict()

    assert manifest["datasets"] == [entry]
    assert manifest["command"] == {"argv": ["train.py", "--lr", "3e-4"], "cwd": str(repo)}


def test_capture_never_raises_for_a_nonexistent_working_directory(tmp_path: Path) -> None:
    manifest, patch = capture(tmp_path / "does" / "not" / "exist")

    assert patch is None
    assert manifest.git.available is False
    assert manifest.git.reason
    # Non-git fields still get captured.
    assert manifest.python["version"]
    assert manifest.packages


def test_capture_survives_git_disappearing(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")

    manifest, patch = capture(repo)

    assert patch is None
    assert manifest.git.available is False
    assert manifest.platform["system"]


def test_the_manifest_round_trips_through_a_dict(repo: Path) -> None:
    _write(repo / "train.py", "def main():\n    return 7\n")
    original, _ = capture(repo)

    restored = Provenance.from_dict(json.loads(json.dumps(original.to_dict())))

    assert restored.to_dict() == original.to_dict()
    assert restored.git.commit == original.git.commit
    assert restored.git.diff_sha256 == original.git.diff_sha256
    assert restored.datasets == original.datasets


def test_from_dict_tolerates_a_manifest_that_is_wrong(tmp_path: Path) -> None:
    """Users can edit the file, so bad fields fall back to defaults instead of raising."""
    restored = Provenance.from_dict({"git": "not an object", "packages": 7, "datasets": None})

    assert restored.git == GitState()
    assert restored.packages == {}
    assert restored.datasets == ()
    assert restored.captured_at == ""


def test_packages_are_sorted_by_lowercased_name() -> None:
    packages = collect_packages()

    assert packages, "the test environment has distributions installed"
    assert "pytest" in packages
    names = list(packages)
    assert names == sorted(names, key=str.lower)
    assert all(isinstance(version, str) for version in packages.values())


def test_hardware_always_reports_a_cpu_count() -> None:
    hardware = collect_hardware()

    assert hardware["cpu_count"] is None or hardware["cpu_count"] >= 1
    assert isinstance(hardware["gpus"], list)
    for gpu in hardware["gpus"]:
        assert set(gpu) == {"name", "memory_total_mb", "driver_version", "cuda_version"}


# --------------------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------------------


def test_the_manifest_and_patch_round_trip_through_storage(repo: Path, tmp_path: Path) -> None:
    _write(repo / "train.py", "def main():\n    return 5\n")
    manifest, patch = capture(repo)
    storage = Storage(tmp_path / "root")
    storage.create_run("proj", "proj_run_0001", {"created_at": "2026-08-13T10:00:00+05:30"})

    assert storage.write_provenance("proj", "proj_run_0001", manifest.to_dict(), patch) is True

    read = storage.read_provenance("proj", "proj_run_0001")
    assert read == manifest.to_dict()
    assert storage.read_patch("proj", "proj_run_0001") == patch
    run_dir = tmp_path / "root" / "proj" / "proj_run_0001"
    assert (run_dir / contract.PROVENANCE_FILE).exists()
    assert (run_dir / contract.PATCH_FILE).read_bytes() == patch


def test_a_patch_is_written_byte_for_byte(tmp_path: Path) -> None:
    """Written as bytes; text mode would mangle binary patches and CRLF on Windows."""
    storage = Storage(tmp_path / "root")
    storage.create_run("proj", "proj_run_0002", {"created_at": "2026-08-13T10:00:00+05:30"})
    payload = b"diff --git a/x b/x\r\nGIT binary patch\n\x00\x01\x02\xff\n"

    assert storage.write_provenance("proj", "proj_run_0002", {"git": {}}, payload) is True
    assert storage.read_patch("proj", "proj_run_0002") == payload


def test_provenance_writes_without_a_patch(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "root")
    storage.create_run("proj", "proj_run_0003", {"created_at": "2026-08-13T10:00:00+05:30"})

    assert storage.write_provenance("proj", "proj_run_0003", {"git": {"dirty": False}}, None)
    assert storage.read_provenance("proj", "proj_run_0003") == {"git": {"dirty": False}}
    assert storage.read_patch("proj", "proj_run_0003") is None
    assert not (tmp_path / "root" / "proj" / "proj_run_0003" / contract.PATCH_FILE).exists()


def test_an_absent_manifest_reads_as_none(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "root")
    storage.create_run("proj", "proj_run_0004", {"created_at": "2026-08-13T10:00:00+05:30"})

    assert storage.read_provenance("proj", "proj_run_0004") is None
    assert storage.read_patch("proj", "proj_run_0004") is None


def test_a_malformed_manifest_reads_as_none(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "root")
    storage.create_run("proj", "proj_run_0005", {"created_at": "2026-08-13T10:00:00+05:30"})
    path = tmp_path / "root" / "proj" / "proj_run_0005" / contract.PROVENANCE_FILE
    path.write_text("{not json", encoding="utf-8")

    assert storage.read_provenance("proj", "proj_run_0005") is None


@pytest.mark.parametrize(
    ("project", "run_id"),
    [
        ("..", "run"),
        ("proj", ".."),
        ("proj/../..", "run"),
        ("proj", "sub/run"),
        ("proj", "C:\\windows"),
        ("proj", ""),
    ],
)
def test_an_unaddressable_name_is_refused_by_every_provenance_method(
    project: str, run_id: str, tmp_path: Path
) -> None:
    storage = Storage(tmp_path / "root")

    assert storage.write_provenance(project, run_id, {"git": {}}, b"patch") is False
    assert storage.read_provenance(project, run_id) is None
    assert storage.read_patch(project, run_id) is None


# --------------------------------------------------------------------------------------
# The format version
# --------------------------------------------------------------------------------------


def test_the_format_version_is_bumped_for_the_manifest() -> None:
    # A floor so later minor bumps pass. Ints because "1.10" < "1.2" as strings.
    major, minor = (int(part) for part in contract.FORMAT_VERSION.split(".")[:2])
    assert (major, minor) >= (1, 1)
    assert contract.PROVENANCE_FILE == "provenance.json"
    assert contract.PATCH_FILE == "uncommitted.patch"


def test_a_1_1_directory_still_reads_as_a_run(repo: Path, tmp_path: Path) -> None:
    """The two provenance files don't change what the 1.0 read path returns."""
    storage = Storage(tmp_path / "root")
    storage.create_run(
        "proj",
        "proj_run_0006",
        {"created_at": "2026-08-13T10:00:00+05:30", "format_version": "1.1", "tags": []},
    )
    before = storage.read_run("proj", "proj_run_0006")

    manifest, patch = capture(repo)
    storage.write_provenance("proj", "proj_run_0006", manifest.to_dict(), patch)

    assert storage.read_run("proj", "proj_run_0006") == before
    assert storage.read_artifacts("proj", "proj_run_0006") == []
