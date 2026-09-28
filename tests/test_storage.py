"""Storage tests against the shapes the dashboard consumes.

Some pin known quirks (two config readers that disagree, two duration formatters, a run
named differently on two pages) so changing the reader breaks a test on purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlexperimenttracker import contract
from mlexperimenttracker.contract import camel_case, format_duration, map_state
from mlexperimenttracker.storage import Storage, StorageError

# --------------------------------------------------------------------------------------
# Fixtures: one example run as a real directory tree
# --------------------------------------------------------------------------------------

PROJECT = "churn-mlp"
RUN_ID = "churn-mlp_20260812T091403Z_7f3a"

METADATA = {
    "created_at": "2026-08-12T09:14:03.482+05:30",
    "name": "mlp-2x256-lr3e-4",
    "state": "initialized",
    "tags": ["baseline", "churn", "mlp"],
    "notes": "",
    "platform": "Windows-11-10.0.26200-SP0",
    "python_version": "3.13.2",
    "working_directory": "E:/Work/Live/code/Project/MLExperimentTracker/examples/churn",
}

CONFIG = {
    "learning_rate": 0.0003,
    "batch_size": 128,
    "epochs": 3,
    "optimizer": "adamw",
    "mixed_precision": True,
    "early_stopping": None,
}

METRIC_ROWS = [
    {"step": 200, "timestamp": 38.4, "absolute_timestamp": 1786506281.88, "loss": 1.9124, "accuracy": 0.3122},
    {
        "step": 391,
        "timestamp": 74.1,
        "absolute_timestamp": 1786506317.58,
        "loss": 1.5507,
        "accuracy": 0.4361,
        "val_loss": 1.4022,
        "val_accuracy": 0.4890,
    },
    {"step": 600, "timestamp": 113.8, "absolute_timestamp": 1786506357.28, "loss": 1.2038, "accuracy": 0.5677},
]

SUMMARY = {
    "state": "completed",
    "duration": 231.4,
    "end_time": "2026-08-12T09:17:54.882+05:30",
    "notes": "",
    "metrics_summary": {
        "loss": {"latest": 0.7402, "mean": 1.2086, "min": 0.7402, "max": 1.9124, "stddev": 0.4106},
        "accuracy": {"latest": 0.7418, "mean": 0.5678, "min": 0.3122, "max": 0.7418, "stddev": 0.1517},
    },
}


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "root")


@pytest.fixture()
def populated(storage: Storage) -> Storage:
    run_dir = storage.root / PROJECT / RUN_ID
    write_json(run_dir / contract.METADATA_FILE, METADATA)
    write_json(run_dir / contract.CONFIG_FILE, CONFIG)
    write_json(run_dir / contract.SUMMARY_FILE, SUMMARY)
    write_jsonl(run_dir / contract.METRICS_FILE, METRIC_ROWS)
    write_json(
        run_dir / contract.SYSTEM_METRICS_FILE,
        [{"timestamp": 1786506243.5, "cpu_percent": 41.2, "memory_percent": 46.8}],
    )
    write_jsonl(
        run_dir / contract.ARTIFACTS_FILE,
        [
            {
                "name": "confusion_matrix",
                "type": "confusion_matrix",
                "version": "1",
                "created_at": "2026-08-12T09:17:52.640+05:30",
                "file_count": 0,
                "metadata": {"labels": ["retained", "churned"], "matrix": [[812, 74], [63, 251]]},
            }
        ],
    )
    (run_dir / contract.ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)
    (run_dir / contract.ARTIFACTS_DIR / "confusion_matrix.png").write_bytes(b"\x89PNG")
    write_json(
        run_dir / contract.CHECKPOINTS_DIR / "epoch_02.json",
        {"checkpoint_name": "epoch_02", "created_at": "2026-08-12T09:16:31.980+05:30", "step": 782},
    )
    write_json(
        run_dir / contract.CHECKPOINTS_DIR / "epoch_03.json",
        {"checkpoint_name": "epoch_03", "created_at": "2026-08-12T09:17:46.210+05:30", "step": 1173},
    )
    return storage


# --------------------------------------------------------------------------------------
# Containment
# --------------------------------------------------------------------------------------

# %2f and %5c are shown decoded, as they arrive after URL decoding.
TRAVERSAL_SEGMENTS = [
    "",
    ".",
    "..",
    "../secrets",
    "../../secrets",
    "..%2f..%2fsecrets".replace("%2f", "/"),
    "..%5c..%5csecrets".replace("%5c", "\\"),
    "..\\..\\Windows",
    "foo/bar",
    "foo\\bar",
    "/etc/passwd",
    "\\Windows\\Temp",
    "//server/share",
    "C:\\Windows",
    "C:/Windows",
    "c:secrets",
    "run\x00.json",
    "sub/../..",
]


@pytest.mark.parametrize("segment", TRAVERSAL_SEGMENTS)
def test_resolve_within_refuses_every_traversal_payload(storage: Storage, segment: str) -> None:
    assert storage.resolve_within(segment) is None
    assert storage.project_path(segment) is None
    assert storage.run_path(segment, "run-1") is None
    assert storage.run_path("proj", segment) is None


@pytest.mark.parametrize("segment", [None, 5, b"proj", ["proj"]])
def test_resolve_within_refuses_non_strings(storage: Storage, segment: object) -> None:
    assert storage.resolve_within(segment) is None  # type: ignore[arg-type]


def test_resolve_within_accepts_ordinary_names(storage: Storage) -> None:
    resolved = storage.resolve_within("churn-mlp", "run_1.a")
    assert resolved == storage.root / "churn-mlp" / "run_1.a"


def test_reads_degrade_for_unaddressable_names(populated: Storage) -> None:
    """A refused name reads as "not found", not an error."""
    escape = ".." + "/" + ".."
    assert populated.read_run(escape, RUN_ID) is None
    assert populated.read_experiment(escape) is None
    assert populated.read_experiment_runs(escape) == []
    assert populated.read_metrics(escape, RUN_ID) == []
    assert populated.read_metrics_timeseries(escape, RUN_ID) == []
    assert populated.read_system_metrics(escape, RUN_ID) == {}
    assert populated.read_checkpoints(escape, RUN_ID) == []
    assert populated.read_artifacts(escape, RUN_ID) == []
    assert populated.export_metrics_csv(escape, RUN_ID) == ""
    assert populated.update_run_tags(escape, RUN_ID, ["x"]) is False
    assert populated.update_run_description(escape, RUN_ID, "x") is False
    assert populated.update_experiment_description(escape, "x", "x") is False


def test_traversal_cannot_reach_a_real_run_outside_the_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "victim"
    write_json(outside / contract.METADATA_FILE, {"created_at": "2026-01-01T00:00:00+00:00"})
    storage = Storage(tmp_path / "root")
    storage.root.mkdir(parents=True, exist_ok=True)
    assert storage.read_run("..", "outside") is None
    assert storage.read_run("../outside", "victim") is None
    assert storage.resolve_within("..", "outside", "victim") is None


def test_create_run_refuses_an_unaddressable_name(storage: Storage) -> None:
    with pytest.raises(StorageError):
        storage.create_run("../escape", RUN_ID, {})


def test_root_resolution_prefers_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(contract.STORAGE_ENV_VAR, str(tmp_path / "from-env"))
    assert Storage().root == (tmp_path / "from-env").resolve()
    assert Storage(tmp_path / "explicit").root == (tmp_path / "explicit").resolve()

    monkeypatch.delenv(contract.STORAGE_ENV_VAR)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert Storage().root == (tmp_path / "home" / contract.DEFAULT_STORAGE_DIRNAME).resolve()


# --------------------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------------------


def test_discovery_sees_directories_only(populated: Storage) -> None:
    (populated.root / "loose.json").write_text("{}", encoding="utf-8")
    (populated.root / PROJECT / "project_metadata.json").write_text("{}", encoding="utf-8")
    assert populated.list_projects() == [PROJECT]
    assert populated.list_runs(PROJECT) == [RUN_ID]


def test_missing_root_is_empty_not_an_error(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "never-created")
    assert storage.list_projects() == []
    assert storage.list_experiments() == []
    assert storage.read_latest_run() is None
    assert storage.read_latest_experiment() is None
    assert storage.find_run("anything") is None


def test_find_run_resolves_collisions_in_sorted_project_order(storage: Storage) -> None:
    for project in ("zeta", "alpha"):
        write_json(storage.root / project / "run_1" / contract.METADATA_FILE, {"created_at": "2026-01-01T00:00:00Z"})
    assert storage.find_run("run_1") == ("alpha", "run_1")
    assert storage.find_run("absent") is None


# --------------------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------------------


def test_run_written_by_the_writer_reads_back(storage: Storage) -> None:
    run_dir = storage.create_run(PROJECT, RUN_ID, dict(METADATA))
    storage.write_json(run_dir / contract.CONFIG_FILE, CONFIG)
    storage.write_json(run_dir / contract.SUMMARY_FILE, SUMMARY)
    for row in METRIC_ROWS:
        storage.append_jsonl(run_dir / contract.METRICS_FILE, row)

    run = storage.read_run(PROJECT, RUN_ID)
    assert run is not None
    assert run["_id"] == RUN_ID
    assert run["experimentId"] == run["experimentName"] == PROJECT
    assert run["status"] == "completed"
    assert run["state"] == "completed"
    assert run["duration"] == 231.4
    assert run["durationFormatted"] == "3m 51s"
    assert run["createdAt"] == run["startTime"] == METADATA["created_at"]
    assert run["endTime"] == SUMMARY["end_time"]
    assert run["tags"] == ["baseline", "churn", "mlp"]
    # notes is empty, so the name falls back to the run id. metadata.name is not used here.
    assert run["name"] == f"Run {RUN_ID}"
    assert run["description"] == ""
    assert run["pythonVersion"] == "3.13.2"
    assert run["metricsHistory"] == METRIC_ROWS
    assert run["config"] == CONFIG
    assert run["parameters"]["learningRate"] == 0.0003

    assert storage.read_metrics_timeseries(PROJECT, RUN_ID) == METRIC_ROWS
    assert storage.find_run(RUN_ID) == (PROJECT, RUN_ID)
    assert storage.read_latest_run()["_id"] == RUN_ID


def test_run_object_key_set_is_frozen(populated: Storage) -> None:
    """The frontend uses these keys as-is, so the full set is pinned."""
    run = populated.read_run(PROJECT, RUN_ID)
    assert run is not None
    assert sorted(run) == sorted(
        [
            "_id",
            "name",
            "experimentId",
            "experimentName",
            "status",
            "description",
            "tags",
            "duration",
            "durationFormatted",
            "createdAt",
            "startTime",
            "endTime",
            "state",
            "platform",
            "pythonVersion",
            "workingDirectory",
            "artifacts",
            "artifactsCount",
            "parameters",
            "metrics",
            "metricsHistory",
            "systemMetrics",
            "checkpoints",
            "config",
            "summary",
        ]
    )


def test_a_run_without_metadata_is_invisible(storage: Storage) -> None:
    run_dir = storage.root / PROJECT / RUN_ID
    write_json(run_dir / contract.SUMMARY_FILE, SUMMARY)
    assert storage.read_run(PROJECT, RUN_ID) is None
    assert storage.read_experiment_runs(PROJECT) == []
    # But it still counts toward totalRuns, lowering the success rate.
    assert storage.read_experiment(PROJECT)["stats"]["totalRuns"] == 1


def test_malformed_files_degrade_rather_than_raise(storage: Storage) -> None:
    run_dir = storage.root / PROJECT / RUN_ID
    write_json(run_dir / contract.METADATA_FILE, METADATA)
    (run_dir / contract.SUMMARY_FILE).write_text("{ truncated", encoding="utf-8")
    (run_dir / contract.CONFIG_FILE).write_text("not json at all", encoding="utf-8")
    (run_dir / contract.SYSTEM_METRICS_FILE).write_text("[{", encoding="utf-8")

    run = storage.read_run(PROJECT, RUN_ID)
    assert run is not None
    assert run["metrics"] == {}
    assert run["parameters"] == {}
    assert run["duration"] == 0
    assert run["durationFormatted"] == "0s"
    # No summary means no state, which reads as running.
    assert run["status"] == "running"
    assert storage.read_system_metrics(PROJECT, RUN_ID) == {}
    assert storage.read_json(run_dir / contract.SUMMARY_FILE) is None
    assert storage.read_json(run_dir / "absent.json") is None


# --------------------------------------------------------------------------------------
# metrics_summary flattening
# --------------------------------------------------------------------------------------


def test_metrics_summary_flattens_into_sibling_keys(storage: Storage) -> None:
    run_dir = storage.root / PROJECT / RUN_ID
    write_json(run_dir / contract.METADATA_FILE, METADATA)
    write_json(
        run_dir / contract.SUMMARY_FILE,
        {
            "metrics_summary": {
                "val_loss": {"latest": 0.88, "mean": 1.10, "max": 1.40, "min": 0.88, "stddev": 0.21},
                # No `latest`: only its stats show up.
                "orphan": {"mean": 1.0},
                # A bare number yields nothing.
                "naive": 0.31,
                # Only the four stat names are read; std/var/count are dropped.
                "extra": {"latest": 1.0, "std": 9.0, "var": 9.0, "count": 6},
            }
        },
    )
    metrics = storage.read_run(PROJECT, RUN_ID)["metrics"]
    assert metrics == {
        "valLoss": 0.88,
        "valLossMean": 1.10,
        "valLossMax": 1.40,
        "valLossMin": 0.88,
        "valLossStddev": 0.21,
        "orphanMean": 1.0,
        "extra": 1.0,
    }
    # The experiment view shows latest only.
    assert storage.read_experiment_runs(PROJECT)[0]["metrics"] == {"valLoss": 0.88, "extra": 1.0}


def test_a_metric_named_like_a_stat_collides(storage: Storage) -> None:
    """loss_mean and the mean of loss map to the same key; last write wins.

    Hence the SDK warning on metric names ending in a stat suffix.
    """
    run_dir = storage.root / PROJECT / RUN_ID
    write_json(run_dir / contract.METADATA_FILE, METADATA)
    write_json(
        run_dir / contract.SUMMARY_FILE,
        {"metrics_summary": {"loss": {"latest": 1.0, "mean": 2.0}, "loss_mean": {"latest": 99.0}}},
    )
    assert storage.read_run(PROJECT, RUN_ID)["metrics"]["lossMean"] == 99.0


# --------------------------------------------------------------------------------------
# Wide jsonl
# --------------------------------------------------------------------------------------


def test_wide_rows_pivot_into_one_series_per_metric(populated: Storage) -> None:
    series = {s["name"]: s["data"] for s in populated.read_metrics(PROJECT, RUN_ID)}
    assert set(series) == {"loss", "accuracy", "val_loss", "val_accuracy"}
    assert len(series["loss"]) == 3
    # Sparse: val metrics are on one step only.
    assert series["val_loss"] == [{"step": 391, "value": 1.4022, "timestamp": 74.1}]
    for reserved in contract.RESERVED_METRIC_KEYS:
        assert reserved not in series


def test_timeseries_filters_and_reshapes(populated: Storage) -> None:
    assert populated.read_metrics_timeseries(PROJECT, RUN_ID, "val_loss") == [
        {"timestamp": 74.1, "absolute_timestamp": 1786506317.58, "step": 391, "value": 1.4022}
    ]
    assert populated.read_metrics_timeseries(PROJECT, RUN_ID, "absent") == []
    assert populated.read_metrics_timeseries(PROJECT, RUN_ID) == METRIC_ROWS


def test_a_torn_final_line_costs_one_step_not_the_file(storage: Storage) -> None:
    path = storage.root / PROJECT / RUN_ID / contract.METRICS_FILE
    write_jsonl(path, METRIC_ROWS)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"step": 999, "loss": 0.1')
    assert storage.read_jsonl(path) == METRIC_ROWS

    # Skip blank lines and valid JSON that is not an object.
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write('\n\n"a string"\n[1, 2]\n{"step": 7}\n')
    assert storage.read_jsonl(path) == METRIC_ROWS + [{"step": 7}]


def test_append_jsonl_writes_one_line_per_record(storage: Storage) -> None:
    path = storage.root / PROJECT / RUN_ID / contract.METRICS_FILE
    for row in METRIC_ROWS:
        storage.append_jsonl(path, row)
    raw = path.read_bytes()
    assert raw.count(b"\n") == len(METRIC_ROWS)
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_non_finite_values_are_refused_rather_than_written(storage: Storage) -> None:
    """NaN/Infinity are not JSON; the reader would silently drop the whole line."""
    path = storage.root / PROJECT / RUN_ID / contract.METRICS_FILE
    with pytest.raises(StorageError):
        storage.append_jsonl(path, {"step": 1, "loss": float("nan")})
    with pytest.raises(StorageError):
        storage.write_json(path.parent / contract.SUMMARY_FILE, {"duration": float("inf")})


# --------------------------------------------------------------------------------------
# The two config readers
# --------------------------------------------------------------------------------------


def test_the_two_config_readers_disagree(storage: Storage) -> None:
    """One flattens one level, the other drops nesting. Pinned: the frontend uses both."""
    run_dir = storage.root / PROJECT / RUN_ID
    write_json(run_dir / contract.METADATA_FILE, METADATA)
    write_json(
        run_dir / contract.CONFIG_FILE,
        {
            "learning_rate": 0.0003,
            "early_stopping": None,
            "model": {"type": "mlp", "encoder": {"layers": 12}},
            "layers": ["a", "b"],
            "storage": {"path": "/tmp"},
            "system": "linux",
            "logging": {"level": "info"},
        },
    )

    detail = storage.read_run(PROJECT, RUN_ID)["parameters"]
    assert detail == {
        "learningRate": 0.0003,
        "earlyStopping": None,
        # One level only; `encoder` is dropped.
        "modelType": "mlp",
        # Arrays flatten by index.
        "layers0": "a",
        "layers1": "b",
        # storage/system/logging are dropped only when they are objects.
        "system": "linux",
    }

    comparison = storage.read_experiment_runs(PROJECT)[0]["parameters"]
    assert comparison == {
        "learningRate": 0.0003,
        "earlyStopping": None,
        "system": "linux",
    }
    assert "modelType" not in comparison and "layers0" not in comparison


# --------------------------------------------------------------------------------------
# Experiments
# --------------------------------------------------------------------------------------


def test_experiment_aggregation(storage: Storage) -> None:
    def add(run_id: str, state: str | None, duration: float, created: str) -> None:
        run_dir = storage.root / PROJECT / run_id
        write_json(
            run_dir / contract.METADATA_FILE,
            {"created_at": created, "name": f"named-{run_id}", "tags": ["a", "b"]},
        )
        summary: dict = {"duration": duration}
        if state is not None:
            summary["state"] = state
        write_json(run_dir / contract.SUMMARY_FILE, summary)

    add("run_1", "completed", 60.0, "2026-08-12T09:00:00+00:00")
    add("run_2", "failed", 120.0, "2026-08-12T10:00:00+00:00")
    add("run_3", "interrupted", 60.0, "2026-08-12T08:00:00+00:00")

    experiment = storage.read_experiment(PROJECT)
    assert experiment is not None
    assert experiment["_id"] == experiment["name"] == PROJECT
    assert experiment["description"] == f"Experiment: {PROJECT}"
    assert experiment["tags"] == ["a", "b"]
    assert experiment["activityTimeline"] == []
    assert experiment["createdAt"] == "2026-08-12T10:00:00.000Z"
    assert experiment["stats"] == {
        "totalRuns": 3,
        "completedRuns": 1,
        "failedRuns": 1,
        # `interrupted` is in no bucket; the counters don't map state.
        "runningRuns": 0,
        "successRate": "33%",
        "avgDuration": "1m 20s",
        "lastRun": "2026-08-12T10:00:00.000Z",
    }
    # Raw state here, so interrupted is not shown as archived...
    assert {r["_id"]: r["status"] for r in experiment["runs"]} == {
        "run_1": "completed",
        "run_2": "failed",
        "run_3": "interrupted",
    }
    # ...while the comparison table does map it.
    assert {r["_id"]: r["status"] for r in storage.read_experiment_runs(PROJECT)} == {
        "run_1": "completed",
        "run_2": "failed",
        "run_3": "archived",
    }
    # Same run, different names on the two pages.
    assert experiment["runs"][0]["name"] == "named-run_1"
    assert storage.read_experiment_runs(PROJECT)[0]["name"] == "Run run_1"
    # Duration is a string here but a number on the run detail path.
    assert storage.read_experiment_runs(PROJECT)[0]["duration"] == "1m 0s"

    assert storage.read_experiment("absent") is None
    assert [e["_id"] for e in storage.list_experiments()] == [PROJECT]
    assert storage.read_latest_experiment()["_id"] == PROJECT


def test_experiment_description_comes_from_the_first_run_with_notes(storage: Storage) -> None:
    write_json(
        storage.root / PROJECT / "run_1" / contract.METADATA_FILE,
        {"created_at": "2026-08-12T09:00:00+00:00", "notes": ""},
    )
    write_json(
        storage.root / PROJECT / "run_2" / contract.METADATA_FILE,
        {"created_at": "2026-08-12T09:30:00+00:00", "notes": "sweep over depth"},
    )
    assert storage.read_experiment(PROJECT)["description"] == "sweep over depth"


def test_a_run_without_created_at_does_not_poison_the_aggregate(storage: Storage) -> None:
    """A run with no created_at is skipped when dating the project.

    Otherwise its Invalid Date throws a RangeError and 500s the whole dashboard.
    """
    write_json(storage.root / PROJECT / "aaa_no_date" / contract.METADATA_FILE, {"state": "running"})
    write_json(
        storage.root / PROJECT / "bbb_dated" / contract.METADATA_FILE,
        {"created_at": "2026-08-12T09:00:00+00:00"},
    )
    experiment = storage.read_experiment(PROJECT)
    assert experiment["stats"]["lastRun"] == "2026-08-12T09:00:00.000Z"
    assert experiment["createdAt"] == "2026-08-12T09:00:00.000Z"
    # The undated run still appears, dated "now".
    assert len(experiment["runs"]) == 2


def test_a_project_with_no_datable_runs_reports_no_last_run(storage: Storage) -> None:
    write_json(storage.root / PROJECT / "run_1" / contract.METADATA_FILE, {"state": "running"})
    experiment = storage.read_experiment(PROJECT)
    assert experiment["createdAt"] is None
    assert experiment["stats"]["lastRun"] == "N/A"


def test_a_non_list_tags_field_does_not_take_down_the_dashboard(storage: Storage) -> None:
    write_json(
        storage.root / PROJECT / "run_1" / contract.METADATA_FILE,
        {"created_at": "2026-08-12T09:00:00+00:00", "tags": "baseline"},
    )
    assert storage.read_experiment(PROJECT)["tags"] == []
    assert storage.read_run(PROJECT, "run_1")["tags"] == "baseline"


# --------------------------------------------------------------------------------------
# Artifacts, checkpoints, system metrics
# --------------------------------------------------------------------------------------


def test_artifacts_combine_records_with_loose_files(populated: Storage) -> None:
    artifacts = populated.read_artifacts(PROJECT, RUN_ID)
    assert [a["_id"] for a in artifacts] == ["confusion_matrix_1", "confusion_matrix.png"]
    assert artifacts[0]["metadata"]["labels"] == ["retained", "churned"]
    # Payload keys are passed through untransformed.
    assert "matrix" in artifacts[0]["metadata"]
    placeholder = artifacts[1]
    assert placeholder["type"] == "unknown" and placeholder["version"] == "latest"
    assert populated.read_run(PROJECT, RUN_ID)["artifactsCount"] == 2


def test_artifact_subdirectories_are_invisible(populated: Storage) -> None:
    (populated.root / PROJECT / RUN_ID / contract.ARTIFACTS_DIR / "nested").mkdir()
    (populated.root / PROJECT / RUN_ID / contract.ARTIFACTS_DIR / "nested" / "x.png").write_bytes(b"x")
    assert [a["name"] for a in populated.read_artifacts(PROJECT, RUN_ID)] == [
        "confusion_matrix",
        "confusion_matrix.png",
    ]


def test_checkpoints_are_newest_first_and_json_only(populated: Storage) -> None:
    (populated.root / PROJECT / RUN_ID / contract.CHECKPOINTS_DIR / "epoch_03.pt").write_bytes(b"weights")
    checkpoints = populated.read_checkpoints(PROJECT, RUN_ID)
    assert [c["name"] for c in checkpoints] == ["epoch_03", "epoch_02"]
    assert [c["step"] for c in checkpoints] == [1173, 782]
    assert checkpoints[0]["size"] > 0


def test_system_metrics_are_returned_verbatim(populated: Storage) -> None:
    assert populated.read_system_metrics(PROJECT, RUN_ID) == [
        {"timestamp": 1786506243.5, "cpu_percent": 41.2, "memory_percent": 46.8}
    ]
    assert populated.read_system_metrics(PROJECT, "absent") == {}


# --------------------------------------------------------------------------------------
# CSV export
# --------------------------------------------------------------------------------------


def test_csv_export_is_the_sorted_union_of_all_keys(populated: Storage) -> None:
    csv = populated.export_metrics_csv(PROJECT, RUN_ID)
    lines = csv.strip("\n").split("\n")
    assert lines[0] == "absolute_timestamp,accuracy,loss,step,timestamp,val_accuracy,val_loss"
    # Missing keys are empty cells.
    assert lines[1] == "1786506281.88,0.3122,1.9124,200,38.4,,"
    assert lines[2].endswith("0.489,1.4022")


def test_csv_export_quotes_per_rfc_4180(storage: Storage) -> None:
    """Unquoted, a comma inside a value would shift every column after it."""
    path = storage.root / PROJECT / RUN_ID / contract.METRICS_FILE
    write_jsonl(
        path,
        [
            {"step": 1, "note": "a,b", "loss": 1.0, "we,ird": 1},
            {"step": 2, "note": 'he said "hi"', "flag": True, "obj": {"a": 1}},
        ],
    )
    csv = storage.export_metrics_csv(PROJECT, RUN_ID)
    assert csv == (
        'flag,loss,note,obj,step,"we,ird"\n'
        ',1,"a,b",,1,1\n'
        'true,,"he said ""hi""","{""a"":1}",2,\n'
    )


def test_csv_export_of_an_empty_file_is_empty(storage: Storage) -> None:
    (storage.root / PROJECT / RUN_ID).mkdir(parents=True)
    assert storage.export_metrics_csv(PROJECT, RUN_ID) == ""


# --------------------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------------------


def test_update_run_tags_preserves_unknown_keys(populated: Storage) -> None:
    path = populated.root / PROJECT / RUN_ID / contract.METADATA_FILE
    assert populated.update_run_tags(PROJECT, RUN_ID, ["fresh"]) is True
    metadata = json.loads(path.read_text(encoding="utf-8"))
    assert metadata["tags"] == ["fresh"]
    assert "updated_at" in metadata
    # Other keys are untouched.
    for key, value in METADATA.items():
        if key != "tags":
            assert metadata[key] == value


def test_update_run_tags_refuses_a_run_without_metadata(storage: Storage) -> None:
    (storage.root / PROJECT / RUN_ID).mkdir(parents=True)
    assert storage.update_run_tags(PROJECT, RUN_ID, ["x"]) is False


def test_update_run_description_creates_the_summary_if_absent(storage: Storage) -> None:
    run_dir = storage.root / PROJECT / RUN_ID
    storage.create_run(PROJECT, RUN_ID, dict(METADATA))
    assert storage.update_run_description(PROJECT, RUN_ID, "a better description") is True
    summary = json.loads((run_dir / contract.SUMMARY_FILE).read_text(encoding="utf-8"))
    assert summary["notes"] == "a better description"
    # notes is also the display name.
    assert storage.read_run(PROJECT, RUN_ID)["name"] == "a better description"


def test_update_experiment_description_will_not_create_a_project(storage: Storage) -> None:
    assert storage.update_experiment_description("brand-new", "brand-new", "hi") is False
    assert not (storage.root / "brand-new").exists()
    (storage.root / PROJECT).mkdir(parents=True)
    assert storage.update_experiment_description(PROJECT, PROJECT, "hi") is True
    assert (storage.root / PROJECT / "project_metadata.json").exists()
    # The experiment read path uses it, so the edit survives a reload.
    assert storage.read_experiment(PROJECT)["description"] == "hi"


def test_write_json_is_atomic_and_leaves_no_debris(storage: Storage) -> None:
    path = storage.root / PROJECT / "data.json"
    storage.write_json(path, {"a": 1, "ünicode": "ok"})
    assert storage.read_json(path) == {"a": 1, "ünicode": "ok"}
    assert path.read_bytes().startswith(b"{")
    storage.write_json(path, {"a": 2})
    assert storage.read_json(path) == {"a": 2}
    assert [p.name for p in path.parent.iterdir()] == ["data.json"]


def test_create_run_writes_metadata_first(storage: Storage) -> None:
    run_dir = storage.create_run(PROJECT, RUN_ID, {"created_at": "2026-08-12T09:00:00+00:00"})
    assert run_dir == storage.root / PROJECT / RUN_ID
    assert (run_dir / contract.METADATA_FILE).exists()
    assert storage.read_run(PROJECT, RUN_ID) is not None


# --------------------------------------------------------------------------------------
# contract.py
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("learning_rate", "learningRate"),
        ("batch_size", "batchSize"),
        ("val_loss", "valLoss"),
        ("val_ACC", "valAcc"),
        ("learning rate", "learningRate"),
        ("learning-rate", "learningRate"),
        ("learning_RATE", "learningRate"),
        # No separator: one token, fully lowercased.
        ("LearningRate", "learningrate"),
        ("top_1_accuracy", "top1Accuracy"),
        ("f1", "f1"),
        ("", ""),
        ("_leading", "Leading"),
        ("trailing_", "trailing"),
        ("a__b", "aB"),
        ("mixed_sep-and space", "mixedSepAndSpace"),
    ],
)
def test_camel_case(raw: str, expected: str) -> None:
    assert camel_case(raw) == expected


def test_camel_case_collisions_are_real(storage: Storage) -> None:
    assert camel_case("learning_rate") == camel_case("learning-rate") == camel_case("learning rate")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (231.4, "3m 51s"),
        (3903, "1h 5m 3s"),
        (3.2, "3s"),
        (0, "0s"),
        (None, "0s"),
        ("231.4", "0s"),
        (True, "0s"),
        (float("nan"), "0s"),
        # Halves round up, not to even (Python's round() gives "30s").
        (30.5, "31s"),
        # Round the total first, so it never renders "60s" or "59m 60s".
        (59.6, "1m 0s"),
        (3599.6, "1h 0m 0s"),
        (3659.7, "1h 1m 0s"),
    ],
)
def test_format_duration(seconds: object, expected: str) -> None:
    assert format_duration(seconds) == expected  # type: ignore[arg-type]


def test_a_duration_never_displays_sixty_seconds() -> None:
    """Checked for every tenth of a second across 0-600s."""
    for tenths in range(0, 6000):
        seconds = tenths / 10
        for rendered in (
            contract.format_duration(seconds),
            contract.format_duration_no_hours(seconds),
        ):
            assert " 60s" not in rendered and not rendered.startswith("60s"), (
                f"{seconds}s rendered as {rendered!r}"
            )


def test_format_duration_without_hours_is_a_separate_formatter() -> None:
    assert contract.format_duration_no_hours(3903) == "65m 3s"
    assert contract.format_duration_no_hours(3.2) == "3s"
    # 3599.6 carries to 3600: "60m 0s" here, "1h 0m 0s" on the run page.
    assert contract.format_duration_no_hours(3599.6) == "60m 0s"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("initialized", "running"),
        ("running", "running"),
        ("completed", "completed"),
        ("failed", "failed"),
        ("interrupted", "archived"),
        ("Completed", "running"),
        ("finished", "running"),
        ("", "running"),
        (None, "running"),
    ],
)
def test_map_state(state: str | None, expected: str) -> None:
    assert map_state(state) == expected


def test_contract_vocabulary_is_exact() -> None:
    assert contract.STAT_KEYS == ("mean", "max", "min", "stddev")
    assert contract.RESERVED_METRIC_KEYS == {
        "timestamp",
        "absolute_timestamp",
        "step",
        "run_id",
        "run_status",
        "run_state",
        "start_timestamp",
    }
    assert {s.value for s in contract.RunState} == {
        "initialized",
        "running",
        "completed",
        "failed",
        "interrupted",
    }
    assert {s.value for s in contract.UIStatus} == {"running", "completed", "failed", "archived"}
    assert contract.TERMINAL_STATES == {
        contract.RunState.COMPLETED,
        contract.RunState.FAILED,
        contract.RunState.INTERRUPTED,
    }
    assert set(contract.STATE_TO_UI_STATUS) == set(contract.RunState)
    assert contract.STORAGE_ENV_VAR == "EXPERIMENT_STORAGE_PATH"
    assert contract.DEFAULT_STORAGE_DIRNAME == ".experiment_tracker"
