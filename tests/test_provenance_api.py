"""The three format-1.1 endpoints: the manifest, the patch and the verification report.

Every assertion runs against a real storage tree under ``tmp_path`` rather than a mocked
Storage, because the property being tested is that these routes see what a writer left on
disk — including the case that dominates in practice, a run directory written before 1.1
that has neither file. Absence is the normal state here, not an error, and each route has
to say so in the one error shape this API uses.

The verification route is the one piece that cannot be pinned end to end from this file
alone: ``mlexperimenttracker.verify`` is written separately, and the route imports it
lazily so that an installation without it still serves the other twenty. The happy path
therefore skips until that module exists, while the 404 that precedes the import — no
manifest, nothing to verify against — is asserted unconditionally.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mlexperimenttracker.server.app import create_app
from mlexperimenttracker.storage import Storage

PROJECT = "churn-mlp"
RUN_WITH = "churn-mlp_20260813T101402Z_9a1b"
RUN_WITHOUT = "churn-mlp_20260812T091403Z_7f3a"

PATCH_BYTES = (
    b"diff --git a/train.py b/train.py\n"
    b"index 1a2b3c4..5d6e7f8 100644\n"
    b"--- a/train.py\n"
    b"+++ b/train.py\n"
    b"@@ -1,2 +1,2 @@\n"
    b"-LR = 1e-3\n"
    b"+LR = 3e-4\n"
)

MANIFEST = {
    "captured_at": "2026-08-13T10:14:02.113+05:30",
    "git": {
        "available": True,
        "reason": None,
        "commit": "3f9c1c4b2b1a0e5d8f4a7c6b9e2d1a0c5b8f7e6d",
        "branch": "main",
        "remote": "https://github.com/example/churn.git",
        "dirty": True,
        "untracked": ["notes.txt"],
        "untracked_truncated": False,
        "diff_file": "uncommitted.patch",
        "diff_sha256": "9f2a",
        "diff_bytes": len(PATCH_BYTES),
        "diff_truncated": False,
    },
    "python": {"version": "3.12.13", "implementation": "CPython", "executable": "python.exe"},
    "platform": {"system": "Windows", "release": "11", "machine": "AMD64", "processor": "x86"},
    "packages": {"numpy": "2.1.0", "torch": "2.4.0"},
    "hardware": {
        "cpu_count": 16,
        "gpus": [
            {
                "name": "NVIDIA GeForce RTX 3050",
                "memory_total_mb": 8188,
                "driver_version": "551.86",
                "cuda_version": "12.4",
            }
        ],
    },
    "environment": {"CUDA_VISIBLE_DEVICES": "0"},
    "datasets": [
        {
            "path": "data/train.csv",
            "sha256": "b1946ac92492d2347c6235b4d2611184",
            "digest": "b1946ac92492d2347c6235b4d2611184",
            "bytes": 1234,
            "files": 1,
            "algorithm": "sha256",
            "hashed_at": "2026-08-13T10:14:02.010+05:30",
        }
    ],
    "command": {"argv": ["train.py", "--lr", "3e-4"], "cwd": "E:/work/churn"},
}

#: Every route the app registers, as the frontend and the parity suite see them. The set
#: is asserted whole rather than incrementally so that a route added by accident — or one
#: lost to a name collision inside a registrar — fails here rather than in a page that
#: stops loading. FastAPI's own ``/api/docs`` and ``/api/openapi.json`` are excluded: they
#: come from the framework's configuration, not from this application's handlers.
EXPECTED_ROUTES = {
    ("/api/health", "GET"),
    ("/api/dashboard", "GET"),
    ("/api/experiment/all", "GET"),
    ("/api/experiment/{experiment_id}/runs", "GET"),
    ("/api/experiment/{experiment_id}", "GET"),
    ("/api/experiment/{experiment_id}", "PATCH"),
    ("/api/experiment", "GET"),
    ("/api/run/{run_id}/metrics/timeseries", "GET"),
    ("/api/run/{run_id}/metrics/export", "GET"),
    ("/api/run/{run_id}/metrics", "GET"),
    ("/api/run/{run_id}/system-metrics", "GET"),
    ("/api/run/{run_id}/checkpoints", "GET"),
    ("/api/run/{run_id}/artifacts", "GET"),
    ("/api/run/{run_id}/provenance", "GET"),
    ("/api/run/{run_id}/patch", "GET"),
    ("/api/run/{run_id}/verify", "GET"),
    ("/api/run/{run_id}", "GET"),
    ("/api/run", "GET"),
    ("/api/run/{run_id}/tags", "PATCH"),
    ("/api/run/{run_id}/description", "PATCH"),
}

#: The seventeen that existed before format 1.1. `tests/test_parity.py` diffs the read
#: half of these against the Express service, so an addition must be an addition and
#: nothing else.
PRE_1_1_ROUTES = EXPECTED_ROUTES - {
    ("/api/run/{run_id}/provenance", "GET"),
    ("/api/run/{run_id}/patch", "GET"),
    ("/api/run/{run_id}/verify", "GET"),
}


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="\n")


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """Two runs in one project: one captured under 1.1, one written before it existed."""
    captured = tmp_path / PROJECT / RUN_WITH
    _write_json(
        captured / "metadata.json",
        {"created_at": "2026-08-13T10:14:02.000+05:30", "format_version": "1.1"},
    )
    _write_json(captured / "provenance.json", MANIFEST)
    (captured / "uncommitted.patch").write_bytes(PATCH_BYTES)

    bare = tmp_path / PROJECT / RUN_WITHOUT
    _write_json(bare / "metadata.json", {"created_at": "2026-08-12T09:14:03.482+05:30"})
    return tmp_path


@pytest.fixture()
def client(root: Path) -> TestClient:
    return TestClient(create_app(Storage(root), static_dir=root / "no-bundle"))


# --------------------------------------------------------------------------------------
# The route set
# --------------------------------------------------------------------------------------


def _api_routes(app: object) -> set[tuple[str, str]]:
    framework = {"/api/docs", "/api/docs/oauth2-redirect", "/api/openapi.json"}
    found: set[tuple[str, str]] = set()
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        if not path.startswith("/api") or path in framework:
            continue
        for method in getattr(route, "methods", set()) or set():
            if method not in ("HEAD", "OPTIONS"):
                found.add((path, method))
    return found


def test_the_existing_route_set_is_unchanged(root: Path) -> None:
    """The three new routes are additive: nothing that existed before moved or vanished."""
    app = create_app(Storage(root), static_dir=root / "no-bundle")
    found = _api_routes(app)
    assert PRE_1_1_ROUTES <= found
    assert found == EXPECTED_ROUTES


# --------------------------------------------------------------------------------------
# GET /api/run/{id}/provenance
# --------------------------------------------------------------------------------------


def test_provenance_is_returned_verbatim(client: TestClient) -> None:
    """No reshaping and no camelCase transform: the manifest is the contract, and the one
    reader that matters — a later verification — compares it field by field."""
    response = client.get(f"/api/run/{RUN_WITH}/provenance")
    assert response.status_code == 200
    assert response.json() == MANIFEST


def test_provenance_404s_for_a_run_that_recorded_none(client: TestClient) -> None:
    """The common case, and the reason this is not an empty object: most run directories
    on any real disk predate format 1.1."""
    response = client.get(f"/api/run/{RUN_WITHOUT}/provenance")
    assert response.status_code == 404
    assert response.json() == {"message": "No provenance recorded"}


def test_provenance_404s_for_an_unknown_run(client: TestClient) -> None:
    response = client.get("/api/run/no-such-run/provenance")
    assert response.status_code == 404
    assert response.json() == {"message": "Run not found"}


@pytest.mark.parametrize("payload", ["%2e%2e", "%2fetc%2fpasswd", "..%5c..%5cwindows"])
def test_traversal_payloads_are_404_on_the_new_routes(client: TestClient, payload: str) -> None:
    """A URL-encoded separator survives routing as a real one, so these arrive as paths of
    the wrong length and match no route at all — 404 in JSON from the ``/api`` guard,
    rather than the 405 a file server would answer with."""
    for suffix in ("provenance", "patch", "verify"):
        response = client.get(f"/api/run/{payload}/{suffix}")
        assert response.status_code == 404
        assert set(response.json()) == {"message"}


# --------------------------------------------------------------------------------------
# GET /api/run/{id}/patch
# --------------------------------------------------------------------------------------


def test_patch_is_served_byte_for_byte_as_text(client: TestClient) -> None:
    response = client.get(f"/api/run/{RUN_WITH}/patch")
    assert response.status_code == 200
    assert response.content == PATCH_BYTES
    assert response.headers["content-type"].startswith("text/plain")


def test_patch_is_offered_as_an_attachment_with_a_sanitised_name(client: TestClient) -> None:
    """The run ID is interpolated into a header. Storage has already refused separators
    and NUL by this point, but not a quote."""
    response = client.get(f"/api/run/{RUN_WITH}/patch")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{RUN_WITH}_uncommitted.patch"'
    )


def test_patch_404s_when_no_diff_was_captured(client: TestClient) -> None:
    response = client.get(f"/api/run/{RUN_WITHOUT}/patch")
    assert response.status_code == 404
    assert response.json() == {"message": "No patch recorded"}


def test_an_emptied_patch_file_reads_as_absent(client: TestClient, root: Path) -> None:
    """The writer never produces a zero-byte patch, so an empty one is a patch somebody
    emptied — and serving it as an empty 200 would claim a clean tree that was dirty."""
    (root / PROJECT / RUN_WITH / "uncommitted.patch").write_bytes(b"")
    response = client.get(f"/api/run/{RUN_WITH}/patch")
    assert response.status_code == 404
    assert response.json() == {"message": "No patch recorded"}


def test_patch_404s_for_an_unknown_run(client: TestClient) -> None:
    response = client.get("/api/run/no-such-run/patch")
    assert response.status_code == 404
    assert response.json() == {"message": "Run not found"}


# --------------------------------------------------------------------------------------
# GET /api/run/{id}/verify
# --------------------------------------------------------------------------------------


def test_verify_404s_before_it_imports_anything(client: TestClient) -> None:
    """A run with no manifest has nothing to verify against, and answers with the same
    message the provenance route uses — the two absences are one fact."""
    response = client.get(f"/api/run/{RUN_WITHOUT}/verify")
    assert response.status_code == 404
    assert response.json() == {"message": "No provenance recorded"}


def test_verify_404s_for_an_unknown_run(client: TestClient) -> None:
    response = client.get("/api/run/no-such-run/verify")
    assert response.status_code == 404
    assert response.json() == {"message": "Run not found"}


def test_verify_returns_a_json_report(client: TestClient) -> None:
    pytest.importorskip(
        "mlexperimenttracker.verify",
        reason="the verifier is a separate module; the route imports it lazily",
    )
    response = client.get(f"/api/run/{RUN_WITH}/verify")
    assert response.status_code == 200
    assert isinstance(response.json(), (dict, list))


# --------------------------------------------------------------------------------------
# The routes these three sit beside
# --------------------------------------------------------------------------------------


def test_the_run_object_is_unaffected_by_the_two_new_files(client: TestClient) -> None:
    """A 1.0 reader handed a 1.1 directory reads it correctly. The run object here is the
    proof on the API side: neither file reaches a parser, and the artifact scan — the one
    place a stray file is picked up — looks only inside ``artifacts/``."""
    with_manifest = client.get(f"/api/run/{RUN_WITH}").json()
    without = client.get(f"/api/run/{RUN_WITHOUT}").json()
    assert tuple(with_manifest) == tuple(without)
    assert with_manifest["artifacts"] == []
    assert with_manifest["artifactsCount"] == 0
