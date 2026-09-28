"""An experiment description set via PATCH survives a reload.

It used to be written to project_metadata.json but never read back. These tests cover the
round trip, the fallback to a description derived from run notes, corrupt sidecars, 404s,
and the sidecar staying out of the run listing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mlexperimenttracker.server.app import create_app
from mlexperimenttracker.storage import PROJECT_METADATA_FILE, Storage

PROJECT = "churn-mlp"
EMPTY_PROJECT = "vision-cnn"
RUN_FIRST = "churn-mlp_20260813T090000Z_0001"
RUN_SECOND = "churn-mlp_20260813T100000Z_0002"

# Derived from the first run (sorted) with non-empty notes, i.e. RUN_SECOND.
DERIVED = "swept dropout, nothing moved"


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="\n")


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """Two projects: one with runs to derive a description from, one entirely empty."""
    _write_json(
        tmp_path / PROJECT / RUN_FIRST / "metadata.json",
        {"created_at": "2026-08-13T09:00:00+00:00", "name": "mlp-2x256", "notes": ""},
    )
    _write_json(
        tmp_path / PROJECT / RUN_SECOND / "metadata.json",
        {"created_at": "2026-08-13T10:00:00+00:00", "name": "mlp-2x256-do", "notes": DERIVED},
    )
    (tmp_path / EMPTY_PROJECT).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture()
def storage(root: Path) -> Storage:
    return Storage(root)


@pytest.fixture()
def client(root: Path) -> TestClient:
    return TestClient(create_app(Storage(root), static_dir=root / "no-bundle"))


def _sidecar(root: Path, project: str = PROJECT) -> dict:
    return json.loads((root / project / PROJECT_METADATA_FILE).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# The round trip
# --------------------------------------------------------------------------------------


def test_a_patched_description_is_read_back_on_every_experiment_route(
    client: TestClient, root: Path
) -> None:
    """All three routes that show the description return the patched value."""
    typed = "Churn baseline. Do not delete — the Q3 numbers came from here."

    response = client.patch(f"/api/experiment/{PROJECT}", json={"description": typed})
    assert response.status_code == 200
    assert _sidecar(root)["description"] == typed

    assert client.get(f"/api/experiment/{PROJECT}").json()["description"] == typed
    listed = next(e for e in client.get("/api/experiment/all").json() if e["_id"] == PROJECT)
    assert listed["description"] == typed
    from_dashboard = next(
        e for e in client.get("/api/dashboard").json()["data"] if e["_id"] == PROJECT
    )
    assert from_dashboard["description"] == typed


def test_the_description_survives_a_fresh_storage_instance(root: Path) -> None:
    """A second Storage (e.g. a restarted server) reads the same value."""
    assert Storage(root).update_experiment_description(PROJECT, PROJECT, "a later note") is True

    reader = Storage(root)
    assert reader.read_experiment(PROJECT)["description"] == "a later note"
    assert next(e for e in reader.list_experiments() if e["_id"] == PROJECT)[
        "description"
    ] == "a later note"


def test_a_stored_description_outranks_the_run_derivation(storage: Storage) -> None:
    assert storage.read_experiment(PROJECT)["description"] == DERIVED
    storage.update_experiment_description(PROJECT, PROJECT, "the real description")
    assert storage.read_experiment(PROJECT)["description"] == "the real description"


def test_only_the_patched_project_changes(client: TestClient, root: Path) -> None:
    client.patch(f"/api/experiment/{PROJECT}", json={"description": "mine"})
    other = client.get(f"/api/experiment/{EMPTY_PROJECT}").json()
    assert other["description"] == f"Experiment: {EMPTY_PROJECT}"
    assert not (root / EMPTY_PROJECT / PROJECT_METADATA_FILE).exists()


def test_the_patch_preserves_keys_it_did_not_write(storage: Storage, root: Path) -> None:
    """Read-modify-write, so unknown keys from newer versions survive."""
    _write_json(
        root / PROJECT / PROJECT_METADATA_FILE,
        {"description": "old", "created_by": "someone", "format_version": "1.1"},
    )
    assert storage.update_experiment_description(PROJECT, PROJECT, "new") is True

    on_disk = _sidecar(root)
    assert on_disk["description"] == "new"
    assert on_disk["created_by"] == "someone"
    assert on_disk["format_version"] == "1.1"
    assert "updated_at" in on_disk


# --------------------------------------------------------------------------------------
# The derivation is still the fallback
# --------------------------------------------------------------------------------------


def test_the_derivation_still_applies_when_the_file_is_absent(storage: Storage) -> None:
    assert storage.read_experiment(PROJECT)["description"] == DERIVED
    assert storage.read_experiment(EMPTY_PROJECT)["description"] == f"Experiment: {EMPTY_PROJECT}"


def test_clearing_the_description_restores_the_derivation(client: TestClient) -> None:
    """An empty description falls back to the derived one."""
    client.patch(f"/api/experiment/{PROJECT}", json={"description": "temporary"})
    assert client.patch(f"/api/experiment/{PROJECT}", json={"description": ""}).status_code == 200
    assert client.get(f"/api/experiment/{PROJECT}").json()["description"] == DERIVED


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("truncated", '{"description": "half a str'),
        ("not json at all", "<html>404</html>"),
        ("empty file", ""),
        ("an array", '["description", "wrong shape"]'),
        ("a bare string", '"just a string"'),
        ("description is a number", '{"description": 42}'),
        ("description is null", '{"description": null}'),
        ("description is an object", '{"description": {"text": "nested"}}'),
        ("no description key", '{"name": "churn-mlp", "updated_at": "2026-08-13"}'),
    ],
)
def test_a_malformed_sidecar_degrades_to_the_derivation(
    client: TestClient, root: Path, label: str, payload: str
) -> None:
    """A corrupt sidecar is treated as absent instead of causing a 500."""
    (root / PROJECT / PROJECT_METADATA_FILE).write_text(payload, encoding="utf-8")

    response = client.get(f"/api/experiment/{PROJECT}")
    assert response.status_code == 200, label
    assert response.json()["description"] == DERIVED, label
    assert client.get("/api/dashboard").status_code == 200, label
    assert Storage(root).read_experiment(PROJECT)["description"] == DERIVED, label


def test_a_malformed_sidecar_is_overwritten_rather_than_merged(
    storage: Storage, root: Path
) -> None:
    """A corrupt sidecar can still be fixed from the UI."""
    (root / PROJECT / PROJECT_METADATA_FILE).write_text("not json", encoding="utf-8")
    assert storage.update_experiment_description(PROJECT, PROJECT, "repaired") is True
    assert storage.read_experiment(PROJECT)["description"] == "repaired"


# --------------------------------------------------------------------------------------
# What must not have changed
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    ["invented", "%2e%2e", "%2e%2e%2f%2e%2e%2fpwned", "..%5c..%5cwindows", "%2fetc%2fpasswd"],
)
def test_an_unaddressable_or_unknown_experiment_is_404_and_creates_nothing(
    client: TestClient, root: Path, payload: str
) -> None:
    before = sorted(p.name for p in root.iterdir())
    response = client.patch(f"/api/experiment/{payload}", json={"description": "x"})

    # Encoded separators decode into extra path segments and match no route; single-segment
    # names reach the handler. Either way it's a 404 with the usual error shape.
    assert response.status_code == 404
    assert set(response.json()) == {"message"}
    assert sorted(p.name for p in root.iterdir()) == before
    assert not list(root.rglob(PROJECT_METADATA_FILE))


def test_an_unknown_experiment_is_refused_by_the_handler(client: TestClient) -> None:
    response = client.patch("/api/experiment/invented", json={"description": "x"})
    assert response.status_code == 404
    assert response.json() == {"message": "Experiment not found"}


def test_the_sidecar_is_not_a_run(client: TestClient, storage: Storage) -> None:
    """The sidecar must not show up as a run (it would skew dashboard stats)."""
    client.patch(f"/api/experiment/{PROJECT}", json={"description": "mine"})

    assert storage.list_runs(PROJECT) == [RUN_FIRST, RUN_SECOND]
    assert storage.list_all_runs() == [(PROJECT, RUN_FIRST), (PROJECT, RUN_SECOND)]
    assert storage.find_run(PROJECT_METADATA_FILE) is None

    experiment = client.get(f"/api/experiment/{PROJECT}").json()
    assert experiment["stats"]["totalRuns"] == 2
    assert [r["_id"] for r in experiment["runs"]] == [RUN_FIRST, RUN_SECOND]
    assert len(client.get(f"/api/experiment/{PROJECT}/runs").json()) == 2


def test_a_run_description_edit_does_not_touch_the_experiment_description(
    client: TestClient, root: Path
) -> None:
    """The run description writes ``summary.notes``, which the derivation also reads."""
    client.patch(f"/api/experiment/{PROJECT}", json={"description": "the experiment"})
    assert client.patch(
        f"/api/run/{RUN_FIRST}/description", json={"description": "the run"}
    ).status_code == 200

    assert client.get(f"/api/experiment/{PROJECT}").json()["description"] == "the experiment"
    assert client.get(f"/api/run/{RUN_FIRST}").json()["description"] == "the run"
