"""Tests for the static demo's snapshot builder.

``scripts/build_demo_snapshot.py`` records the real API's real answers so the GitHub Pages
build can replay them without a server. Everything that makes that trustworthy is a
property of the file it writes, so this is where those properties are pinned:

* **It is reproducible.** Two builds with the same seed produce the same bytes. That is
  what makes a snapshot reviewable — a diff means the API changed, not that the clock did —
  and it is the claim most likely to rot, because a live SDK run reads wall clocks, invents
  a run id from one, and shells out to git.
* **It is complete.** The route set is derived from the frontend source here rather than
  written down twice, so a component that starts calling a new endpoint fails this test
  instead of failing silently in the deployed demo with an unexplained empty panel.
* **It carries a real manifest.** The Provenance tab is the most distinctive thing in this
  dashboard and the generator does not write manifests, so one run in the snapshot is a
  genuine SDK capture of a genuine commit. A manifest that stopped being captured would
  leave the demo showing its empty state everywhere, which reads as a feature nobody built.
* **It is a file a browser can parse, and small enough to send one.** Python's JSON writer
  emits ``NaN`` and ``Infinity`` for non-finite floats; ``JSON.parse`` rejects both, and a
  single metric that went non-finite would take the whole demo down at load.

The builds are run as subprocesses rather than imported: the tracked script installs a
SIGINT handler and the builder patches module globals for the length of the run, and
neither belongs in the interpreter running the suite. Two builds cost about forty seconds,
which is the price of testing the thing that ships rather than a cheaper imitation of it.
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

#: Pinned so two builds can be compared byte for byte. The build's own wall-clock stamp is
#: not a function of the seed, and pretending otherwise would be the one piece of the file
#: that lied about when it was made.
GENERATED = "2026-08-16T00:00:00+00:00"

#: The ceiling the script enforces, restated here so a change to one is a visible change.
SIZE_CEILING = 2_000_000

#: Routes the frontend calls that the snapshot deliberately does not carry, each with the
#: reason. Listed one by one so a *new* uncaptured route cannot hide among them: this is an
#: exemption list, and an exemption list that grows by accident is a coverage hole.
DELIBERATELY_ABSENT = {
    # Attachments streamed from disk. A static build has no server to attach anything from,
    # so the demo disables the two download controls rather than record a response for them.
    "/api/run/{}/logs/download": "download",
    "/api/run/{}/patch": "download",
    # Writes. There is nothing behind the page to write to, and a recorded response would
    # be a saved edit that vanishes on reload — worse than a control that says it is off.
    "/api/run/{}/tags": "write",
    "/api/run/{}/description": "write",
}

#: Every test here rides on two builds in two subprocesses, so the whole module is slow by
#: the marker's definition and is deselected with ``-m 'not slow'`` along with the rest.
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
    """Two builds, same arguments, same scratch directory, one after the other.

    The scratch directory is shared on purpose: the paths inside it are recorded in the
    manifest — a manifest names the repository it was captured in — so two builds under
    two different roots would differ for a reason that has nothing to do with determinism.
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
    """``capturedAt`` is the only field the seed does not determine, which is why the build
    accepts it as an argument — and why a caller that does not pass one gets the real time
    rather than a convenient constant."""
    assert snapshot["capturedAt"] == GENERATED


# --------------------------------------------------------------------------------------
# Completeness
# --------------------------------------------------------------------------------------


def frontend_routes() -> set[str]:
    """Every ``/api`` path the dashboard's source actually requests, as templates.

    Read out of the frontend rather than listed here, because a list here would be a second
    copy of the truth and would go stale the first time a component gained an endpoint.
    Comment lines are dropped before matching: several of them quote ``/api/runs/...``,
    the pluralised path that was never a route (GAPS B9), and a scanner that believed its
    own documentation would demand a capture of a URL nothing calls.
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
    """Reduce a path to its template: ids and query strings out, ``{}`` in.

    Applied to both sides of the comparison, so a captured
    ``/api/run/cifar10-cnn_.../metrics`` and a source template ``/api/run/${runId}/metrics``
    meet at ``/api/run/{}/metrics``.
    """
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
    """The exemption list is only honest while it is exhaustive: a route that quietly
    starts being captured should be removed from it rather than left as cover."""
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
    """The captured body, whichever key carries it.

    Defined here rather than imported from the builder on purpose: these tests assert the
    shape of the file the frontend parses, and a helper borrowed from the code that writes
    it would agree with that code by construction even if both had drifted from the
    contract in ``ml_frontend/src/lib/demoData.ts``.
    """
    return entry["text"] if "text" in entry else entry["json"]


@requires_git
def test_the_file_matches_the_contract_the_frontend_parses(snapshot: dict) -> None:
    """``lib/demoData.ts`` reads ``capturedAt``, ``source`` and ``routes``, and each entry
    as ``json`` or ``text`` but never both. This is the seam between the two halves of the
    demo, and nothing else in either test suite would notice if one half moved: the
    frontend answers a mis-shaped entry with a 404 rather than an error, so the demo would
    deploy as an empty dashboard captioned as a snapshot of real runs."""
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
    """``json.load`` accepts ``NaN`` and ``Infinity``; the browser does not. A non-finite
    metric reaching the file is therefore invisible to every check but this one."""
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
    """It is the one route that does not answer JSON, and the Metrics tab reads it as a
    string. Stored as anything else — a parsed table, a list of rows — the demo would have
    to reassemble a file the server sent whole."""
    exports = [
        entry for path, entry in routes.items() if path.endswith("/metrics/export?format=csv")
    ]
    assert exports, "no CSV export was captured"
    for entry in exports:
        assert entry["status"] == 200
        assert isinstance(entry["text"], str)
        assert entry["text"].splitlines()[0].startswith("absolute_timestamp,")


# --------------------------------------------------------------------------------------
# The manifest, and the runs that honestly have none
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
        # A manifest with no dataset is legal and far less interesting: the content hash is
        # the part that makes the tab evidence rather than metadata.
        assert manifest["datasets"], path
        assert re.fullmatch(r"[0-9a-f]{64}", str(manifest["datasets"][0]["sha256"])), path
        assert manifest["python"]["version"], path
        assert isinstance(manifest["packages"], dict) and manifest["packages"], path

        report = routes[path.replace("/provenance", "/verify")]
        assert report["status"] == 200, path
        assert {check["status"] for check in body_of(report)["checks"]} <= {"ok", "warning"}


@requires_git
def test_the_runs_without_a_manifest_record_their_404s(routes: dict[str, dict]) -> None:
    """The generated runs have no provenance, and the demo should show that rather than
    hide it: the empty state is a real state, and recording the 404 is what lets the static
    build reproduce it instead of spinning."""
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
    """Every visitor downloads this. The ceiling is a budget, not a limit of the format —
    exceed it and the fix is fewer runs or shorter histories, not a bigger number here."""
    size = builds[0].stat().st_size
    assert size <= SIZE_CEILING, (
        f"the snapshot is {size} bytes, over the {SIZE_CEILING} byte budget — "
        "reduce --runs or the metric history rather than shipping a slow page"
    )
