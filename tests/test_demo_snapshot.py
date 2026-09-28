"""Tests for scripts/build_demo_snapshot.py.

Checks the snapshot is reproducible, covers every route the frontend calls, includes a real
provenance manifest, is strict JSON a browser can parse, and stays under the size ceiling.
Builds run as subprocesses because the builder patches module globals (about 40s for two).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "build_demo_snapshot.py"
FRONTEND_SRC = REPO_ROOT / "ml_frontend" / "src"

# Pinned so two builds can be compared byte for byte.
GENERATED = "2026-08-16T00:00:00+00:00"

# Same ceiling as the script, restated so a change to it shows up here.
SIZE_CEILING = 2_000_000

# Frontend routes the snapshot skips on purpose. Listed one by one so a new uncaptured
# route can't slip in.
DELIBERATELY_ABSENT = {
    # File downloads; no server in a static build, so the demo disables them.
    "/api/run/{}/logs/download": "download",
    "/api/run/{}/patch": "download",
    # Writes; nothing to write to, so the demo disables them.
    "/api/run/{}/tags": "write",
    "/api/run/{}/description": "write",
}

# Every test depends on two subprocess builds.
pytestmark = pytest.mark.slow

requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="the snapshot's provenance run needs a real git repository",
)


# --------------------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------------------


def build(out: Path, work: Path) -> Path:
    """Run the builder exactly as the deploy does, and return the file it wrote."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out",
            str(out),
            "--work",
            str(work),
            "--generated",
            GENERATED,
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert result.returncode == 0, f"build failed:\n{result.stdout}\n{result.stderr}"
    assert out.exists(), f"build reported success but wrote nothing:\n{result.stdout}"
    return out


@pytest.fixture(scope="module")
def builds(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Two builds with the same arguments and scratch dir.

    Same scratch dir because the manifest records paths inside it.
    """
    root = tmp_path_factory.mktemp("snapshot")
    work = root / "work"
    return (
        build(root / "first.json", work),
        build(root / "second.json", work),
    )


@pytest.fixture(scope="module")
def snapshot(builds: tuple[Path, Path]) -> dict:
    return json.loads(builds[0].read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def routes(snapshot: dict) -> dict[str, dict]:
    return snapshot["routes"]


# --------------------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------------------


@requires_git
def test_two_builds_with_the_same_seed_are_byte_identical(builds: tuple[Path, Path]) -> None:
    first, second = builds
    assert first.read_bytes() == second.read_bytes()


@requires_git
def test_the_stamp_is_recorded_rather_than_derived(snapshot: dict) -> None:
    """``capturedAt`` comes from ``--generated``, not from the seed."""
    assert snapshot["capturedAt"] == GENERATED


# --------------------------------------------------------------------------------------
# Completeness
# --------------------------------------------------------------------------------------


def frontend_routes() -> set[str]:
    """Every ``/api`` path the frontend source requests, as templates.

    Comment lines are skipped: some mention ``/api/runs/...``, which was never a real route.
    """
    pattern = re.compile(r"""["'`](/api/[^"'`\s]*)["'`]""")
    found: set[str] = set()
    for source in [*FRONTEND_SRC.rglob("*.ts"), *FRONTEND_SRC.rglob("*.tsx")]:
        for line in source.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            for raw in pattern.findall(stripped):
                found.add(normalise(raw))
    return found


def normalise(path: str) -> str:
    """Reduce a path to a template: ids become ``{}``, query strings are dropped."""
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = path.split("?", 1)[0].rstrip("/")
    parts = path.split("/")
    # parts[0] is empty, parts[1] is "api", parts[2] is the collection.
    if len(parts) > 3 and parts[2] in {"run", "experiment"} and parts[3] != "all":
        parts[3] = "{}"
    return "/".join(parts)


@requires_git
def test_the_capture_covers_every_route_the_frontend_calls(routes: dict[str, dict]) -> None:
    captured = {normalise(path) for path in routes}
    wanted = frontend_routes() - set(DELIBERATELY_ABSENT)

    assert wanted, "found no /api calls in the frontend source — the scanner is broken"
    missing = sorted(wanted - captured)
    assert not missing, (
        f"the demo would answer nothing for {missing}. Add them to the crawl in "
        "scripts/build_demo_snapshot.py, or to DELIBERATELY_ABSENT with the reason."
    )


@requires_git
def test_the_absent_routes_are_absent_on_purpose(routes: dict[str, dict]) -> None:
    """A route that starts being captured should come off the exemption list."""
    captured = {normalise(path) for path in routes}
    assert not (captured & set(DELIBERATELY_ABSENT))


@requires_git
def test_the_crawl_reaches_every_run_and_every_experiment(routes: dict[str, dict]) -> None:
    experiments = body_of(routes["/api/experiment/all"])
    assert len(experiments) >= 2

    for experiment in experiments:
        experiment_id = experiment["_id"]
        assert f"/api/experiment/{experiment_id}" in routes
        runs = body_of(routes[f"/api/experiment/{experiment_id}/runs"])
        assert runs, f"experiment {experiment_id} contributed no runs"
        for run in runs:
            assert f"/api/run/{run['_id']}" in routes
            assert f"/api/run/{run['_id']}/metrics" in routes


# --------------------------------------------------------------------------------------
# What the bodies are
# --------------------------------------------------------------------------------------


def body_of(entry: dict) -> object:
    """The captured body. Not imported from the builder, so the tests check it independently."""
    return entry["text"] if "text" in entry else entry["json"]


@requires_git
def test_the_file_matches_the_contract_the_frontend_parses(snapshot: dict) -> None:
    """Matches what lib/demoData.ts reads: each entry has ``json`` or ``text``, never both.

    The frontend treats a bad entry as a 404, so nothing else would catch drift.
    """
    assert isinstance(snapshot["capturedAt"], str) and snapshot["capturedAt"]
    assert isinstance(snapshot["source"], str) and snapshot["source"]
    assert snapshot["source"] != "not captured yet"
    assert set(snapshot) == {"capturedAt", "source", "routes"}

    for path, entry in snapshot["routes"].items():
        assert ("json" in entry) ^ ("text" in entry), path
        assert set(entry) <= {"status", "json", "text", "contentType"}, path
        if "text" in entry:
            assert isinstance(entry["text"], str), path
            assert isinstance(entry["contentType"], str) and entry["contentType"], path


def reject_constant(token: str) -> object:
    raise AssertionError(
        f"the snapshot contains the JSON literal {token}, which JSON.parse rejects — "
        "the demo would fail to load at all"
    )


@requires_git
def test_the_file_is_json_a_browser_would_accept(builds: tuple[Path, Path]) -> None:
    """No ``NaN``/``Infinity``: Python's json accepts them, ``JSON.parse`` doesn't."""
    json.loads(builds[0].read_text(encoding="utf-8"), parse_constant=reject_constant)


@requires_git
def test_every_captured_body_round_trips(routes: dict[str, dict]) -> None:
    for path, entry in routes.items():
        assert isinstance(entry["status"], int), path
        body = body_of(entry)
        rendered = json.dumps(body, allow_nan=False)
        assert json.loads(rendered) == body, path


@requires_git
def test_the_csv_export_is_captured_as_text(routes: dict[str, dict]) -> None:
    """The only non-JSON route; the Metrics tab reads it as a string."""
    exports = [
        entry for path, entry in routes.items() if path.endswith("/metrics/export?format=csv")
    ]
    assert exports, "no CSV export was captured"
    for entry in exports:
        assert entry["status"] == 200
        assert isinstance(entry["text"], str)
        assert entry["text"].splitlines()[0].startswith("absolute_timestamp,")


# --------------------------------------------------------------------------------------
# Provenance manifests
# --------------------------------------------------------------------------------------


@requires_git
def test_at_least_one_run_carries_a_real_provenance_manifest(routes: dict[str, dict]) -> None:
    captured = [
        (path, body_of(entry))
        for path, entry in routes.items()
        if path.endswith("/provenance") and entry["status"] == 200
    ]
    assert captured, (
        "no run in the snapshot has a manifest — the Provenance tab is the most "
        "distinctive thing in this dashboard and the demo would show only its empty state"
    )

    for path, manifest in captured:
        git = manifest["git"]
        assert git["available"] is True, path
        assert re.fullmatch(r"[0-9a-f]{40}", str(git["commit"])), path
        assert git["branch"] == "main", path
        # No dataset is legal, but the demo should show a dataset hash.
        assert manifest["datasets"], path
        assert re.fullmatch(r"[0-9a-f]{64}", str(manifest["datasets"][0]["sha256"])), path
        assert manifest["python"]["version"], path
        assert isinstance(manifest["packages"], dict) and manifest["packages"], path

        report = routes[path.replace("/provenance", "/verify")]
        assert report["status"] == 200, path
        assert {check["status"] for check in body_of(report)["checks"]} <= {"ok", "warning"}


@requires_git
def test_the_runs_without_a_manifest_record_their_404s(routes: dict[str, dict]) -> None:
    """Generated runs have no manifest; their 404s are recorded so the demo shows the empty state."""
    missing = [
        entry
        for path, entry in routes.items()
        if path.endswith("/provenance") and entry["status"] == 404
    ]
    assert missing, "every run had a manifest — the empty state is now untested"
    for entry in missing:
        assert body_of(entry) == {"message": "No provenance recorded"}


# --------------------------------------------------------------------------------------
# Size
# --------------------------------------------------------------------------------------


@requires_git
def test_the_snapshot_stays_under_the_size_ceiling(builds: tuple[Path, Path]) -> None:
    """Every visitor downloads this; fix an overrun with fewer runs, not a higher ceiling."""
    size = builds[0].stat().st_size
    assert size <= SIZE_CEILING, (
        f"the snapshot is {size} bytes, over the {SIZE_CEILING} byte budget — "
        "reduce --runs or the metric history rather than shipping a slow page"
    )
