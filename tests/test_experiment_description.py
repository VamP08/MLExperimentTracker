"""The experiment description a user types survives a reload (GAPS M3).

``PATCH /api/experiment/{id}`` has always written ``project_metadata.json``, and until
this was fixed nothing ever opened it. The edit returned 200, the page updated its own
state, and the next load quietly replaced it with the description derived from the first
run's notes. A silent revert is worse than a refusal: there is no error to report and
nothing on screen to point at.

So these tests pin the round trip rather than the write. What a PATCH leaves on disk is
only interesting if a later, unrelated reader picks it up — which is why the survival test
builds a second :class:`Storage` instead of reusing the one that did the writing.

They also pin what must *not* change. The derivation is still the answer when no
description has been set, a sidecar somebody corrupted degrades to that derivation instead
of raising, an unaddressable project name is still a 404 that creates nothing, and
``project_metadata.json`` stays invisible to the run listing — it sits beside the run
directories, and the listing returns directories only.
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

#: The description the reader derives for ``PROJECT``: the first run in sorted order whose
#: ``notes`` are non-empty. ``RUN_FIRST`` has none, so it comes from ``RUN_SECOND``.
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
    """The reload the user actually performs. Three routes render this string, and the
    dashboard is the one they land on first."""
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
    """The write is on disk, not in the writer. A second `Storage` — a restarted server,
    or the CLI — reads the same string, which is what "survives a reload" means."""
    assert Storage(root).update_experiment_description(PROJECT, PROJECT, "a later note") is True

    reader = Storage(root)
    assert reader.read_experiment(PROJECT)["description"] == "a later note"
    assert next(e for e in reader.list_experiments() if e["_id"] == PROJECT)[
        "description"
    ] == "a later note"


def test_a_stored_description_outranks_the_run_derivation(storage: Storage) -> None:
    """Both sources are present. The one the user typed wins — the derivation exists to
    have something to show, not to have the last word."""
    assert storage.read_experiment(PROJECT)["description"] == DERIVED
    storage.update_experiment_description(PROJECT, PROJECT, "the real description")
    assert storage.read_experiment(PROJECT)["description"] == "the real description"


def test_only_the_patched_project_changes(client: TestClient, root: Path) -> None:
    client.patch(f"/api/experiment/{PROJECT}", json={"description": "mine"})
    other = client.get(f"/api/experiment/{EMPTY_PROJECT}").json()
    assert other["description"] == f"Experiment: {EMPTY_PROJECT}"
    assert not (root / EMPTY_PROJECT / PROJECT_METADATA_FILE).exists()


def test_the_patch_preserves_keys_it_did_not_write(storage: Storage, root: Path) -> None:
    """Read-modify-write, for the same reason the tags patch does it: a key a later
    version adds must survive a description edit made by this one."""
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
    """An empty box asks for the default back. Rendering an experiment with no description
    at all reads as a broken page, and the sidecar stays on disk either way."""
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
    """Nothing here raises. Every read path in this module answers a corrupt file the way
    it answers an absent one, because a 500 on the experiment page would make a file no
    user knows exists take down the page they were looking at."""
    (root / PROJECT / PROJECT_METADATA_FILE).write_text(payload, encoding="utf-8")

    response = client.get(f"/api/experiment/{PROJECT}")
    assert response.status_code == 200, label
    assert response.json()["description"] == DERIVED, label
    assert client.get("/api/dashboard").status_code == 200, label
    assert Storage(root).read_experiment(PROJECT)["description"] == DERIVED, label


def test_a_malformed_sidecar_is_overwritten_rather_than_merged(
    storage: Storage, root: Path
) -> None:
    """There is nothing to merge with, so the edit still has to work: a user who corrupts
    this file must be able to fix it from the UI."""
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

    # A payload carrying an encoded separator survives routing as a real one and matches
    # no route, so it is refused before the handler; a single-segment name reaches the
    # handler and is refused there. Both are 404 in the one error shape this API uses.
    assert response.status_code == 404
    assert set(response.json()) == {"message"}
    assert sorted(p.name for p in root.iterdir()) == before
    assert not list(root.rglob(PROJECT_METADATA_FILE))


def test_an_unknown_experiment_is_refused_by_the_handler(client: TestClient) -> None:
    response = client.patch("/api/experiment/invented", json={"description": "x"})
    assert response.status_code == 404
    assert response.json() == {"message": "Experiment not found"}


def test_the_sidecar_is_not_a_run(client: TestClient, storage: Storage) -> None:
    """It is a file beside the run directories, and the listing returns directories only —
    verified rather than assumed, because a sidecar that counted as a run would depress
    every success rate on the dashboard by inventing a run with no metadata."""
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
    """Two descriptions, two files. The run one writes ``summary.notes``, which is also
    what the derivation reads — so this is the case where the two could collide."""
    client.patch(f"/api/experiment/{PROJECT}", json={"description": "the experiment"})
    assert client.patch(
        f"/api/run/{RUN_FIRST}/description", json={"description": "the run"}
    ).status_code == 200

    assert client.get(f"/api/experiment/{PROJECT}").json()["description"] == "the experiment"
    assert client.get(f"/api/run/{RUN_FIRST}").json()["description"] == "the run"
