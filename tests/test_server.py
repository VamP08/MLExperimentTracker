"""HTTP API tests.

These pin the shapes the React frontend uses, including some known quirks (a route that
never 404s, duration typed differently per route, two names for one run). Also covers
traversal 404s, path-free error bodies, CSV headers and the no-bundle message.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mlexperimenttracker.server.app import create_app
from mlexperimenttracker.storage import Storage

# --------------------------------------------------------------------------------------
# Fixture tree: two projects, four run dirs, one unreadable
# --------------------------------------------------------------------------------------

PROJECT_A = "churn-mlp"
PROJECT_B = "vision-cnn"
RUN_DONE = "churn-mlp_20260812T091403Z_7f3a"
RUN_LIVE = "churn-mlp_20260812T110000Z_1c2d"
RUN_BROKEN = "churn-mlp_20260812T120000Z_dead"
RUN_FAILED = "vision-cnn_20260811T090000Z_4b5e"

METRIC_ROWS = [
    {"step": 200, "timestamp": 38.4, "absolute_timestamp": 1786506281.88, "loss": 1.9124, "accuracy": 0.3122},
    {
        "step": 391,
        "timestamp": 74.1,
        "absolute_timestamp": 1786506317.58,
        "loss": 1.5507,
        "accuracy": 0.4361,
        "val_loss": 1.4022,
    },
    {"step": 600, "timestamp": 113.8, "absolute_timestamp": 1786506357.28, "loss": 1.2038, "accuracy": 0.5677},
]

SYSTEM_SAMPLES = [
    {"timestamp": 1786506243.5, "cpu_percent": 41.2, "memory_percent": 46.8, "gpu_utilization": 0.0},
    {"timestamp": 1786506273.5, "cpu_percent": 78.9, "memory_percent": 52.1, "gpu_utilization": 91.4},
]

ARTIFACT_LINES = [
    {
        "name": "confusion_matrix",
        "type": "confusion_matrix",
        "version": "1",
        "created_at": "2026-08-12T09:17:52.640+05:30",
        "file_count": 0,
        "metadata": {"labels": ["retained", "churned"], "matrix": [[812, 74], [63, 251]], "f1Score": [0.9222, 0.7856]},
    }
]


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """A storage tree covering every state the endpoints branch on."""
    done = tmp_path / PROJECT_A / RUN_DONE
    _write_json(
        done / "metadata.json",
        {
            "created_at": "2026-08-12T09:14:03.482+05:30",
            "name": "mlp-2x256-lr3e-4",
            "state": "initialized",
            "tags": ["baseline", "churn"],
            "notes": "",
            "platform": "Windows-11-10.0.26200-SP0",
            "python_version": "3.12.13",
        },
    )
    _write_json(
        done / "summary.json",
        {
            "state": "completed",
            "duration": 231.4,
            "end_time": "2026-08-12T09:17:54.882+05:30",
            "notes": "",
            "metrics_summary": {
                "loss": {"latest": 1.2038, "mean": 1.5556, "min": 1.2038, "max": 1.9124, "stddev": 0.2905},
                "accuracy": {"latest": 0.5677, "mean": 0.4386},
            },
        },
    )
    _write_json(
        done / "config.json",
        {"learning_rate": 0.0003, "batch_size": 128, "epochs": 3, "model": {"type": "mlp"}},
    )
    _write_jsonl(done / "metrics.jsonl", METRIC_ROWS)
    _write_jsonl(done / "artifacts.jsonl", ARTIFACT_LINES)
    _write_json(done / "system_metrics.json", SYSTEM_SAMPLES)
    _write_json(
        done / "checkpoints" / "epoch_02.json",
        {"checkpoint_name": "epoch_02", "created_at": "2026-08-12T09:16:31.980+05:30", "step": 391},
    )
    _write_json(
        done / "checkpoints" / "epoch_03.json",
        {"checkpoint_name": "epoch_03", "created_at": "2026-08-12T09:17:46.210+05:30", "step": 600},
    )
    (done / "artifacts").mkdir(parents=True, exist_ok=True)
    (done / "artifacts" / "confusion_matrix.png").write_bytes(b"\x89PNG\r\n")

    live = tmp_path / PROJECT_A / RUN_LIVE
    _write_json(
        live / "metadata.json",
        {"created_at": "2026-08-12T11:00:00.000+05:30", "state": "running", "tags": ["churn"]},
    )
    _write_json(live / "summary.json", {"state": "running", "duration": 61.5})

    # No metadata.json: 404 on per-run endpoints but still counted in totalRuns.
    (tmp_path / PROJECT_A / RUN_BROKEN).mkdir(parents=True, exist_ok=True)

    failed = tmp_path / PROJECT_B / RUN_FAILED
    _write_json(
        failed / "metadata.json",
        {"created_at": "2026-08-11T09:00:00.000+05:30", "state": "failed", "notes": "diverged at epoch 2"},
    )
    _write_json(failed / "summary.json", {"state": "failed", "duration": 90.0})
    return tmp_path


@pytest.fixture()
def client(root: Path) -> TestClient:
    return TestClient(create_app(Storage(root)))


@pytest.fixture()
def empty_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Storage(tmp_path / "nothing-here")))


# --------------------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------------------


def test_health_reports_the_resolved_root_and_counts(client: TestClient, root: Path) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert Path(body["storage_root"]) == root.resolve()
    assert body["projects"] == 2
    # Includes the run without metadata.json.
    assert body["runs"] == 4
    assert isinstance(body["version"], str) and body["version"]


def test_health_on_a_missing_root_is_not_an_error(empty_client: TestClient) -> None:
    body = empty_client.get("/api/health").json()
    assert body == {**body, "status": "ok", "projects": 0, "runs": 0}


# --------------------------------------------------------------------------------------
# Dashboard and experiments
# --------------------------------------------------------------------------------------


def test_dashboard_wraps_its_payload(client: TestClient) -> None:
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Dashboard data retrieved successfully"
    assert [experiment["_id"] for experiment in body["data"]] == [PROJECT_A, PROJECT_B]


def test_dashboard_stats_count_the_unreadable_run(client: TestClient) -> None:
    stats = client.get("/api/dashboard").json()["data"][0]["stats"]
    assert stats["totalRuns"] == 3
    assert stats["completedRuns"] == 1
    assert stats["runningRuns"] == 1
    # 1 of 3: the run with no metadata.json is in the denominator.
    assert stats["successRate"] == "33%"
    assert isinstance(stats["avgDuration"], str)


def test_dashboard_run_rows_use_metadata_name_and_raw_state(client: TestClient) -> None:
    runs = client.get("/api/dashboard").json()["data"][0]["runs"]
    row = next(run for run in runs if run["_id"] == RUN_DONE)
    assert row["name"] == "mlp-2x256-lr3e-4"
    assert row["status"] == "completed"


def test_experiment_all_is_a_bare_array(client: TestClient) -> None:
    body = client.get("/api/experiment/all").json()
    assert isinstance(body, list)
    assert [experiment["name"] for experiment in body] == [PROJECT_A, PROJECT_B]


def test_experiment_all_is_not_swallowed_by_the_id_route(client: TestClient) -> None:
    """/all must be declared before /{id}, or the id route would match it."""
    assert isinstance(client.get("/api/experiment/all").json(), list)
    assert client.get("/api/experiment/nope").status_code == 404


def test_latest_experiment(client: TestClient) -> None:
    body = client.get("/api/experiment").json()
    assert body["_id"] == PROJECT_A


def test_latest_experiment_404_body(empty_client: TestClient) -> None:
    response = empty_client.get("/api/experiment")
    assert response.status_code == 404
    assert response.json() == {"message": "No experiments found"}


def test_experiment_by_id(client: TestClient) -> None:
    body = client.get(f"/api/experiment/{PROJECT_B}").json()
    assert body["_id"] == PROJECT_B
    assert body["description"] == "diverged at epoch 2"
    assert body["activityTimeline"] == []


def test_experiment_by_id_404_body(client: TestClient) -> None:
    response = client.get("/api/experiment/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"message": "Experiment not found"}


def test_experiment_runs_rows(client: TestClient) -> None:
    rows = client.get(f"/api/experiment/{PROJECT_A}/runs").json()
    assert isinstance(rows, list)
    row = next(r for r in rows if r["_id"] == RUN_DONE)
    # Duration is a formatted string here, and nested config objects are dropped.
    assert row["name"] == f"Run {RUN_DONE}"
    assert row["status"] == "completed"
    assert row["duration"] == "3m 51s"
    assert row["metrics"] == {"loss": 1.2038, "accuracy": 0.5677}
    assert row["parameters"] == {"learningRate": 0.0003, "batchSize": 128, "epochs": 3}


def test_experiment_runs_never_404s(client: TestClient) -> None:
    response = client.get("/api/experiment/does-not-exist/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_patch_experiment_description(client: TestClient, root: Path) -> None:
    response = client.patch(f"/api/experiment/{PROJECT_A}", json={"description": "churn baseline"})
    assert response.status_code == 200
    assert response.json() == {"message": "Description updated", "description": "churn baseline"}
    written = json.loads((root / PROJECT_A / "project_metadata.json").read_text(encoding="utf-8"))
    assert written["description"] == "churn baseline"


def test_patch_experiment_description_404_does_not_create_a_project(
    client: TestClient, root: Path
) -> None:
    response = client.patch("/api/experiment/invented", json={"description": "x"})
    assert response.status_code == 404
    assert response.json() == {"message": "Experiment not found"}
    assert not (root / "invented").exists()


# --------------------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------------------


def test_latest_run_is_the_newest_created_at(client: TestClient) -> None:
    body = client.get("/api/run").json()
    assert body["_id"] == RUN_LIVE


def test_latest_run_404_body(empty_client: TestClient) -> None:
    response = empty_client.get("/api/run")
    assert response.status_code == 404
    assert response.json() == {"message": "No runs found"}


def test_run_detail(client: TestClient) -> None:
    body = client.get(f"/api/run/{RUN_DONE}").json()
    assert body["_id"] == RUN_DONE
    assert body["experimentId"] == PROJECT_A == body["experimentName"]
    assert body["status"] == "completed"
    assert body["state"] == "completed"
    assert body["duration"] == 231.4
    assert body["durationFormatted"] == "3m 51s"
    assert body["createdAt"] == body["startTime"] == "2026-08-12T09:14:03.482+05:30"
    assert body["endTime"] == "2026-08-12T09:17:54.882+05:30"
    assert body["tags"] == ["baseline", "churn"]
    # Empty notes, so named after its ID here (the dashboard uses metadata.name).
    assert body["name"] == f"Run {RUN_DONE}"
    assert body["pythonVersion"] == "3.12.13"
    assert len(body["metricsHistory"]) == len(METRIC_ROWS)
    assert body["artifactsCount"] == 2


def test_run_detail_flattens_metrics_and_parameters(client: TestClient) -> None:
    body = client.get(f"/api/run/{RUN_DONE}").json()
    assert body["metrics"] == {
        "loss": 1.2038,
        "lossMean": 1.5556,
        "lossMax": 1.9124,
        "lossMin": 1.2038,
        "lossStddev": 0.2905,
        "accuracy": 0.5677,
        "accuracyMean": 0.4386,
    }
    # Run detail flattens one level of nesting; the comparison table drops it.
    assert body["parameters"]["modelType"] == "mlp"


def test_run_detail_404_body(client: TestClient) -> None:
    response = client.get("/api/run/no-such-run")
    assert response.status_code == 404
    assert response.json() == {"message": "Run not found"}


def test_run_without_metadata_is_invisible(client: TestClient) -> None:
    assert client.get(f"/api/run/{RUN_BROKEN}").status_code == 404
    assert client.get(f"/api/run/{RUN_BROKEN}/metrics").status_code == 404


# --------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------


def test_metrics_pivot(client: TestClient) -> None:
    series = client.get(f"/api/run/{RUN_DONE}/metrics").json()
    assert [s["name"] for s in series] == ["loss", "accuracy", "val_loss"]
    assert series[0]["data"][0] == {"step": 200, "value": 1.9124, "timestamp": 38.4}
    # val_loss is only on one row.
    assert len(series[2]["data"]) == 1


def test_metrics_timeseries_without_a_metric_is_the_raw_rows(client: TestClient) -> None:
    rows = client.get(f"/api/run/{RUN_DONE}/metrics/timeseries").json()
    assert rows == METRIC_ROWS


def test_metrics_timeseries_with_a_metric(client: TestClient) -> None:
    rows = client.get(f"/api/run/{RUN_DONE}/metrics/timeseries", params={"metric": "val_loss"}).json()
    assert rows == [
        {"timestamp": 74.1, "absolute_timestamp": 1786506317.58, "step": 391, "value": 1.4022}
    ]


def test_metrics_timeseries_is_not_swallowed_by_the_metrics_route(client: TestClient) -> None:
    """/metrics/timeseries must be declared first or the other route matches it."""
    response = client.get(f"/api/run/{RUN_DONE}/metrics/timeseries")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert "name" not in response.json()[0]


def test_metrics_export_csv(client: TestClient) -> None:
    response = client.get(f"/api/run/{RUN_DONE}/metrics/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="metrics_{RUN_DONE}.csv"'
    )
    lines = response.text.strip().split("\n")
    assert lines[0] == "absolute_timestamp,accuracy,loss,step,timestamp,val_loss"
    assert lines[1] == "1786506281.88,0.3122,1.9124,200,38.4,"


def test_metrics_export_json(client: TestClient) -> None:
    response = client.get(f"/api/run/{RUN_DONE}/metrics/export", params={"format": "json"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="metrics_{RUN_DONE}.json"'
    )
    assert response.json() == METRIC_ROWS


def test_metrics_export_of_a_run_without_metrics_is_empty(client: TestClient) -> None:
    """200 with an empty body, not an error."""
    response = client.get(f"/api/run/{RUN_LIVE}/metrics/export")
    assert response.status_code == 200
    assert response.text == ""


def test_metrics_export_rejects_other_formats(client: TestClient) -> None:
    response = client.get(f"/api/run/{RUN_DONE}/metrics/export", params={"format": "parquet"})
    assert response.status_code == 400
    assert response.json() == {"message": "Unsupported format. Use 'csv' or 'json'"}


def test_metrics_export_of_an_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/run/nope/metrics/export").status_code == 404


# --------------------------------------------------------------------------------------
# System metrics, checkpoints, artifacts
# --------------------------------------------------------------------------------------


def test_system_metrics_are_returned_verbatim(client: TestClient) -> None:
    assert client.get(f"/api/run/{RUN_DONE}/system-metrics").json() == SYSTEM_SAMPLES


def test_system_metrics_absent_is_an_empty_object(client: TestClient) -> None:
    assert client.get(f"/api/run/{RUN_LIVE}/system-metrics").json() == {}


def test_checkpoints_are_newest_first(client: TestClient) -> None:
    checkpoints = client.get(f"/api/run/{RUN_DONE}/checkpoints").json()
    assert [c["name"] for c in checkpoints] == ["epoch_03", "epoch_02"]
    assert checkpoints[0]["step"] == 600
    assert checkpoints[0]["size"] > 0


def test_artifacts_include_a_placeholder_for_the_loose_file(client: TestClient) -> None:
    artifacts = client.get(f"/api/run/{RUN_DONE}/artifacts").json()
    assert [a["name"] for a in artifacts] == ["confusion_matrix", "confusion_matrix.png"]
    # Metadata is passed through as-is.
    assert artifacts[0]["metadata"]["f1Score"] == [0.9222, 0.7856]
    assert artifacts[0]["_id"] == "confusion_matrix_1"
    assert artifacts[1]["type"] == "unknown"


def test_artifacts_of_a_run_without_any(client: TestClient) -> None:
    assert client.get(f"/api/run/{RUN_LIVE}/artifacts").json() == []


# --------------------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------------------


def test_patch_tags(client: TestClient, root: Path) -> None:
    response = client.patch(f"/api/run/{RUN_DONE}/tags", json={"tags": ["baseline", "final"]})
    assert response.status_code == 200
    assert response.json() == {"message": "Tags updated", "tags": ["baseline", "final"]}
    metadata = json.loads((root / PROJECT_A / RUN_DONE / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["tags"] == ["baseline", "final"]
    # Other keys are kept.
    assert metadata["name"] == "mlp-2x256-lr3e-4"
    assert "updated_at" in metadata


def test_patch_tags_rejects_a_non_array(client: TestClient) -> None:
    response = client.patch(f"/api/run/{RUN_DONE}/tags", json={"tags": "baseline"})
    assert response.status_code == 400
    assert response.json() == {"message": "Tags must be an array"}


def test_patch_tags_rejects_a_missing_field(client: TestClient) -> None:
    assert client.patch(f"/api/run/{RUN_DONE}/tags", json={}).status_code == 400


def test_patch_tags_validates_before_it_resolves_the_run(client: TestClient) -> None:
    """A bad body is a 400 even when the run doesn't exist."""
    response = client.patch("/api/run/no-such-run/tags", json={"tags": "baseline"})
    assert response.status_code == 400


def test_patch_tags_404_body(client: TestClient) -> None:
    response = client.patch("/api/run/no-such-run/tags", json={"tags": []})
    assert response.status_code == 404
    assert response.json() == {"message": "Run not found"}


def test_patch_description_creates_summary_when_absent(client: TestClient, root: Path) -> None:
    response = client.patch(f"/api/run/{RUN_FAILED}/description", json={"description": "notes"})
    assert response.status_code == 200
    assert response.json() == {"message": "Description updated", "description": "notes"}
    summary = json.loads((root / PROJECT_B / RUN_FAILED / "summary.json").read_text(encoding="utf-8"))
    assert summary["notes"] == "notes"
    assert summary["state"] == "failed"


def test_patch_description_renames_the_run(client: TestClient) -> None:
    """notes is both description and display name, so editing one renames the run."""
    client.patch(f"/api/run/{RUN_DONE}/description", json={"description": "best so far"})
    body = client.get(f"/api/run/{RUN_DONE}").json()
    assert body["description"] == "best so far"
    assert body["name"] == "best so far"


def test_patch_description_404_body(client: TestClient) -> None:
    response = client.patch("/api/run/no-such-run/description", json={"description": "x"})
    assert response.status_code == 404
    assert response.json() == {"message": "Run not found"}


def test_patch_description_rejects_a_non_string(client: TestClient) -> None:
    response = client.patch(f"/api/run/{RUN_DONE}/description", json={"description": 7})
    assert response.status_code == 400
    assert response.json() == {"message": "Description must be a string"}


def test_patch_with_a_malformed_body_is_not_a_500(client: TestClient) -> None:
    response = client.patch(
        f"/api/run/{RUN_DONE}/tags", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json() == {"message": "Tags must be an array"}


# --------------------------------------------------------------------------------------
# Containment
# --------------------------------------------------------------------------------------

TRAVERSAL_IDS = [
    "%2e%2e",
    "%2e%2e%2f%2e%2e%2fsecret",
    "..%5c..%5cwindows",
    "%2fetc%2fpasswd",
    "C:%5cWindows",
]


@pytest.mark.parametrize("payload", TRAVERSAL_IDS)
def test_traversal_payloads_are_404_on_run_routes(client: TestClient, payload: str) -> None:
    for path in (
        f"/api/run/{payload}",
        f"/api/run/{payload}/metrics",
        f"/api/run/{payload}/checkpoints",
        f"/api/run/{payload}/metrics/export",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert "message" in response.json(), path


@pytest.mark.parametrize("payload", TRAVERSAL_IDS)
def test_traversal_payloads_are_404_on_experiment_routes(client: TestClient, payload: str) -> None:
    response = client.get(f"/api/experiment/{payload}")
    assert response.status_code == 404
    assert "message" in response.json()


@pytest.mark.parametrize("payload", TRAVERSAL_IDS)
def test_traversal_payloads_never_write_outside_the_root(
    client: TestClient, payload: str, root: Path
) -> None:
    response = client.patch(f"/api/experiment/{payload}", json={"description": "x"})
    assert response.status_code == 404
    assert sorted(p.name for p in root.iterdir()) == [PROJECT_A, PROJECT_B]


def test_error_bodies_carry_no_server_path(client: TestClient, root: Path) -> None:
    for response in (
        client.get("/api/run/no-such-run"),
        client.get("/api/experiment/no-such-experiment"),
        client.get("/api/nope"),
    ):
        assert str(root) not in response.text
        assert "Traceback" not in response.text


def test_unknown_api_route_answers_json(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert set(response.json()) == {"message"}


# --------------------------------------------------------------------------------------
# The UI mount
# --------------------------------------------------------------------------------------


def test_root_explains_a_missing_bundle(root: Path, tmp_path: Path) -> None:
    """Without a UI build, / returns a JSON message instead of a 500 or 404.

    Uses an explicit missing dir because server/static is gitignored and may or may not exist.
    """
    no_bundle = TestClient(create_app(Storage(root), static_dir=tmp_path / "never-built"))
    response = no_bundle.get("/")
    assert response.status_code == 200
    body = response.json()
    assert "not been built" in body["message"]
    assert body["api"] == "/api/health"


@pytest.fixture()
def ui_client(root: Path, tmp_path: Path) -> TestClient:
    """App with a fake UI bundle mounted, so the SPA fallback is tested without a build."""
    bundle = tmp_path / "bundle"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (bundle / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return TestClient(create_app(Storage(root), static_dir=bundle))


def test_bundle_is_served_at_the_root(ui_client: TestClient) -> None:
    response = ui_client.get("/")
    assert response.status_code == 200
    assert "<div id=root>" in response.text


def test_bundle_assets_are_served(ui_client: TestClient) -> None:
    assert ui_client.get("/assets/app.js").text == "console.log(1)"


def test_client_side_routes_fall_back_to_index(ui_client: TestClient) -> None:
    response = ui_client.get(f"/run/{RUN_DONE}/metrics")
    assert response.status_code == 200
    assert "<div id=root>" in response.text


def test_the_api_still_wins_over_the_bundle(ui_client: TestClient) -> None:
    assert ui_client.get("/api/health").json()["status"] == "ok"


def test_unknown_api_paths_do_not_fall_back_to_index(ui_client: TestClient) -> None:
    """A mistyped /api path should be a JSON 404, not the index page."""
    response = ui_client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_cors_allows_the_vite_dev_server(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_does_not_allow_an_arbitrary_origin(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers
