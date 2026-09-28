"""Capture a real API's real responses into one JSON file, for the static demo build.

The dashboard is local-first: it reads a directory on your machine through a server that
runs on your machine. That is the product, and it is also the reason there is nothing to
link to. This script closes that gap without loosening the honesty the rest of the project
is held to — it does **not** write example JSON for the frontend to render. It builds a
real archive, serves it through the real FastAPI application, walks every route the
dashboard actually calls, and writes down exactly what came back, status codes included.
The static build then replays those recordings instead of calling ``fetch``.

Three things are worth stating plainly, because each of them is a place where a demo
usually starts lying.

*The data is generated, not invented.* Eight runs come from
:func:`mlexperimenttracker.demo.generate` — the same deterministic generator behind
``mlexp demo``, whose curves are produced by a small training model rather than by
sampling noise. The ninth run is not generated at all: it is ``examples/quickstart.py``,
committed into a throwaway git repository and executed through the SDK, so the manifest on
its Provenance tab is a genuine capture of a genuine commit, with a real content hash of
the dataset the script wrote. Nothing here is hand-written into a fixture.

*The 404s are real too.* The eight generated runs carry no manifest, so their
``/provenance`` and ``/verify`` routes answer 404 — and those responses are recorded as
they are. The demo therefore shows the dashboard's empty states rather than a world where
every run happens to have everything.

*The output is reproducible.* Two builds on the same machine with the same seed produce a
byte-identical file, which is what makes the snapshot reviewable and what lets a test
assert it. Getting there takes more than a seed, because a live SDK run reads the wall
clock, invents a run id from it, and shells out to git:

- the run's clock is frozen (:class:`_FrozenClock`), the same technique ``demo.py`` uses
  with its fixed ``_BASE_TIME`` — an *input* is pinned, no captured output is edited;
- the throwaway repository is committed with fixed author and committer dates and a fixed
  identity, so its commit hash is a function of its contents;
- the work directory is a fixed path under ``build/``, so the paths that legitimately
  appear in a manifest are stable, rather than a random temp directory's.

Reproducibility stops at the machine boundary, and it has to: a provenance manifest
records the interpreter, the installed distributions, the CPU and the local timezone
offset, and a capture that produced the same bytes on a different machine would be a
capture that made them up. ``--generated`` exists for the same reason — the build's
wall-clock stamp is not a function of the seed, so a determinism check pins it.

Usage:

    python scripts/build_demo_snapshot.py

Needs ``git`` on PATH. It overwrites ``ml_frontend/src/demo/snapshot.json``, which the
frontend imports and which is tracked as an *empty placeholder* — the path cannot be
gitignored, because Rollup resolves that import while building the normal bundle too and an
absent file breaks ``npm run build`` on a fresh clone. The ~540 KiB capture this writes over
it is a build artifact: regenerate it, do not commit it. The Render build regenerates it on
the way to the deploy, and fails if the placeholder reaches the bundle.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import runpy
import shutil
import stat
import subprocess
import sys
import time as _time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

# Run against the working tree rather than an installed copy, for the same reason
# ``tests/conftest.py`` does: the snapshot must describe the API in this checkout.
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

#: Where the frontend expects it. Relative paths on the command line resolve against the
#: repository root, not the shell's cwd, so the documented invocation works from anywhere.
#: This is the path ``ml_frontend/src/lib/demoData.ts`` imports; the two move together.
DEFAULT_OUT = Path("ml_frontend/src/demo/snapshot.json")

#: One line describing the capture, shown by the demo banner. It says what actually
#: happened: the runs come from ``mlexp demo``, and the responses come from the real
#: FastAPI application exercised in-process. It deliberately does not claim an HTTP
#: server on a port, because the capture does not use one.
SOURCE = "mlexp demo runs, captured from the real FastAPI app by scripts/build_demo_snapshot.py"

#: Scratch space: the storage root, the throwaway repository, and the quickstart's output.
#: Fixed rather than temporary so the paths inside the manifest are stable — ``build/`` is
#: already gitignored.
DEFAULT_WORK = REPO_ROOT / "build" / "demo-snapshot"

#: The snapshot is downloaded by every visitor to the demo. Past this, trim runs or metric
#: history rather than ship a slow page.
SIZE_CEILING = 2_000_000

#: The experiment the tracked script runs under. Named for what it trains, so it reads as
#: a third experiment beside the generator's two rather than as scaffolding.
PROVENANCE_PROJECT = "churn-logreg"

#: Fixed git identity and dates for the throwaway repository. The commit hash is a function
#: of tree, message, identity and dates — pin all four and the hash is reproducible, which
#: is what makes the manifest reproducible.
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "MLExperimentTracker demo",
    "GIT_AUTHOR_EMAIL": "demo@example.invalid",
    "GIT_COMMITTER_NAME": "MLExperimentTracker demo",
    "GIT_COMMITTER_EMAIL": "demo@example.invalid",
    "GIT_AUTHOR_DATE": "2026-08-05T09:00:00+05:30",
    "GIT_COMMITTER_DATE": "2026-08-05T09:00:00+05:30",
}

REPO_README = """# demo-repo

A throwaway repository built by `scripts/build_demo_snapshot.py`. It exists so that one run
in the static demo carries a provenance manifest that was captured rather than written:
`quickstart.py` below is committed here, and the SDK records this commit while it runs.
"""

REPO_GITIGNORE = "__pycache__/\n*.py[cod]\n"


# --------------------------------------------------------------------------------------
# A deterministic clock for the one run that is not generated
# --------------------------------------------------------------------------------------


class _FrozenClock:
    """Stand-in for the ``time`` module, for the duration of the tracked run.

    The SDK reads two clocks and writes both: wall clock into ``created_at``, ``end_time``
    and every log record's absolute timestamp, monotonic into durations and per-step
    elapsed times. Left alone they make every build differ, which would cost the snapshot
    its reviewability for no gain — nothing in the demo depends on the run having taken the
    number of milliseconds it happened to take.

    Both clocks come off one counter that advances a fixed step per read, so they stay
    consistent with each other: the run's end time is its start time plus its duration, and
    a log record's absolute timestamp falls between the two, exactly as they do on a real
    clock. What the counter cannot do is measure anything — the elapsed time it produces is
    a function of how many times the SDK looks at the clock, not of how long the machine
    took. It lands in the same few seconds the script really takes, and that is a
    coincidence of scale rather than a measurement, which is why it is written down here.
    """

    def __init__(self, instant: datetime, step: float = 0.05) -> None:
        self._instant = instant
        self._step = step
        self._ticks = 0

    def _advance(self) -> float:
        """Seconds since the run began, one step further on than the last read."""
        value = self._ticks * self._step
        self._ticks += 1
        return value

    def monotonic(self) -> float:
        return self._advance()

    def time(self) -> float:
        return self._instant.timestamp() + self._advance()

    def wall(self) -> datetime:
        return self._instant + timedelta(seconds=self._advance())

    def __getattr__(self, name: str) -> Any:
        # Everything else — sleep above all — is the real module's.
        return getattr(_time, name)


class _SeededSecrets:
    """``secrets.token_hex`` made reproducible, for the random half of a run id.

    Only ever installed over ``run.py``'s import, and only while the demo run is being
    written. The ids it produces are not secrets and were never used as one: they are the
    four hex characters that stop two runs created in the same second from colliding.
    """

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def token_hex(self, nbytes: int = 32) -> str:
        return self._rng.randbytes(nbytes).hex()


def _frozen_datetime(clock: _FrozenClock) -> type[datetime]:
    """A ``datetime`` subclass reading ``now()`` off ``clock``. Everything else is inherited."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:  # type: ignore[override]
            moment = clock.wall()
            return moment.astimezone(tz) if tz is not None else moment.astimezone()

    return _Frozen


@contextmanager
def _pinned_clock(instant: datetime, seed: int) -> Iterator[None]:
    """Freeze every clock the SDK reads, and the run id's random suffix, then restore them.

    Scoped to the ``with`` body and to this process. The modules are patched rather than
    given an injected clock because the SDK has no seam for one, and adding a
    test-only parameter to a shipped API to make a build script tidier is the worse trade.
    """
    from mlexperimenttracker import logs as logs_module
    from mlexperimenttracker import provenance as provenance_module
    from mlexperimenttracker import run as run_module

    clock = _FrozenClock(instant)
    frozen = _frozen_datetime(clock)
    saved = (
        (run_module, "datetime", run_module.datetime),
        (run_module, "time", run_module.time),
        (run_module, "secrets", run_module.secrets),
        (logs_module, "time", logs_module.time),
        (provenance_module, "datetime", provenance_module.datetime),
    )
    run_module.datetime = frozen  # type: ignore[assignment,misc]
    run_module.time = clock  # type: ignore[assignment]
    run_module.secrets = _SeededSecrets(seed)  # type: ignore[assignment]
    logs_module.time = clock  # type: ignore[assignment]
    provenance_module.datetime = frozen  # type: ignore[assignment,misc]
    try:
        yield
    finally:
        for module, name, original in saved:
            setattr(module, name, original)


@contextmanager
def _in_directory(path: Path, argv: list[str]) -> Iterator[None]:
    """Run as if launched from ``path`` with ``argv``.

    Both are captured by the manifest — ``command.cwd`` and ``command.argv`` are what a
    replay re-runs — so both are set to what a user would have typed, rather than left as
    this script's own absolute path.
    """
    previous_cwd = Path.cwd()
    previous_argv = list(sys.argv)
    os.chdir(path)
    sys.argv = argv
    try:
        yield
    finally:
        os.chdir(previous_cwd)
        sys.argv = previous_argv


# --------------------------------------------------------------------------------------
# Building the archive
# --------------------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **GIT_IDENTITY}
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=60,
    )
    return result.stdout


def _remove_tree(path: Path) -> None:
    """Delete the work directory, including a git object store.

    Git marks every loose object read-only, and on Windows a read-only file cannot be
    unlinked — so the plain ``rmtree`` fails on the second build, which is exactly the run
    that proves reproducibility. The handler clears the bit and retries.
    """

    def force(func: Any, target: str, _exc: Any) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=force)
    else:  # pragma: no cover - the 3.10/3.11 spelling of the same hook
        shutil.rmtree(path, onerror=force)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _build_repo(repo: Path) -> str:
    """A one-commit repository holding the project's own quickstart example.

    ``core.autocrlf=false`` because Git for Windows turns it on in the system config, and a
    checkout that rewrites line endings changes the blob the manifest points at. The tree
    is left clean: a dirty tree would put a patch on the Provenance tab whose download link
    has no server to answer it in a static build.
    """
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", GIT_IDENTITY["GIT_AUTHOR_NAME"])
    _git(repo, "config", "user.email", GIT_IDENTITY["GIT_AUTHOR_EMAIL"])
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")

    source = (REPO_ROOT / "examples" / "quickstart.py").read_text(encoding="utf-8")
    _write_text(repo / "quickstart.py", source)
    _write_text(repo / "README.md", REPO_README)
    _write_text(repo / ".gitignore", REPO_GITIGNORE)

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Add the quickstart training script")
    return _git(repo, "rev-parse", "HEAD").strip()


def _newest_run_time(store: Any) -> datetime:
    """The most recent ``created_at`` in the archive, parsed from disk."""
    newest: datetime | None = None
    for project, run_id in store.list_all_runs():
        run_dir = store.run_path(project, run_id)
        if run_dir is None:
            continue
        metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        moment = datetime.fromisoformat(str(metadata["created_at"]))
        if newest is None or moment > newest:
            newest = moment
    if newest is None:
        raise RuntimeError("the generated archive contains no runs")
    return newest


def _run_tracked_script(work: Path, store: Any, seed: int) -> str:
    """Execute the committed quickstart through the SDK, and return the run id.

    In-process rather than as a subprocess: the frozen clock lives in this interpreter, and
    the script is executed by path with ``__name__`` set to ``__main__``, which is what its
    ``raise SystemExit(main())`` expects. It is the file in the repository that runs —
    ``runpy`` reads it off disk — so the commit the manifest records is the commit that
    produced the numbers.
    """
    repo = work / "demo-repo"
    output = work / "quickstart-output"
    commit = _build_repo(repo)

    # An hour and a half after the newest generated run, so this is the run the dashboard
    # opens on and the Provenance tab is the first thing a visitor sees.
    instant = _newest_run_time(store) + timedelta(minutes=90)

    argv = [
        "quickstart.py",
        "--project",
        PROVENANCE_PROJECT,
        "--storage",
        str(store.root),
        "--outdir",
        str(output),
    ]

    before = {run_id for _, run_id in store.list_all_runs()}
    with _pinned_clock(instant, seed), _in_directory(repo, argv):
        try:
            runpy.run_path("quickstart.py", run_name="__main__")
        except SystemExit as exit_code:
            if exit_code.code not in (0, None):
                raise RuntimeError(f"quickstart.py exited with {exit_code.code}") from exit_code
    created = [run_id for _, run_id in store.list_all_runs() if run_id not in before]
    if len(created) != 1:
        raise RuntimeError(f"expected exactly one new run, got {created}")

    manifest = store.read_provenance(PROVENANCE_PROJECT, created[0])
    if manifest is None:
        raise RuntimeError(
            "the tracked run wrote no provenance manifest — the demo would ship without "
            "the Provenance card, which is the thing this run exists to show"
        )
    recorded = str(manifest.get("git", {}).get("commit", ""))
    if recorded != commit:
        raise RuntimeError(f"manifest records commit {recorded!r}, repository is at {commit!r}")
    return created[0]


# --------------------------------------------------------------------------------------
# Crawling the API
# --------------------------------------------------------------------------------------


def _entry(response: Any) -> dict[str, Any]:
    """One captured response in the shape the demo adapter replays.

    ``ml_frontend/src/lib/demoData.ts`` documents the contract: ``json`` or ``text``,
    never both, with ``contentType`` carried only for the non-JSON routes so the CSV
    export replays as a CSV rather than as a string that happens to have commas in it.
    Which of the two a route gets is decided by the response's own content type, not by
    the Python type of its body — a JSON route answering with a bare string is still JSON.
    """
    content_type = response.headers.get("content-type", "")
    entry: dict[str, Any] = {"status": response.status_code}
    if "json" in content_type:
        entry["json"] = response.json()
    else:
        entry["text"] = response.text
        entry["contentType"] = content_type
    return entry


def body_of(entry: dict[str, Any]) -> Any:
    """The captured body, whichever of the two keys is carrying it."""
    return entry["text"] if "text" in entry else entry["json"]


def crawl(client: Any) -> dict[str, dict[str, Any]]:
    """Every route the frontend calls, recorded with its status.

    Four routes the frontend does use are deliberately absent, and none of them is data the
    page renders. ``/logs/download`` and ``/patch`` are ``Content-Disposition`` attachments
    served straight from disk, and a static demo has no server to attach them from; the
    ``tags`` and ``description`` PATCHes are writes, and a recorded answer to a write is a
    saved edit that disappears on reload. The demo turns those four controls off instead —
    ``tests/test_demo_snapshot.py`` holds the same list, so a fifth cannot join them
    quietly. The CSV export *is* here, because the Metrics tab reads it into the page.

    ``/logs`` is captured unpaginated. The Logs tab requests ``?limit=500&offset=0``, but
    the demo runs are far shorter than one page and a replay that has the whole array can
    serve any window of it; recording every page the UI might ask for would multiply the
    file by the number of filters.
    """
    routes: dict[str, dict[str, Any]] = {}

    def get(path: str) -> Any:
        response = client.get(path)
        routes[path] = _entry(response)
        return body_of(routes[path])

    get("/api/dashboard")
    get("/api/experiment")
    experiments = get("/api/experiment/all")

    for experiment in experiments:
        experiment_id = str(experiment["_id"])
        get(f"/api/experiment/{experiment_id}")
        runs = get(f"/api/experiment/{experiment_id}/runs")

        for run in runs:
            run_id = str(run["_id"])
            get(f"/api/run/{run_id}")
            get(f"/api/run/{run_id}/metrics")
            get(f"/api/run/{run_id}/metrics/timeseries")
            get(f"/api/run/{run_id}/metrics/export?format=csv")
            get(f"/api/run/{run_id}/system-metrics")
            get(f"/api/run/{run_id}/checkpoints")
            get(f"/api/run/{run_id}/artifacts")
            get(f"/api/run/{run_id}/provenance")
            get(f"/api/run/{run_id}/logs")
            get(f"/api/run/{run_id}/verify")

    # Last, because it is the parameterless route the run page falls back to and its answer
    # is whichever run the archive reports as newest — a fact about the whole crawl.
    get("/api/run")
    return routes


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------


def payload(generated: str, routes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The object that gets written, in the shape ``lib/demoData.ts`` parses.

    Built in one place because it is serialised twice — once indented for the file, once
    compact to report what the browser will actually download — and two literals of the
    same object are two chances for the reported size to describe something else.
    """
    return {"capturedAt": generated, "source": SOURCE, "routes": routes}


def render(generated: str, routes: dict[str, dict[str, Any]]) -> str:
    """The file's bytes.

    ``sort_keys`` puts the routes in path order so a diff between two snapshots is
    readable, and ``indent=1`` keeps it line-oriented for the same reason. The indentation
    is free as long as the demo *imports* this file: Vite parses an imported JSON module and
    re-emits it compactly, so the browser receives the smaller of the two figures the
    summary prints. Serve it as a static asset instead and the on-disk size is what ships,
    which is why both are reported rather than the flattering one.
    """
    return json.dumps(payload(generated, routes), sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def build(
    out: Path,
    *,
    runs: int,
    seed: int,
    work: Path,
    generated: str,
    ceiling: int,
) -> dict[str, Any]:
    """Generate, capture, write. Returns the numbers the caller reports."""
    from fastapi.testclient import TestClient

    from mlexperimenttracker import demo
    from mlexperimenttracker.server.app import create_app
    from mlexperimenttracker.storage import Storage

    if work.exists():
        _remove_tree(work)
    work.mkdir(parents=True)

    store = Storage(work / "store")
    generated_ids = demo.generate(store, runs=runs, seed=seed)
    provenance_run = _run_tracked_script(work, store, seed)

    app = create_app(store)
    with TestClient(app) as client:
        routes = crawl(client)

    text = render(generated, routes)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")

    raw = text.encode("utf-8")
    compact = json.dumps(
        payload(generated, routes),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    statuses = [entry["status"] for entry in routes.values()]
    experiments = body_of(routes["/api/experiment/all"])
    return {
        "runs": len(generated_ids) + 1,
        "experiments": len(experiments),
        "routes": len(routes),
        "ok": sum(1 for status in statuses if status == 200),
        "not_found": sum(1 for status in statuses if status == 404),
        "provenance_run": provenance_run,
        "bytes": len(raw),
        "compact_bytes": len(compact),
        "gzip_bytes": len(gzip.compress(compact, 9, mtime=0)),
        "ceiling": ceiling,
        "out": out,
    }


def _resolve(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where to write the snapshot")
    parser.add_argument("--runs", type=int, default=8, help="runs to generate; the tracked run "
                        "is written on top of them")
    parser.add_argument("--seed", type=int, default=0, help="seed for the generator and the run id")
    parser.add_argument("--work", default=str(DEFAULT_WORK), help="scratch directory, wiped first")
    parser.add_argument(
        "--generated",
        default=None,
        help="the ISO stamp to record; defaults to now. Pin it to compare two builds byte for byte",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=SIZE_CEILING,
        help="fail if the snapshot is larger than this",
    )
    args = parser.parse_args(argv)

    generated = args.generated or datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = build(
        _resolve(args.out),
        runs=args.runs,
        seed=args.seed,
        work=_resolve(args.work),
        generated=generated,
        ceiling=args.max_bytes,
    )

    print()
    print(f"snapshot     {report['out']}")
    print(f"generated    {generated}")
    print(f"runs         {report['runs']}: {report['runs'] - 1} generated, 1 tracked (provenance)")
    print(f"experiments  {report['experiments']}")
    print(f"routes       {report['routes']}: {report['ok']} ok, {report['not_found']} not found")
    print(f"provenance   {report['provenance_run']}")
    print(
        f"size         {report['bytes'] / 1024:.1f} KiB on disk, "
        f"{report['compact_bytes'] / 1024:.1f} KiB compact, "
        f"{report['gzip_bytes'] / 1024:.1f} KiB gzipped"
    )

    if report["bytes"] > report["ceiling"]:
        print(
            f"\nover the {report['ceiling']} byte ceiling by "
            f"{report['bytes'] - report['ceiling']} bytes — generate fewer runs (--runs) "
            "rather than shipping a slow page",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
