"""Provenance through the SDK: captured by ``init()``, read back off disk via Storage.

test_provenance.py covers the capture layer alone. Each test here chdirs into a real
throwaway git repo, since ``init()`` captures the cwd.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest

import mlexperimenttracker as met
from mlexperimenttracker import provenance
from mlexperimenttracker import run as run_module
from mlexperimenttracker.contract import PATCH_FILE, PROVENANCE_FILE, RunState
from mlexperimenttracker.storage import Storage

PROJECT = "prov"


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


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


@pytest.fixture(autouse=True)
def _no_leaked_runs():
    """Finish leftover runs so atexit doesn't write into a deleted tmp dir."""
    yield
    for run in list(run_module._ACTIVE.values()):
        try:
            run.finish(RunState.COMPLETED)
        except Exception:
            run_module._ACTIVE.pop(id(run), None)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with one commit on ``main``.

    autocrlf is off because Git for Windows enables it system-wide, and CRLF checkouts
    make correct patches fail to apply.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Test Runner")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    _write(repo / "train.py", "def main():\n    return 0\n")
    _write(repo / ".gitignore", "ignored/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    return repo


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """Storage root outside the repo, so runs don't show up in their own diff."""
    return tmp_path / "store"


def start(store: Path, **kwargs):
    """Start a run without signal capture (it would replace pytest's SIGINT handler)."""
    kwargs.setdefault("capture_signals", False)
    kwargs.setdefault("project", PROJECT)
    return met.init(storage_path=store, **kwargs)


def manifest_of(store: Path, run_id: str) -> dict | None:
    return Storage(store).read_provenance(PROJECT, run_id)


# --------------------------------------------------------------------------------------
# The manifest a run carries
# --------------------------------------------------------------------------------------


def test_a_run_in_a_repository_writes_a_manifest_that_reads_back(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    head = _git(repo, "rev-parse", "HEAD").strip()

    with start(store) as run:
        run.log({"loss": 1.0}, step=1)

    written = manifest_of(store, run.id)
    assert written is not None, "a run in a git repository must carry a manifest"

    git = written["git"]
    assert git["available"] is True
    assert git["commit"] == head
    assert git["branch"] == "main"
    assert git["dirty"] is False
    assert git["diff_file"] is None

    # Non-git blocks must always be present; presence is how "not recorded" differs from
    # "recorded as nothing".
    assert written["python"]["version"]
    assert written["platform"]["system"]
    assert isinstance(written["packages"], dict)
    assert written["hardware"]["cpu_count"] is None or written["hardware"]["cpu_count"] > 0
    assert isinstance(written["hardware"]["gpus"], list)
    assert written["captured_at"]
    assert written["datasets"] == []

    # run.provenance must match the file.
    assert run.provenance == written


def test_the_manifest_does_not_disturb_what_a_1_0_reader_sees(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 1.1 bump is additive: the served run object is the same with or without a manifest."""
    monkeypatch.chdir(repo)
    with start(store, run_id="with_manifest") as with_manifest:
        with_manifest.log({"loss": 0.5}, step=1)
    with start(store, run_id="without_manifest", provenance=False) as without:
        without.log({"loss": 0.5}, step=1)

    storage = Storage(store)
    first = storage.read_run(PROJECT, "with_manifest")
    second = storage.read_run(PROJECT, "without_manifest")
    assert first is not None and second is not None

    volatile = {"_id", "name", "createdAt", "startTime", "endTime", "duration",
                "durationFormatted", "metricsHistory", "summary", "checkpoints"}
    assert {k: v for k, v in first.items() if k not in volatile} == {
        k: v for k, v in second.items() if k not in volatile
    }
    # Artifact scan only looks in artifacts/, so manifest and patch never show up there.
    assert first["artifacts"] == [] == second["artifacts"]


def test_the_captured_patch_round_trips_through_storage(
    repo: Path, store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch on disk matches its recorded hash and still applies to a clone."""
    _write(repo / "train.py", "def main():\n    # uncommitted\n    return 42\n")
    _write(repo / "staged.py", "STAGED = True\n")
    _git(repo, "add", "staged.py")
    monkeypatch.chdir(repo)

    with start(store) as run:
        pass

    written = manifest_of(store, run.id)
    assert written is not None
    git = written["git"]
    assert git["dirty"] is True
    assert git["diff_file"] == PATCH_FILE == "uncommitted.patch"
    assert git["diff_truncated"] is False

    patch = Storage(store).read_patch(PROJECT, run.id)
    assert patch is not None
    # Byte-exact: a text-mode write on Windows adds CRs and the patch stops applying.
    assert len(patch) == git["diff_bytes"]
    assert hashlib.sha256(patch).hexdigest() == git["diff_sha256"]
    assert b"staged.py" in patch and b"uncommitted" in patch

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "clone", str(repo), str(clone)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    applied = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "--check", "--whitespace=nowarn", "-"],
        cwd=str(clone),
        input=patch,
        capture_output=True,
        timeout=60,
    )
    assert applied.returncode == 0, applied.stderr.decode("utf-8", "replace")


def test_a_run_outside_a_repository_still_succeeds(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Common for notebooks and scratch dirs."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)

    with start(store) as run:
        run.log({"loss": 0.25}, step=1)

    written = manifest_of(store, run.id)
    assert written is not None
    git = written["git"]
    assert git["available"] is False
    assert git["reason"], "an unavailable git block must say why, or drift cannot be told from a gap"
    assert git["commit"] is None
    assert git["diff_file"] is None
    assert not (run.path / PATCH_FILE).exists()

    # Non-git fields are still captured.
    assert written["python"]["version"]
    assert written["command"]["argv"]

    summary = Storage(store).read_run(PROJECT, run.id)
    assert summary is not None and summary["status"] == "completed"


def test_provenance_false_writes_no_file(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    with start(store, provenance=False) as run:
        run.log({"loss": 1.0}, step=1)

    assert not (run.path / PROVENANCE_FILE).exists()
    assert not (run.path / PATCH_FILE).exists()
    assert manifest_of(store, run.id) is None
    assert run.provenance is None


def test_capture_diff_false_writes_a_manifest_but_no_patch(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tree is still reported dirty; only the diff is skipped."""
    _write(repo / "train.py", "def main():\n    return 99\n")
    monkeypatch.chdir(repo)

    with start(store, capture_diff=False) as run:
        pass

    written = manifest_of(store, run.id)
    assert written is not None
    git = written["git"]
    assert git["available"] is True
    assert git["dirty"] is True, "dirtiness is a fact about the tree, not about the flag"
    assert git["diff_file"] is None
    assert git["diff_sha256"] is None
    assert git["diff_bytes"] == 0
    assert "disabled" in (git["reason"] or "")
    assert not (run.path / PATCH_FILE).exists()


# --------------------------------------------------------------------------------------
# Capture must never break the run
# --------------------------------------------------------------------------------------


def test_a_capture_that_raises_leaves_a_usable_run(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture failure must not break the run. capture() has no raise path, so patch one in."""
    monkeypatch.chdir(repo)

    def explode(*args: object, **kwargs: object):
        raise RuntimeError("nvml segfaulted its way into python")

    monkeypatch.setattr(provenance, "capture", explode)

    with start(store) as run:
        run.log({"loss": 0.5}, step=1)
        run.log({"loss": 0.25}, step=2)

    assert run.provenance is None
    assert not (run.path / PROVENANCE_FILE).exists()

    read_back = Storage(store).read_run(PROJECT, run.id)
    assert read_back is not None
    assert read_back["status"] == "completed"
    assert read_back["metrics"]["loss"] == 0.25
    assert len(read_back["metricsHistory"]) == 2


def test_an_unwritable_manifest_leaves_a_usable_run(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture works but the manifest write fails; the run must still complete."""
    monkeypatch.chdir(repo)
    monkeypatch.setattr(Storage, "write_provenance", lambda *args, **kwargs: False)

    with start(store) as run:
        run.log({"loss": 1.0}, step=1)

    assert run.provenance is None
    read_back = Storage(store).read_run(PROJECT, run.id)
    assert read_back is not None and read_back["status"] == "completed"


# --------------------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------------------


def test_datasets_given_to_init_are_hashed_into_the_manifest(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = repo / "data" / "train.csv"
    _write(data, "a,b\n1,2\n")
    monkeypatch.chdir(repo)

    with start(store, datasets=["data/train.csv"]) as run:
        pass

    written = manifest_of(store, run.id)
    assert written is not None
    (entry,) = written["datasets"]
    # Path kept as given (not resolved) so it compares across runs from the same dir.
    assert entry["path"] == "data/train.csv"
    assert entry["algorithm"] == "sha256"
    assert entry["sha256"] == entry["digest"] == provenance.hash_path(data)["digest"]
    assert entry["bytes"] == data.stat().st_size
    assert entry["files"] == 1


def test_log_dataset_after_init_updates_the_manifest(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Datasets logged mid-run are written to the manifest and survive a re-read."""
    monkeypatch.chdir(repo)
    tree = repo / "data" / "shards"
    _write(tree / "a.bin", "aaaa")
    _write(tree / "b.bin", "bbbb")
    single = repo / "data" / "val.csv"
    _write(single, "x\n1\n")

    with start(store) as run:
        assert manifest_of(store, run.id)["datasets"] == []

        first = run.log_dataset("data/shards", name="training")
        assert first["files"] == 2
        assert first["name"] == "training"

        second = run.log_dataset(single)
        assert second["files"] == 1

        written = manifest_of(store, run.id)
        assert [entry.get("name") or entry["path"] for entry in written["datasets"]] == [
            "training",
            str(single),
        ]
        assert written["datasets"][0]["digest"] == first["digest"]
        assert run.provenance == written

        # Re-logging the same dataset replaces the entry instead of appending.
        _write(tree / "c.bin", "cccc")
        third = run.log_dataset("data/shards", name="training")
        assert third["digest"] != first["digest"]
        rewritten = manifest_of(store, run.id)
        assert len(rewritten["datasets"]) == 2
        assert rewritten["datasets"][0]["digest"] == third["digest"]

        # The rest of the manifest is untouched.
        assert rewritten["git"] == written["git"]
        assert rewritten["captured_at"] == written["captured_at"]


def test_log_dataset_never_raises_over_a_path_that_is_not_there(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad dataset path records an error; the run carries on."""
    monkeypatch.chdir(repo)
    with start(store) as run:
        entry = run.log_dataset("data/does_not_exist.csv")
        assert "error" in entry
        assert "digest" not in entry
        run.log({"loss": 1.0}, step=1)

    written = manifest_of(store, run.id)
    assert "error" in written["datasets"][0]
    assert Storage(store).read_run(PROJECT, run.id)["status"] == "completed"


def test_log_dataset_without_a_manifest_hashes_but_writes_nothing(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With provenance=False, log_dataset still hashes but doesn't create a manifest.

    One written here would have an empty git block that looks like "not a repository".
    """
    data = repo / "data.csv"
    _write(data, "x\n1\n")
    monkeypatch.chdir(repo)

    with start(store, provenance=False) as run:
        entry = run.log_dataset(data)

    assert entry["digest"] == provenance.hash_path(data)["digest"]
    assert not (run.path / PROVENANCE_FILE).exists()


def test_log_dataset_is_refused_after_the_run_has_finished(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    run = start(store)
    run.finish()
    with pytest.raises(RuntimeError, match="finished"):
        run.log_dataset(repo / "train.py")


# --------------------------------------------------------------------------------------
# What the manifest must never contain
# --------------------------------------------------------------------------------------


def test_the_environment_block_is_an_allowlist_and_nothing_else(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only allowlisted env vars reach the manifest; secrets must never be captured."""
    monkeypatch.chdir(repo)
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sk-live-do-not-capture")
    monkeypatch.setenv("HF_TOKEN", "hf_do_not_capture")
    monkeypatch.setenv("MY_HARMLESS_VAR", "harmless")

    with start(store) as run:
        pass

    environment = manifest_of(store, run.id)["environment"]
    assert environment["OMP_NUM_THREADS"] == "3"
    assert set(environment) <= set(provenance.ENV_ALLOWLIST)
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "HF_TOKEN" not in environment
    assert "MY_HARMLESS_VAR" not in environment

    # Also check values, whatever key they came under.
    assert not any("do-not-capture" in value or "do_not_capture" in value
                   for value in environment.values())


def test_an_allowlisted_name_that_looks_like_a_credential_is_redacted(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards future allowlist additions; nothing on it looks like a credential today."""
    monkeypatch.chdir(repo)
    monkeypatch.setattr(provenance, "ENV_ALLOWLIST", ("OMP_NUM_THREADS", "WANDB_API_KEY"))
    monkeypatch.setenv("OMP_NUM_THREADS", "2")
    monkeypatch.setenv("WANDB_API_KEY", "leaked-if-you-are-reading-this")

    with start(store) as run:
        pass

    environment = manifest_of(store, run.id)["environment"]
    assert environment["OMP_NUM_THREADS"] == "2"
    assert environment["WANDB_API_KEY"] == provenance.REDACTED


def test_the_command_block_records_the_command_that_ran(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """argv is recorded verbatim, flags included."""
    monkeypatch.chdir(repo)
    with start(store) as run:
        pass

    command = manifest_of(store, run.id)["command"]
    assert command["argv"] and isinstance(command["argv"], list)
    assert Path(command["cwd"]).resolve() == repo.resolve()


def test_an_enormous_untracked_list_is_capped_and_says_so(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing ``.gitignore`` must not write 40,000 paths into every run's manifest."""
    monkeypatch.chdir(repo)
    for index in range(provenance.MAX_UNTRACKED + 25):
        _write(repo / "junk" / f"{index:05d}.tmp", "x")

    with start(store, capture_diff=False) as run:
        pass

    git = manifest_of(store, run.id)["git"]
    assert len(git["untracked"]) == provenance.MAX_UNTRACKED
    assert git["untracked_truncated"] is True
    assert "truncated" in (git["reason"] or "")


# --------------------------------------------------------------------------------------
# What capture costs
# --------------------------------------------------------------------------------------


def test_capture_cost_is_measured_rather_than_assumed(
    repo: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print how long capture and dataset hashing take.

    Bounds are loose on purpose so this doesn't flake on slow CI; the printout is the result.
    """
    monkeypatch.chdir(repo)

    tree = repo / "ignored" / "dataset"  # inside .gitignore, so it stays out of the diff
    payload = os.urandom(128 * 1024)
    for index in range(128):
        path = tree / f"shard_{index:04d}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    total_mb = 128 * 128 * 1024 / (1024 * 1024)

    started = time.perf_counter()
    with start(store, provenance=False) as bare:
        pass
    bare_seconds = time.perf_counter() - started

    started = time.perf_counter()
    with start(store) as captured:
        pass
    capture_seconds = time.perf_counter() - started

    started = time.perf_counter()
    entry = provenance.hash_path(tree)
    hash_seconds = time.perf_counter() - started

    started = time.perf_counter()
    with start(store, datasets=[str(tree)]) as with_data:
        pass
    with_data_seconds = time.perf_counter() - started

    assert entry["files"] == 128
    assert manifest_of(store, with_data.id)["datasets"][0]["digest"] == entry["digest"]
    assert manifest_of(store, bare.id) is None
    assert manifest_of(store, captured.id) is not None

    print(
        "\nprovenance capture cost"
        f"\n  init(provenance=False)          {bare_seconds * 1000:8.1f} ms"
        f"\n  init(provenance=True)           {capture_seconds * 1000:8.1f} ms"
        f"\n  hash_path({total_mb:.0f} MiB, 128 files) {hash_seconds * 1000:8.1f} ms"
        f"  ({total_mb / hash_seconds:.0f} MiB/s)"
        f"\n  init(datasets=[{total_mb:.0f} MiB])       {with_data_seconds * 1000:8.1f} ms"
    )

    # A minute would be a bug on any machine.
    assert capture_seconds < 60.0
    assert with_data_seconds < 120.0
