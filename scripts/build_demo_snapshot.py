"""Capture a real API's real responses into one JSON file, for the static demo build.

Generates eight runs with ``mlexp demo``, adds a ninth by running examples/quickstart.py in a
throwaway git repo (so it has a real provenance manifest), serves them through the FastAPI
app and records every route the dashboard calls, 404s included. The static build replays it.

Same seed on the same machine gives identical bytes: the run's clock, the run id suffix, the
git identity/dates and the work dir are all pinned. Manifests record the interpreter,
packages, CPU and timezone, so output differs across machines. ``--generated`` pins the stamp.

Needs git on PATH. Overwrites ml_frontend/src/demo/snapshot.json. That file is tracked as an
empty placeholder because the normal build imports it; don't commit the generated version.
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

# Use the working tree, not an installed copy.
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Imported by ml_frontend/src/lib/demoData.ts. Relative paths resolve against the repo root.
DEFAULT_OUT = Path("ml_frontend/src/demo/snapshot.json")

# Shown by the demo banner. The app runs in-process, not behind a real HTTP server.
SOURCE = "mlexp demo runs, captured from the real FastAPI app by scripts/build_demo_snapshot.py"

# Fixed (not a temp dir) so paths inside the manifest are stable. build/ is gitignored.
DEFAULT_WORK = REPO_ROOT / "build" / "demo-snapshot"

# Every demo visitor downloads the snapshot.
SIZE_CEILING = 2_000_000

PROVENANCE_PROJECT = "churn-logreg"

# Pinned so the throwaway repo's commit hash is reproducible.
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
# Deterministic clock for the tracked run
# --------------------------------------------------------------------------------------


class _FrozenClock:
    """Stand-in for the ``time`` module during the tracked run.

    Wall and monotonic time both come off one counter that advances a fixed step per read,
    so they stay consistent. The durations it produces count clock reads, not real time.
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
        # Everything else (sleep etc.) comes from the real module.
        return getattr(_time, name)


class _SeededSecrets:
    """Seeded ``secrets.token_hex`` for the run id suffix. Not used for anything secret."""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def token_hex(self, nbytes: int = 32) -> str:
        return self._rng.randbytes(nbytes).hex()


def _frozen_datetime(clock: _FrozenClock) -> type[datetime]:
    """A ``datetime`` subclass whose ``now()`` reads ``clock``."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:  # type: ignore[override]
            moment = clock.wall()
            return moment.astimezone(tz) if tz is not None else moment.astimezone()

    return _Frozen


@contextmanager
def _pinned_clock(instant: datetime, seed: int) -> Iterator[None]:
    """Patch the SDK's clocks and run id suffix for the ``with`` body, then restore them.

    Patched rather than injected because the SDK has no clock parameter.
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
    """Run as if launched from ``path`` with ``argv``; the manifest records both."""
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
    """Delete the work dir. Git objects are read-only, which breaks rmtree on Windows."""

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
    """Make a one-commit repo holding examples/quickstart.py; return the commit hash.

    autocrlf is off so line endings (and blobs) don't change on Windows. The tree is left
    clean because a patch download can't work in a static build.
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
    """Latest ``created_at`` in the archive."""
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
    """Run the committed quickstart through the SDK and return the new run id.

    In-process so the frozen clock applies. runpy runs the file from the repo on disk, so
    the recorded commit is the code that ran.
    """
    repo = work / "demo-repo"
    output = work / "quickstart-output"
    commit = _build_repo(repo)

    # After the newest generated run, so the dashboard opens on this one.
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
    """One captured response, in the shape ml_frontend/src/lib/demoData.ts replays.

    ``json`` or ``text`` (plus ``contentType``), never both, chosen by the response's
    content type.
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
    """The captured body, from ``text`` or ``json``."""
    return entry["text"] if "text" in entry else entry["json"]


def crawl(client: Any) -> dict[str, dict[str, Any]]:
    """Record every route the frontend calls, with its status.

    Skipped: the two downloads (``/logs/download``, ``/patch``) and the two PATCH writes
    (tags, description). The demo disables those controls; tests/test_demo_snapshot.py keeps
    the same list. ``/logs`` is captured unpaginated since demo runs fit in one page.
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

    # Returns the newest run overall, so do it last.
    get("/api/run")
    return routes


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------


def payload(generated: str, routes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The snapshot object. Serialised twice: indented for the file, compact for sizing."""
    return {"capturedAt": generated, "source": SOURCE, "routes": routes}


def render(generated: str, routes: dict[str, dict[str, Any]]) -> str:
    """The file's text: sorted keys and ``indent=1`` so diffs stay readable.

    Vite re-emits imported JSON compactly, so the browser gets the compact size.
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
