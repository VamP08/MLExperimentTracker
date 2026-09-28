"""Tests for the demo generator.

The tree must be renderable (all fields, all states) and deterministic, since the fixture
in tests/fixtures/ is generated from it. The UI computes no aggregates, so
test_metrics_summary_matches_the_series checks summary.json against metrics.jsonl.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

import pytest

from mlexperimenttracker import demo
from mlexperimenttracker.contract import RESERVED_METRIC_KEYS, STAT_KEYS, RunState
from mlexperimenttracker.storage import Storage

FIXTURE_TREE = Path(__file__).resolve().parent / "fixtures" / "experiment_tracker"


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Storage:
    """One generated archive, shared by every read-only test in the module."""
    root = tmp_path_factory.mktemp("archive")
    demo.generate(root, runs=8, seed=0)
    return Storage(root)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run_dirs(storage: Storage) -> list[Path]:
    return [storage.run_path(project, run_id) for project, run_id in storage.list_all_runs()]


def state_of(run_dir: Path) -> str:
    """The state the reader sees: ``summary.json`` if present, else ``metadata.json``.

    ``metadata.state`` is always ``running``; the terminal state lives in the summary.
    """
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        return str(json.loads(summary_path.read_text(encoding="utf-8"))["state"])
    return str(json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))["state"])


# --------------------------------------------------------------------------------------
# Shape of the archive
# --------------------------------------------------------------------------------------


def test_generate_returns_the_ids_it_created(archive: Storage) -> None:
    run_ids = {run_id for _, run_id in archive.list_all_runs()}
    assert len(run_ids) == 8
    assert set(demo.generate(archive.root, runs=8, seed=0)) == run_ids


def test_two_projects_with_different_characters(archive: Storage) -> None:
    assert archive.list_projects() == ["agnews-distilbert", "cifar10-cnn"]
    models = {
        json.loads((run_dir / "config.json").read_text(encoding="utf-8"))["model"]
        for run_dir in run_dirs(archive)
    }
    assert models == {"resnet18", "distilbert-base-uncased"}


def test_run_ids_embed_the_project_and_are_addressable(archive: Storage) -> None:
    for project, run_id in archive.list_all_runs():
        # The reader doesn't enforce unique ids across projects; the prefix does.
        assert run_id.startswith(f"{project}_")
        assert re.fullmatch(r"[A-Za-z0-9._-]+", run_id)
        assert archive.resolve_within(project, run_id) is not None


def test_every_state_in_the_vocabulary_appears(archive: Storage) -> None:
    states = [state_of(run_dir) for run_dir in run_dirs(archive)]
    assert states.count(RunState.COMPLETED.value) >= 3
    assert RunState.FAILED.value in states
    assert RunState.INTERRUPTED.value in states
    assert RunState.RUNNING.value in states


def test_metadata_state_is_what_the_sdk_leaves_behind(archive: Storage) -> None:
    """The SDK leaves ``running`` in metadata on every run; demo data must match."""
    for run_dir in run_dirs(archive):
        metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["state"] == "running"
        assert isinstance(metadata["tags"], list)  # a bare string here 500s the dashboard
        assert metadata["format_version"]


def test_the_running_run_has_no_summary_at_all(archive: Storage) -> None:
    """A live run has no summary, so status comes from the metadata fallback."""
    running = [d for d in run_dirs(archive) if not (d / "summary.json").exists()]
    assert len(running) == 1
    run_dir = running[0]

    project, run_id = run_dir.parent.name, run_dir.name
    run = archive.read_run(project, run_id)
    assert run is not None
    assert run["status"] == "running"
    # No summary means no metrics and zero duration.
    assert run["metrics"] == {}
    assert run["durationFormatted"] == "0s"


def test_the_failed_run_stops_mid_series(archive: Storage) -> None:
    failed = [d for d in run_dirs(archive) if state_of(d) == RunState.FAILED.value]
    assert len(failed) == 1
    run_dir = failed[0]
    rows = read_jsonl(run_dir / "metrics.jsonl")
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert len(rows) < config["epochs"] * 4
    # The file just ends, well short of the planned last step.
    assert rows[-1]["step"] < config["epochs"] * (45000 // config["batch_size"])


def test_interrupted_reads_as_archived(archive: Storage) -> None:
    interrupted = [d for d in run_dirs(archive) if state_of(d) == RunState.INTERRUPTED.value]
    assert len(interrupted) == 1
    run = archive.read_run(interrupted[0].parent.name, interrupted[0].name)
    assert run is not None
    assert run["status"] == "archived"


# --------------------------------------------------------------------------------------
# summary.json vs metrics.jsonl
# --------------------------------------------------------------------------------------


def test_metrics_summary_matches_the_series(archive: Storage) -> None:
    """Recompute every aggregate from ``metrics.jsonl`` and check it matches the summary.

    Also pins population (not sample) stddev.
    """
    checked = 0
    for run_dir in run_dirs(archive):
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        rows = read_jsonl(run_dir / "metrics.jsonl")

        series: dict[str, list[float]] = {}
        for row in rows:
            for key, value in row.items():
                if key not in RESERVED_METRIC_KEYS:
                    series.setdefault(key, []).append(float(value))

        assert set(summary["metrics_summary"]) == set(series)
        for name, values in series.items():
            stats = summary["metrics_summary"][name]
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(values)
            assert stats["latest"] == pytest.approx(values[-1])
            assert stats["mean"] == pytest.approx(mean, abs=1e-6)
            assert stats["min"] == pytest.approx(min(values))
            assert stats["max"] == pytest.approx(max(values))
            assert stats["stddev"] == pytest.approx(math.sqrt(variance), abs=1e-6)
            checked += 1
    assert checked >= 20


def test_every_metric_carries_latest_and_only_recognised_stats(archive: Storage) -> None:
    for run_dir in run_dirs(archive):
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            continue
        for name, stats in json.loads(summary_path.read_text(encoding="utf-8"))["metrics_summary"].items():
            # Without `latest` the metric is hidden; unknown stat keys are ignored.
            assert isinstance(stats, dict)
            assert "latest" in stats
            assert set(stats) == {"latest", *STAT_KEYS}
            # `loss_mean` would collide with loss's mean after the camelCase transform.
            assert not name.endswith(tuple(f"_{stat}" for stat in STAT_KEYS))


def test_the_table_the_frontend_hardcodes_is_populated(archive: Storage) -> None:
    """``Runs.tsx`` renders fixed cells with no fallback, so these keys must exist."""
    for project in archive.list_projects():
        rows = archive.read_experiment_runs(project)
        assert rows
        for row in rows:
            assert set(row["parameters"]) >= {"learningRate", "batchSize", "epochs"}
            if row["status"] == "running":
                continue  # no summary yet, so no metrics
            assert set(row["metrics"]) >= {"loss", "accuracy", "valLoss", "valAccuracy"}


def test_metrics_are_wide_rows_not_tall_records(archive: Storage) -> None:
    for run_dir in run_dirs(archive):
        rows = read_jsonl(run_dir / "metrics.jsonl")
        assert rows
        for row in rows:
            assert {"step", "timestamp", "absolute_timestamp", "loss", "accuracy"} <= set(row)
            # `name`/`value` records would produce two chart series called name and value.
            assert "name" not in row and "value" not in row
        # Validation metrics only appear on epoch boundaries (sparse keys are fine).
        validation = [row for row in rows if "val_loss" in row]
        assert 0 < len(validation) < len(rows)


def test_timestamps_use_both_conventions_correctly(archive: Storage) -> None:
    for project, run_id in archive.list_all_runs():
        run_dir = archive.run_path(project, run_id)
        created = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))["created_at"]
        # Explicit offset required: `new Date` reads a bare timestamp as local time.
        assert re.search(r"([+-]\d{2}:\d{2}|Z)$", created)

        rows = read_jsonl(run_dir / "metrics.jsonl")
        steps = [row["step"] for row in rows]
        relative = [row["timestamp"] for row in rows]
        assert steps == sorted(steps)
        assert relative == sorted(relative)
        assert relative[0] > 0  # relative seconds since the run started, not epoch seconds

        epoch_of_created = _epoch(created)
        for row in rows:
            assert row["absolute_timestamp"] == pytest.approx(
                epoch_of_created + row["timestamp"], abs=0.005
            )


# --------------------------------------------------------------------------------------
# The curves respond to the hyperparameters
# --------------------------------------------------------------------------------------


def test_a_too_high_learning_rate_visibly_diverges(archive: Storage) -> None:
    failed = [d for d in run_dirs(archive) if state_of(d) == RunState.FAILED.value][0]
    losses = [row["loss"] for row in read_jsonl(failed / "metrics.jsonl")]
    config = json.loads((failed / "config.json").read_text(encoding="utf-8"))

    assert config["learning_rate"] > 0.01
    assert min(losses) < losses[0]  # it learns for a while
    assert losses[-1] > 4 * min(losses)  # and then it does not
    assert losses[-1] == max(losses)


def test_a_small_batch_is_noisier_than_a_large_one(archive: Storage) -> None:
    """Noise is measured against a local linear fit, since both curves are falling."""
    noise = {}
    for run_dir in run_dirs(archive):
        if run_dir.parent.name != "cifar10-cnn" or not (run_dir / "summary.json").exists():
            continue
        config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
        losses = [row["loss"] for row in read_jsonl(run_dir / "metrics.jsonl")]
        residuals = [
            abs(losses[i] - (losses[i - 1] + losses[i + 1]) / 2) / losses[i]
            for i in range(1, len(losses) - 1)
        ]
        noise[config["batch_size"]] = sum(residuals) / len(residuals)

    assert min(noise) == 32 and max(noise) == 256
    assert noise[32] > noise[256] * 1.8


def test_validation_curves_open_an_overfitting_gap_late(archive: Storage) -> None:
    completed = [
        d
        for d in run_dirs(archive)
        if (d / "summary.json").exists() and state_of(d) == RunState.COMPLETED.value
    ]
    gaps_that_open = 0
    for run_dir in completed:
        rows = [row for row in read_jsonl(run_dir / "metrics.jsonl") if "val_loss" in row]
        if len(rows) < 4:
            continue
        early = rows[len(rows) // 3]
        late = rows[-1]
        early_gap = early["val_loss"] - early["loss"]
        late_gap = late["val_loss"] - late["loss"]
        if late_gap > early_gap and late["val_loss"] > min(row["val_loss"] for row in rows):
            gaps_that_open += 1
    assert gaps_that_open >= 2


# --------------------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------------------


def test_confusion_matrix_payload_is_internally_consistent(archive: Storage) -> None:
    for project, run_id in archive.list_all_runs():
        matrices = [a for a in archive.read_artifacts(project, run_id) if a["type"] == "confusion_matrix"]
        if not matrices:
            continue
        payload = matrices[0]["metadata"]
        labels, matrix = payload["labels"], payload["matrix"]
        assert len(matrix) == len(labels)
        assert all(len(row) == len(labels) for row in matrix)

        total = sum(sum(row) for row in matrix)
        correct = sum(matrix[i][i] for i in range(len(labels)))
        assert payload["accuracy"] == pytest.approx(correct / total, abs=5e-5)

        # Artifact metadata isn't camelCased on read, so it must be f1Score on disk.
        assert "f1Score" in payload and "f1_score" not in payload
        for index in range(len(labels)):
            column = sum(row[index] for row in matrix)
            precision = matrix[index][index] / column
            recall = matrix[index][index] / sum(matrix[index])
            assert payload["precision"][index] == pytest.approx(precision, abs=5e-5)
            assert payload["recall"][index] == pytest.approx(recall, abs=5e-5)
            assert payload["f1Score"][index] == pytest.approx(
                2 * precision * recall / (precision + recall), abs=5e-5
            )


def test_one_roc_line_per_class_with_a_measured_auc(archive: Storage) -> None:
    for project, run_id in archive.list_all_runs():
        artifacts = archive.read_artifacts(project, run_id)
        curves = [a for a in artifacts if a["type"] == "roc_curve"]
        if not curves:
            continue
        matrix = [a for a in artifacts if a["type"] == "confusion_matrix"][0]["metadata"]
        assert [c["metadata"]["className"] for c in curves] == matrix["labels"]

        for curve in curves:
            payload = curve["metadata"]
            fpr, tpr, thresholds = payload["fpr"], payload["tpr"], payload["thresholds"]
            assert len(fpr) == len(tpr) == len(thresholds)
            assert fpr[0] == 0.0 and fpr[-1] == 1.0
            assert tpr == sorted(tpr) and thresholds == sorted(thresholds, reverse=True)
            area = sum(
                (fpr[i] - fpr[i - 1]) * (tpr[i] + tpr[i - 1]) / 2 for i in range(1, len(fpr))
            )
            assert payload["auc"] == pytest.approx(area, abs=5e-5)


def test_feature_importance_has_the_key_the_component_requires(archive: Storage) -> None:
    seen = 0
    for project, run_id in archive.list_all_runs():
        for artifact in archive.read_artifacts(project, run_id):
            if artifact["type"] != "feature_importance":
                continue
            features = artifact["metadata"]["features"]
            assert features
            assert all({"name", "importance", "std"} <= set(f) for f in features)
            importances = [f["importance"] for f in features]
            assert importances == sorted(importances, reverse=True)
            assert sum(importances) == pytest.approx(1.0, abs=0.001)
            seen += 1
    assert seen >= 4


def test_only_finished_runs_carry_evaluation_artifacts(archive: Storage) -> None:
    """Crashed or unfinished runs never reached evaluation, so they have no artifacts."""
    for run_dir in run_dirs(archive):
        has_artifacts = (run_dir / "artifacts.jsonl").exists()
        assert has_artifacts == (state_of(run_dir) == RunState.COMPLETED.value)


def test_checkpoint_sidecars_sort_newest_first(archive: Storage) -> None:
    for project, run_id in archive.list_all_runs():
        checkpoints = archive.read_checkpoints(project, run_id)
        assert checkpoints
        assert any(c["name"] == "best" for c in checkpoints)
        created = [c["createdAt"] for c in checkpoints]
        assert created == sorted(created, reverse=True)
        for checkpoint in checkpoints:
            assert checkpoint["step"] is not None


# --------------------------------------------------------------------------------------
# System metrics
# --------------------------------------------------------------------------------------


def test_system_metrics_are_shape_a_and_homogeneous(archive: Storage) -> None:
    for project, run_id in archive.list_all_runs():
        samples = archive.read_system_metrics(project, run_id)
        assert isinstance(samples, list) and len(samples) >= 2
        # The table picks columns from the first sample, so all samples need the same keys.
        keys = set(samples[0])
        assert keys == {
            "timestamp",
            "cpu_percent",
            "memory_percent",
            "memory_used_mb",
            "memory_available_mb",
            "gpu_utilization",
            "gpu_memory_used_mb",
            "disk_usage_percent",
        }
        assert all(set(sample) == keys for sample in samples)

        timestamps = [sample["timestamp"] for sample in samples]
        assert timestamps == sorted(timestamps)
        # Epoch seconds here, unlike the relative seconds in metrics.jsonl.
        created = json.loads(
            (archive.run_path(project, run_id) / "metadata.json").read_text(encoding="utf-8")
        )["created_at"]
        assert timestamps[0] == pytest.approx(_epoch(created), abs=1.0)

        for sample in samples:
            assert 0 <= sample["cpu_percent"] <= 100
            assert 0 <= sample["memory_percent"] <= 100
            assert 0 <= sample["gpu_utilization"] <= 100
            assert sample["memory_used_mb"] + sample["memory_available_mb"] == 16384

        # GPU trace should vary (warm-up vs busy), not be flat.
        gpu = [sample["gpu_utilization"] for sample in samples]
        assert max(gpu) - min(gpu) > 40


# --------------------------------------------------------------------------------------
# Determinism and file hygiene
# --------------------------------------------------------------------------------------


def test_same_seed_is_byte_identical(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    assert demo.generate(first, runs=9, seed=3) == demo.generate(second, runs=9, seed=3)
    assert tree_bytes(first) == tree_bytes(second)


def test_a_different_seed_changes_the_archive(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    demo.generate(first, runs=6, seed=1)
    demo.generate(second, runs=6, seed=2)
    assert tree_bytes(first) != tree_bytes(second)


def test_regenerating_over_an_existing_tree_does_not_append(tmp_path: Path) -> None:
    """The .jsonl files are opened in append mode, so a rerun must truncate them first."""
    demo.generate(tmp_path, runs=5, seed=0)
    before = tree_bytes(tmp_path)
    demo.generate(tmp_path, runs=5, seed=0)
    assert tree_bytes(tmp_path) == before


def test_run_count_is_honoured(tmp_path: Path) -> None:
    assert demo.generate(tmp_path / "none", runs=0, seed=0) == []
    small = demo.generate(tmp_path / "small", runs=3, seed=0)
    assert len(small) == 3
    large = demo.generate(tmp_path / "large", runs=14, seed=0)
    assert len(large) == 14
    assert len(set(large)) == 14  # a collision would make one run unreachable
    assert Storage(tmp_path / "large").list_projects() == ["agnews-distilbert", "cifar10-cnn"]


def test_every_file_is_strict_json_without_nan(archive: Storage) -> None:
    """No ``NaN``/``Infinity``, BOM or CRLF: ``JSON.parse`` would reject them."""
    for path in sorted(archive.root.rglob("*")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")  # a UTF-8 BOM breaks JSON.parse
        assert b"\r\n" not in raw
        text = raw.decode("utf-8")
        assert "NaN" not in text and "Infinity" not in text
        if path.suffix == ".jsonl":
            for line in text.splitlines():
                if line.strip():
                    json.loads(line)
        else:
            json.loads(text)


def test_project_metadata_is_never_written(archive: Storage) -> None:
    """The generator doesn't write experiment descriptions."""
    assert not list(archive.root.rglob("project_metadata.json"))


# --------------------------------------------------------------------------------------
# The committed fixture
# --------------------------------------------------------------------------------------


def test_committed_fixture_matches_the_generator(tmp_path: Path) -> None:
    """Regenerate the fixture and compare it byte for byte with the committed one.

    A diff means the writer changed: review it, then commit the regenerated tree.
    """
    assert FIXTURE_TREE.is_dir(), "run demo.write_fixture_tree to create tests/fixtures"
    demo.write_fixture_tree(tmp_path)
    assert tree_bytes(tmp_path) == tree_bytes(FIXTURE_TREE)


def test_committed_fixture_is_small_and_complete() -> None:
    storage = Storage(FIXTURE_TREE)
    assert storage.list_projects() == ["agnews-distilbert", "cifar10-cnn"]
    states = {state_of(run_dir) for run_dir in run_dirs(storage)}
    assert states == {"completed", "failed", "interrupted", "running"}

    total = sum(path.stat().st_size for path in FIXTURE_TREE.rglob("*") if path.is_file())
    # Keep it small enough to review in diffs.
    assert total < 200_000

    for project, run_id in storage.list_all_runs():
        assert storage.read_run(project, run_id) is not None


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()
