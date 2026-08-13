"""Parity with the Express backend this server replaces.

The React frontend was written against `ml_backend`, so its payloads are the requirement:
a key renamed, a number turned into a string, or an object that gained a level of nesting
breaks a page rather than a test. This file is what stops that happening silently.

It is in two halves, and the split is deliberate.

The first half runs everywhere. It pins the payloads against the worked example in
`docs/DATA-CONTRACT.md` section 5 — a fixture built for exactly this purpose, whose
expected output that document states field by field. Those values were diffed against the
running Express services and matched, so asserting on them is asserting on the reader's
behaviour without needing the reader present.

The second half runs only where Node and `ml_backend/node_modules` exist. It starts the
real Express server and this one against one storage tree and diffs every read route.
That is the assertion that cannot go stale: the first half encodes what Express did on the
day it was measured, and only the second half notices if that was ever wrong.

Divergences that are deliberate are asserted *as* divergences, not tolerated. Each one is
a decision that should have to be re-taken to be undone:

1. `undefined` becomes `null`. `JSON.stringify` drops a key whose value is `undefined`, so
   Express returns 20 keys for a bare run and 25 for a complete one. A fixed key set is
   easier to type against and both are falsy in React.
2. The CSV is RFC 4180 quoted. Express joined raw values with commas, so one value
   containing a comma shifted every column to its right in a file people open in a
   spreadsheet.
3. Malformed input degrades instead of 500ing the page. A non-array `tags` or a run with
   no `created_at` took the whole dashboard down for every project.
4. A non-finite metric costs its own value, not its entire row.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from mlexperimenttracker.server.app import create_app
from mlexperimenttracker.storage import Storage

REPO = Path(__file__).resolve().parents[1]
ML_BACKEND = REPO / "ml_backend"

PROJECT = "churn-mlp"
RUN = "churn-mlp_20260812T091403Z_7f3a"


# --------------------------------------------------------------------------------------
# The DATA-CONTRACT section 5 worked example, verbatim
# --------------------------------------------------------------------------------------

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
    "weight_decay": 0.0005,
    "hidden_sizes": "256,256",
    "dropout": 0.2,
    "dataset": "churn_v3",
    "seed": 1337,
    "device": "cuda:0",
    "mixed_precision": True,
    "early_stopping": None,
}

METRIC_ROWS = [
    {"step": 200, "timestamp": 38.4, "absolute_timestamp": 1786506281.88, "loss": 1.9124, "accuracy": 0.3122},
    {"step": 391, "timestamp": 74.1, "absolute_timestamp": 1786506317.58, "loss": 1.5507, "accuracy": 0.4361, "val_loss": 1.4022, "val_accuracy": 0.4890},
    {"step": 600, "timestamp": 113.8, "absolute_timestamp": 1786506357.28, "loss": 1.2038, "accuracy": 0.5677},
    {"step": 782, "timestamp": 148.2, "absolute_timestamp": 1786506391.68, "loss": 1.0114, "accuracy": 0.6402, "val_loss": 1.0455, "val_accuracy": 0.6301},
    {"step": 1000, "timestamp": 189.6, "absolute_timestamp": 1786506433.08, "loss": 0.8331, "accuracy": 0.7089},
    {"step": 1173, "timestamp": 222.5, "absolute_timestamp": 1786506465.98, "loss": 0.7402, "accuracy": 0.7418, "val_loss": 0.8817, "val_accuracy": 0.6952},
]

SUMMARY = {
    "state": "completed",
    "duration": 231.4,
    "end_time": "2026-08-12T09:17:54.882+05:30",
    "notes": "",
    "metrics_summary": {
        "loss": {"latest": 0.7402, "mean": 1.2086, "min": 0.7402, "max": 1.9124, "stddev": 0.4106},
        "accuracy": {"latest": 0.7418, "mean": 0.5678, "min": 0.3122, "max": 0.7418, "stddev": 0.1517},
        "val_loss": {"latest": 0.8817, "mean": 1.1098, "min": 0.8817, "max": 1.4022, "stddev": 0.2173},
        "val_accuracy": {"latest": 0.6952, "mean": 0.6048, "min": 0.4890, "max": 0.6952, "stddev": 0.0861},
    },
}

SYSTEM_SAMPLES = [
    {"timestamp": 1786506243.5, "cpu_percent": 41.2, "memory_percent": 46.8, "memory_used_mb": 7492, "memory_available_mb": 8892, "gpu_utilization": 0.0, "gpu_memory_used_mb": 312, "disk_usage_percent": 71.4},
    {"timestamp": 1786506273.5, "cpu_percent": 78.9, "memory_percent": 52.1, "memory_used_mb": 8340, "memory_available_mb": 8044, "gpu_utilization": 91.4, "gpu_memory_used_mb": 4188, "disk_usage_percent": 71.4},
]

ARTIFACT_LINES = [
    {"name": "confusion_matrix", "type": "confusion_matrix", "version": "1", "created_at": "2026-08-12T09:17:52.640+05:30", "file_count": 0, "metadata": {"labels": ["retained", "churned"], "matrix": [[812, 74], [63, 251]], "accuracy": 0.8858, "precision": [0.9280, 0.7723], "recall": [0.9165, 0.7994], "f1Score": [0.9222, 0.7856]}},
    {"name": "roc_curve_churned", "type": "roc_curve", "version": "1", "created_at": "2026-08-12T09:17:52.712+05:30", "file_count": 0, "metadata": {"fpr": [0.0, 0.02, 1.0], "tpr": [0.0, 0.35, 1.0], "thresholds": [1.0, 0.86, 0.0], "auc": 0.9137, "className": "churned"}},
    {"name": "feature_importance", "type": "feature_importance", "version": "1", "created_at": "2026-08-12T09:17:52.804+05:30", "file_count": 0, "metadata": {"features": [{"name": "tenure_months", "importance": 0.284, "std": 0.019}]}},
]

CHECKPOINTS = {
    "epoch_02": {"checkpoint_name": "epoch_02", "created_at": "2026-08-12T09:16:31.980+05:30", "step": 782},
    "epoch_03": {"checkpoint_name": "epoch_03", "created_at": "2026-08-12T09:17:46.210+05:30", "step": 1173},
}

#: The Run object Express emits for a run whose metadata carries every optional field, in
#: the order `run.service.js:70-96` builds it. Order is asserted because the port was
#: written by reading that literal, and a key appearing somewhere else means it was
#: rebuilt from memory rather than from the source.
RUN_KEYS = (
    "_id", "name", "experimentId", "experimentName", "status", "description", "tags",
    "duration", "durationFormatted", "createdAt", "startTime", "endTime", "state",
    "platform", "pythonVersion", "workingDirectory", "artifacts", "artifactsCount",
    "parameters", "metrics", "metricsHistory", "systemMetrics", "checkpoints", "config",
    "summary",
)

#: The keys `JSON.stringify` drops from that object when the fields behind them are
#: absent, leaving Express with 20. This port emits all 25 with `null` in the gaps.
NULLABLE_RUN_KEYS = ("endTime", "state", "platform", "pythonVersion", "workingDirectory")


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8", newline="\n"
    )


def build_worked_example(root: Path) -> None:
    """Write the section 5 tree. Its expected output is stated in the contract."""
    run = root / PROJECT / RUN
    _write_json(run / "metadata.json", METADATA)
    _write_json(run / "config.json", CONFIG)
    _write_json(run / "summary.json", SUMMARY)
    _write_json(run / "system_metrics.json", SYSTEM_SAMPLES)
    _write_jsonl(run / "metrics.jsonl", METRIC_ROWS)
    _write_jsonl(run / "artifacts.jsonl", ARTIFACT_LINES)
    for name, body in CHECKPOINTS.items():
        _write_json(run / "checkpoints" / f"{name}.json", body)
    # A loose file whose name matches no artifact record, which section 3.6 synthesizes a
    # fourth, placeholder artifact for. Without it `artifactsCount` is 3 and the dedup
    # rule goes untested.
    (run / "artifacts").mkdir(parents=True, exist_ok=True)
    (run / "artifacts" / "confusion_matrix.png").write_bytes(b"\x89PNG\r\n\x1a\n")


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    build_worked_example(tmp_path)
    return tmp_path


@pytest.fixture()
def client(root: Path) -> TestClient:
    # An absent bundle: `/` must answer for the API, not for the UI, and whether a bundle
    # has been built is not this file's business.
    return TestClient(create_app(Storage(root), static_dir=root / "no-bundle"))


# --------------------------------------------------------------------------------------
# GET /api/run/{id} — the payload the run detail page is built from
# --------------------------------------------------------------------------------------


def test_run_has_the_express_key_set_in_the_express_order(client: TestClient) -> None:
    body = client.get(f"/api/run/{RUN}").json()
    assert tuple(body) == RUN_KEYS
    assert len(body) == 25


def test_run_scalars_match_the_contract(client: TestClient) -> None:
    """Every value here is stated in the section 5 verification table."""
    body = client.get(f"/api/run/{RUN}").json()
    assert body["_id"] == RUN
    assert body["status"] == "completed"
    assert body["state"] == "completed"
    assert body["duration"] == 231.4
    assert body["durationFormatted"] == "3m 51s"
    assert body["createdAt"] == body["startTime"] == METADATA["created_at"]
    assert body["endTime"] == SUMMARY["end_time"]
    assert body["tags"] == ["baseline", "churn", "mlp"]
    assert body["experimentId"] == body["experimentName"] == PROJECT
    # `notes` is empty in both files, so the display name falls all the way through —
    # and `metadata.name` is deliberately not consulted on this route.
    assert body["name"] == f"Run {RUN}"
    assert body["description"] == ""


def test_duration_is_a_number_here_and_a_string_on_the_experiment_route(
    client: TestClient,
) -> None:
    """One value, two types, two pages. Inherited, and load-bearing for both callers."""
    detail = client.get(f"/api/run/{RUN}").json()
    row = client.get(f"/api/experiment/{PROJECT}/runs").json()[0]
    assert isinstance(detail["duration"], float) and detail["duration"] == 231.4
    assert isinstance(row["duration"], str) and row["duration"] == "3m 51s"


def test_parameters_are_camel_cased_top_level_scalars(client: TestClient) -> None:
    parameters = client.get(f"/api/run/{RUN}").json()["parameters"]
    assert set(parameters) == {
        "learningRate", "batchSize", "epochs", "optimizer", "weightDecay", "hiddenSizes",
        "dropout", "dataset", "seed", "device", "mixedPrecision", "earlyStopping",
    }
    assert parameters["mixedPrecision"] is True
    assert parameters["earlyStopping"] is None
    assert parameters["hiddenSizes"] == "256,256"


def test_metrics_are_flattened_to_five_keys_per_metric(client: TestClient) -> None:
    """`latest` under the bare name, the four stats as sibling keys — not nested."""
    metrics = client.get(f"/api/run/{RUN}").json()["metrics"]
    assert len(metrics) == 20
    assert metrics["valLoss"] == 0.8817
    assert metrics["valLossMean"] == 1.1098
    assert metrics["valLossMax"] == 1.4022
    assert metrics["valLossMin"] == 0.8817
    assert metrics["valLossStddev"] == 0.2173
    assert all(not isinstance(value, dict) for value in metrics.values())


def test_artifacts_count_includes_the_synthesized_placeholder(client: TestClient) -> None:
    body = client.get(f"/api/run/{RUN}").json()
    assert body["artifactsCount"] == 4
    names = [a["name"] for a in body["artifacts"]]
    # The dedup is exact-match on name, so `confusion_matrix.png` does not match the
    # record named `confusion_matrix` and both appear.
    assert "confusion_matrix" in names and "confusion_matrix.png" in names
    placeholder = next(a for a in body["artifacts"] if a["name"] == "confusion_matrix.png")
    assert placeholder["type"] == "unknown"
    assert placeholder["version"] == "latest"
    assert placeholder["metadata"] == {}


def test_artifact_metadata_is_passed_through_untransformed(client: TestClient) -> None:
    """The one place a key is camelCase on disk, and the one place nothing is renamed."""
    artifacts = client.get(f"/api/run/{RUN}/artifacts").json()
    matrix = next(a for a in artifacts if a["name"] == "confusion_matrix")
    assert matrix["_id"] == "confusion_matrix_1"
    assert "f1Score" in matrix["metadata"]
    assert "f1_score" not in matrix["metadata"]
    assert matrix["metadata"]["matrix"] == [[812, 74], [63, 251]]


def test_checkpoints_are_newest_first(client: TestClient) -> None:
    checkpoints = client.get(f"/api/run/{RUN}/checkpoints").json()
    assert [c["name"] for c in checkpoints] == ["epoch_03", "epoch_02"]
    assert set(checkpoints[0]) == {"name", "path", "createdAt", "step", "size"}


def test_system_metrics_are_returned_verbatim(client: TestClient) -> None:
    """Shape A is an array. The backend never inspects this file and neither does this."""
    assert client.get(f"/api/run/{RUN}/system-metrics").json() == SYSTEM_SAMPLES


# --------------------------------------------------------------------------------------
# Metrics: four shapes out of one file
# --------------------------------------------------------------------------------------


def test_metrics_pivot_is_one_series_per_metric(client: TestClient) -> None:
    series = client.get(f"/api/run/{RUN}/metrics").json()
    assert {s["name"] for s in series} == {"loss", "accuracy", "val_loss", "val_accuracy"}
    loss = next(s for s in series if s["name"] == "loss")
    assert len(loss["data"]) == 6
    assert set(loss["data"][0]) == {"step", "value", "timestamp"}
    # Sparse logging: the validation series carry three points, not six.
    assert len(next(s for s in series if s["name"] == "val_loss")["data"]) == 3


def test_timeseries_filtered_and_unfiltered_are_different_shapes(client: TestClient) -> None:
    filtered = client.get(f"/api/run/{RUN}/metrics/timeseries?metric=val_loss").json()
    assert len(filtered) == 3
    assert set(filtered[0]) == {"timestamp", "absolute_timestamp", "step", "value"}

    raw = client.get(f"/api/run/{RUN}/metrics/timeseries").json()
    assert raw == METRIC_ROWS  # the wide rows verbatim, no pivot and no filter


def test_metrics_history_echoes_the_whole_file(client: TestClient) -> None:
    assert client.get(f"/api/run/{RUN}").json()["metricsHistory"] == METRIC_ROWS


# --------------------------------------------------------------------------------------
# Experiment aggregation
# --------------------------------------------------------------------------------------


def test_dashboard_wraps_and_the_others_do_not(client: TestClient) -> None:
    """The one endpoint with an envelope. Everything else returns the bare payload."""
    dashboard = client.get("/api/dashboard").json()
    assert set(dashboard) == {"success", "message", "data"}
    assert dashboard["success"] is True
    assert dashboard["data"] == client.get("/api/experiment/all").json()


def test_experiment_stats_match_the_contract(client: TestClient) -> None:
    experiment = client.get(f"/api/experiment/{PROJECT}").json()
    assert experiment["_id"] == experiment["name"] == PROJECT
    # No run has non-empty notes, so the description falls back to the literal.
    assert experiment["description"] == f"Experiment: {PROJECT}"
    assert experiment["tags"] == ["baseline", "churn", "mlp"]
    assert experiment["stats"] == {
        "totalRuns": 1,
        "completedRuns": 1,
        "failedRuns": 0,
        "runningRuns": 0,
        "successRate": "100%",
        "avgDuration": "3m 51s",
        "lastRun": "2026-08-12T03:44:03.482Z",
    }
    assert experiment["activityTimeline"] == []


def test_the_same_run_is_named_two_things_on_two_pages(client: TestClient) -> None:
    """A defect the contract records rather than fixes: the dashboard reads
    `metadata.name` and the run page derives a name from `notes`."""
    on_dashboard = client.get(f"/api/experiment/{PROJECT}").json()["runs"][0]["name"]
    on_run_page = client.get(f"/api/run/{RUN}").json()["name"]
    assert on_dashboard == "mlp-2x256-lr3e-4"
    assert on_run_page == f"Run {RUN}"
    assert on_dashboard != on_run_page


def test_experiment_runs_carry_latest_only_without_stat_suffixes(client: TestClient) -> None:
    row = client.get(f"/api/experiment/{PROJECT}/runs").json()[0]
    assert row["metrics"] == {
        "loss": 0.7402, "accuracy": 0.7418, "valLoss": 0.8817, "valAccuracy": 0.6952
    }
    # The five names the comparison table hardcodes.
    assert row["parameters"]["learningRate"] == 0.0003
    assert row["parameters"]["batchSize"] == 128
    assert row["parameters"]["epochs"] == 3


def test_experiment_runs_never_404(client: TestClient) -> None:
    """Inherited: an unknown project is an empty table, not an error."""
    response = client.get("/api/experiment/no-such-project/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_latest_endpoints_return_the_single_run_and_experiment(client: TestClient) -> None:
    assert client.get("/api/run").json()["_id"] == RUN
    assert client.get("/api/experiment").json()["_id"] == PROJECT


# --------------------------------------------------------------------------------------
# Deliberate divergence 1 — undefined becomes null
# --------------------------------------------------------------------------------------


def test_absent_metadata_fields_are_null_rather_than_missing(root: Path) -> None:
    """Express drops these five keys entirely; `JSON.stringify` omits `undefined`.

    Emitting them as `null` keeps the Run object one fixed shape whatever the run
    contains, which is what makes it typeable. Both are falsy in React, so no rendered
    output changes — but a client doing `"endTime" in run` sees a different answer, which
    is why this is pinned rather than assumed harmless.
    """
    bare = "bare_20260812T000000Z_0001"
    _write_json(
        root / "bare-proj" / bare / "metadata.json",
        {"created_at": "2026-08-12T00:00:00+00:00"},
    )
    client = TestClient(create_app(Storage(root), static_dir=root / "no-bundle"))
    body = client.get(f"/api/run/{bare}").json()

    assert tuple(body) == RUN_KEYS
    for key in NULLABLE_RUN_KEYS:
        assert key in body, f"{key} must be present even when the field is absent"
        assert body[key] is None
    # The fallbacks the contract specifies for a run with no summary.json.
    assert body["status"] == "running"
    assert body["duration"] == 0
    assert body["durationFormatted"] == "0s"
    assert body["metrics"] == {}
    assert body["tags"] == []


# --------------------------------------------------------------------------------------
# Deliberate divergence 2 — the CSV is quoted
# --------------------------------------------------------------------------------------


def test_csv_is_unquoted_when_nothing_needs_quoting(client: TestClient) -> None:
    """The common case is byte-identical to the Express output, which is what makes the
    quoting a fix rather than a format change."""
    body = client.get(f"/api/run/{RUN}/metrics/export?format=csv").text
    lines = body.split("\n")
    # Union of all keys across all lines, sorted alphabetically.
    assert lines[0] == "absolute_timestamp,accuracy,loss,step,timestamp,val_accuracy,val_loss"
    assert lines[1] == "1786506281.88,0.3122,1.9124,200,38.4,,"
    assert '"' not in body


def test_csv_quotes_a_value_containing_a_comma(root: Path) -> None:
    """Express emitted `1,a,b,,1` here — five fields for four columns, every column to
    the right of the comma shifted, in a file whose only purpose is to be opened in a
    spreadsheet."""
    run = "csv_20260812T000000Z_0002"
    _write_json(root / "csv-proj" / run / "metadata.json",
                {"created_at": "2026-08-12T00:00:00+00:00"})
    _write_jsonl(root / "csv-proj" / run / "metrics.jsonl",
                 [{"step": 1, "note": "a,b", "loss": 0.5}])
    client = TestClient(create_app(Storage(root), static_dir=root / "no-bundle"))
    body = client.get(f"/api/run/{run}/metrics/export?format=csv").text

    assert body.split("\n")[0] == "loss,note,step"
    assert body.split("\n")[1] == '0.5,"a,b",1'


def test_csv_headers_name_the_run_and_are_sanitised(client: TestClient) -> None:
    response = client.get(f"/api/run/{RUN}/metrics/export?format=csv")
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="metrics_{RUN}.csv"'
    )


# --------------------------------------------------------------------------------------
# Deliberate divergence 3 — one malformed run no longer 500s every page
# --------------------------------------------------------------------------------------


def test_a_non_array_tags_does_not_take_down_the_dashboard(root: Path) -> None:
    """`metadata.tags.forEach is not a function` — Express returned 500 for
    `/api/dashboard`, i.e. the landing page for *every* project, because one run in one
    project had a string where an array belonged."""
    _write_json(
        root / "bad-tags" / "bad_20260812T000000Z_0003" / "metadata.json",
        {"created_at": "2026-08-12T00:00:00+00:00", "tags": "not-an-array"},
    )
    client = TestClient(create_app(Storage(root), static_dir=root / "no-bundle"))

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    # The healthy project is still served, which is the whole point.
    assert {e["_id"] for e in dashboard.json()["data"]} == {PROJECT, "bad-tags"}
    broken = next(e for e in dashboard.json()["data"] if e["_id"] == "bad-tags")
    assert broken["tags"] == []


def test_a_run_with_no_created_at_does_not_take_down_the_dashboard(root: Path) -> None:
    """`new Date(undefined)` is an Invalid Date: it passes the `!lastActivity` guard, is
    never displaced by a later comparison, and then `.toISOString()` throws. One run
    missing one field returned 500 for every project's dashboard."""
    _write_json(
        root / "no-date" / "undated_20260812T000000Z_0004" / "metadata.json",
        {"tags": ["y"]},
    )
    client = TestClient(create_app(Storage(root), static_dir=root / "no-bundle"))

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert {e["_id"] for e in dashboard.json()["data"]} == {PROJECT, "no-date"}
    undated = next(e for e in dashboard.json()["data"] if e["_id"] == "no-date")
    assert undated["stats"]["totalRuns"] == 1


def test_a_non_finite_metric_costs_its_value_and_not_its_row(root: Path) -> None:
    """`JSON.parse` rejects a bare `NaN`, so Express dropped the whole line — including
    every other metric logged at that step. Here the row survives with a null in it, and
    the response stays parseable by a browser."""
    run = "nan_20260812T000000Z_0005"
    _write_json(root / "nan-proj" / run / "metadata.json",
                {"created_at": "2026-08-12T00:00:00+00:00"})
    (root / "nan-proj" / run / "metrics.jsonl").write_text(
        '{"step":1,"loss":NaN,"accuracy":0.9}\n{"step":2,"loss":0.4,"accuracy":0.95}\n',
        encoding="utf-8", newline="\n",
    )
    client = TestClient(create_app(Storage(root), static_dir=root / "no-bundle"))
    response = client.get(f"/api/run/{run}/metrics/timeseries")

    assert response.status_code == 200
    assert b"NaN" not in response.content  # a browser must be able to parse this
    rows = response.json()
    assert len(rows) == 2
    assert rows[0]["loss"] is None
    assert rows[0]["accuracy"] == 0.9  # the value Express threw away with the row


# --------------------------------------------------------------------------------------
# Deliberate divergence 4 — containment is a 404, never a 500
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    ["%2e%2e", "%2e%2e%2f%2e%2e%2fsecret", "..%5c..%5cwindows", "%2fetc%2fpasswd",
     "C:%5cWindows"],
)
def test_unaddressable_names_are_404_on_every_method(
    client: TestClient, payload: str, root: Path
) -> None:
    before = sorted(p.name for p in root.iterdir())
    for response in (
        client.get(f"/api/run/{payload}"),
        client.get(f"/api/experiment/{payload}"),
        client.patch(f"/api/experiment/{payload}", json={"description": "x"}),
        client.patch(f"/api/run/{payload}/tags", json={"tags": []}),
    ):
        assert response.status_code == 404
        assert "message" in response.json()
    assert sorted(p.name for p in root.iterdir()) == before


def test_error_bodies_do_not_leak_the_storage_path(client: TestClient, root: Path) -> None:
    """Express answered a failed dashboard with `{success, message, error}` where `error`
    was the raw exception message — for a filesystem failure, an absolute server path."""
    for response in (
        client.get("/api/run/no-such-run"),
        client.get("/api/experiment/no-such-experiment"),
        client.get("/api/nope"),
    ):
        assert str(root) not in response.text
        assert "Traceback" not in response.text
        assert set(response.json()) == {"message"}


# --------------------------------------------------------------------------------------
# Write routes
# --------------------------------------------------------------------------------------


def test_tags_patch_merges_into_metadata_without_losing_keys(client: TestClient, root: Path) -> None:
    """The read-modify-write the SDK has to survive: unknown keys must round-trip."""
    response = client.patch(f"/api/run/{RUN}/tags", json={"tags": ["a", "b"]})
    assert response.status_code == 200
    assert response.json() == {"message": "Tags updated", "tags": ["a", "b"]}

    on_disk = json.loads((root / PROJECT / RUN / "metadata.json").read_text(encoding="utf-8"))
    assert on_disk["tags"] == ["a", "b"]
    assert "updated_at" in on_disk
    for key in METADATA:
        if key != "tags":
            assert on_disk[key] == METADATA[key], f"{key} was lost by the patch"


def test_a_non_array_tags_is_rejected_before_the_run_is_looked_up(client: TestClient) -> None:
    """The one piece of validation the format cannot do without: a string written here
    is what 500s the dashboard for every project."""
    for body in ({"tags": "oops"}, {}, {"tags": None}):
        response = client.patch(f"/api/run/{RUN}/tags", json=body)
        assert response.status_code == 400
        assert response.json() == {"message": "Tags must be an array"}
    # Rejected on an unknown run too, and with the same body — the check precedes lookup.
    assert client.patch("/api/run/nope/tags", json={"tags": "oops"}).status_code == 400


def test_description_patch_writes_notes_into_summary(client: TestClient, root: Path) -> None:
    response = client.patch(f"/api/run/{RUN}/description", json={"description": "a note"})
    assert response.status_code == 200
    on_disk = json.loads((root / PROJECT / RUN / "summary.json").read_text(encoding="utf-8"))
    assert on_disk["notes"] == "a note"
    assert on_disk["metrics_summary"] == SUMMARY["metrics_summary"]  # nothing else moved


def test_a_non_string_description_is_refused(client: TestClient, root: Path) -> None:
    """Express wrote it. `notes` doubles as the run's display name, so a number there
    produces a run called 42 and an object one called `[object Object]`."""
    for path in (f"/api/run/{RUN}/description", f"/api/experiment/{PROJECT}"):
        response = client.patch(path, json={"description": 42})
        assert response.status_code == 400
        assert response.json() == {"message": "Description must be a string"}
    unchanged = json.loads((root / PROJECT / RUN / "summary.json").read_text(encoding="utf-8"))
    assert unchanged["notes"] == ""


def test_patching_an_unknown_experiment_creates_nothing(client: TestClient, root: Path) -> None:
    """Express's own 404 branch was unreachable: `writeJSON` mkdir'd the chain first, so
    patching a nonexistent experiment silently created it."""
    response = client.patch("/api/experiment/invented", json={"description": "x"})
    assert response.status_code == 404
    assert not (root / "invented").exists()


# --------------------------------------------------------------------------------------
# The live diff against the Express server
# --------------------------------------------------------------------------------------


def _node_available() -> bool:
    return bool(shutil.which("node")) and (ML_BACKEND / "node_modules").is_dir()


requires_express = pytest.mark.skipif(
    not _node_available(),
    reason="needs Node and ml_backend/node_modules; the assertions above stand in for it",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def express(root: Path) -> Iterator[str]:
    """The real Express backend, on the same storage tree, on a free port."""
    port = _free_port()
    env = {**os.environ, "EXPERIMENT_STORAGE_PATH": str(root), "PORT": str(port),
           "HOST": "127.0.0.1"}
    process = subprocess.Popen(
        ["node", "index.js"], cwd=ML_BACKEND, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if process.poll() is not None:
                pytest.fail(f"express exited: {process.stdout.read() if process.stdout else ''}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("express did not start")
        yield base
    finally:
        process.kill()
        process.wait(timeout=15)


def _structural_diff(js: Any, py: Any, path: str = "") -> list[str]:
    """Same keys, same nesting, same types, same values — reporting every divergence.

    Two exemptions, both documented in the contract: the placeholder artifact's
    `createdAt` is `new Date()` at request time and differs between any two calls, and a
    key Express omits because its value is `undefined` is emitted here as `null`.
    """
    out: list[str] = []
    if isinstance(js, dict) and isinstance(py, dict):
        for key in js:
            if key not in py:
                out.append(f"{path}/{key}: missing from the port")
        for key in py:
            if key not in js and py[key] is not None:
                out.append(f"{path}/{key}: extra in the port ({py[key]!r})")
        for key in js:
            if key in py:
                out += _structural_diff(js[key], py[key], f"{path}/{key}")
        return out
    if isinstance(js, list) and isinstance(py, list):
        if len(js) != len(py):
            out.append(f"{path}: length {len(js)} vs {len(py)}")
        for index, (a, b) in enumerate(zip(js, py, strict=False)):
            out += _structural_diff(a, b, f"{path}[{index}]")
        return out
    if type(js) is not type(py) and not (
        isinstance(js, (int, float)) and isinstance(py, (int, float))
    ):
        out.append(f"{path}: type {type(js).__name__} vs {type(py).__name__}")
    elif js != py and not path.endswith("createdAt"):
        out.append(f"{path}: {js!r} vs {py!r}")
    return out


READ_ROUTES = [
    "/api/dashboard",
    "/api/experiment/all",
    f"/api/experiment/{PROJECT}",
    f"/api/experiment/{PROJECT}/runs",
    "/api/experiment",
    f"/api/run/{RUN}",
    "/api/run",
    f"/api/run/{RUN}/metrics",
    f"/api/run/{RUN}/metrics/timeseries",
    f"/api/run/{RUN}/metrics/timeseries?metric=val_loss",
    f"/api/run/{RUN}/metrics/export?format=json",
    f"/api/run/{RUN}/system-metrics",
    f"/api/run/{RUN}/checkpoints",
    f"/api/run/{RUN}/artifacts",
    "/api/run/no-such-run",
    "/api/experiment/no-such-experiment",
]


@requires_express
@pytest.mark.parametrize("route", READ_ROUTES)
def test_read_route_matches_the_express_backend(
    client: TestClient, express: str, route: str
) -> None:
    """The assertion the rest of this file is a stand-in for: one storage tree, two
    servers, one diff."""
    import httpx

    reference = httpx.get(express + route, timeout=30)
    ported = client.get(route)

    assert ported.status_code == reference.status_code, route
    divergences = _structural_diff(reference.json(), ported.json())
    assert not divergences, f"{route}:\n  " + "\n  ".join(divergences)


@requires_express
def test_csv_export_is_byte_identical_when_nothing_needs_quoting(
    client: TestClient, express: str
) -> None:
    import httpx

    route = f"/api/run/{RUN}/metrics/export?format=csv"
    assert client.get(route).text == httpx.get(express + route, timeout=30).text
