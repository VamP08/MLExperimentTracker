"""The ``mlexp`` command.

Plain argparse to keep the base install dependency-free. Server, verify/replay and demo
modules are imported inside their commands so a missing extra can't break ``mlexp path``.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import inspect
import json
import os
import shlex
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .contract import (
    METADATA_FILE,
    PATCH_FILE,
    PROVENANCE_FILE,
    STORAGE_ENV_VAR,
    SUMMARY_FILE,
    format_duration,
    map_state,
)
from .storage import Storage, StorageError

__all__ = ["main"]

PROGRAM = "mlexp"

# Exit code for each `mlexp verify` verdict. "drifted" shares 1 with generic failure; a
# verdict prints to stdout, a failure prints one line to stderr.
VERIFY_EXIT_CODES: dict[str, int] = {
    "reproducible": 0,
    "drifted": 1,
    "unverifiable": 2,
}

# Loopback only: there is no auth and some endpoints write to disk.
DEFAULT_HOST = "127.0.0.1"

# Same port the old Express backend used, so existing bookmarks still work.
DEFAULT_PORT = 5000

DEFAULT_DEMO_RUNS = 12

_SERVER_EXTRA_HINT = (
    "This command needs the server extra:\n\n"
    '    pip install "mlexperimenttracker[server]"\n\n'
    "fastapi and uvicorn are deliberately not part of the base install, so that adding\n"
    "the tracker to a training script pulls in nothing."
)


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run one subcommand. Returns a process exit code.

    Expected failures return 1 with one line on stderr; Ctrl-C returns 130.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        return 130
    except StorageError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(f"filesystem error: {exc}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Local-first ML experiment tracker: record runs, then read them back.",
        epilog=(
            "storage root:\n"
            f"  {STORAGE_ENV_VAR} if set, otherwise ~/.experiment_tracker.\n"
            f"  Run `{PROGRAM} path` to see which one is in effect."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {_version()}")

    subcommands = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    ui = subcommands.add_parser(
        "ui",
        help="serve the dashboard and open it in a browser",
        description="Serve the dashboard and the API it reads, then open a browser at it.",
    )
    ui.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default: {DEFAULT_HOST})")
    ui.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"port (default: {DEFAULT_PORT})"
    )
    ui.add_argument(
        "--storage",
        metavar="PATH",
        help=f"storage root for this server, overriding {STORAGE_ENV_VAR}",
    )
    ui.add_argument(
        "--no-browser", action="store_true", help="do not open a browser on startup"
    )
    ui.set_defaults(handler=_cmd_ui)

    ls = subcommands.add_parser(
        "ls",
        help="list experiments, or the runs inside one",
        description="List experiments with their run counts, or the runs of one experiment.",
    )
    ls.add_argument("--project", metavar="P", help="list the runs of this experiment instead")
    ls.set_defaults(handler=_cmd_ls)

    show = subcommands.add_parser(
        "show",
        help="print one run: config, final metrics, state",
        description="Print one run in full: identity, state, hyperparameters and final metrics.",
    )
    show.add_argument("run_id", metavar="<run_id>", help="run ID, unique across all experiments")
    show.set_defaults(handler=_cmd_show)

    provenance = subcommands.add_parser(
        "provenance",
        help="print what a run recorded about the world it ran in",
        description=(
            "Print the reproducibility manifest a run captured: the commit, the "
            "uncommitted diff, the interpreter, the packages and the dataset digests. "
            "Runs written before format 1.1, and runs whose capture failed, have none."
        ),
    )
    provenance.add_argument("run_id", metavar="<run_id>", help="run ID, unique across all experiments")
    provenance_output = provenance.add_mutually_exclusive_group()
    provenance_output.add_argument(
        "--json", action="store_true", help=f"print {PROVENANCE_FILE} verbatim"
    )
    provenance_output.add_argument(
        "--patch",
        action="store_true",
        help=f"write the raw {PATCH_FILE} to stdout, for piping into `git apply`",
    )
    provenance.set_defaults(handler=_cmd_provenance)

    verify = subcommands.add_parser(
        "verify",
        help="check whether a run's recorded world still matches this one",
        description=(
            "Re-ask every question the manifest answered at capture: is the commit here, "
            "is it checked out, is the uncommitted work intact, is the interpreter the "
            "same, are the packages the same, is the data the same bytes."
        ),
        epilog=(
            "exit status:\n"
            "  0  reproducible — every check answered, nothing differs\n"
            "  1  drifted      — at least one established difference\n"
            "  2  unverifiable — nothing differs, but a question went unanswered\n\n"
            "A failure that stops verification from starting at all also exits 1, and\n"
            "prints one line to stderr instead of a report to stdout."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verify.add_argument("run_id", metavar="<run_id>", help="run ID, unique across all experiments")
    verify.add_argument("--json", action="store_true", help="print the report as JSON")
    verify.add_argument(
        "--in",
        dest="directory",
        metavar="DIR",
        help="verify against this working tree instead of the one the run recorded",
    )
    verify.add_argument(
        "--no-rehash",
        action="store_true",
        help="skip re-reading the datasets, which is the slow half on a large corpus",
    )
    verify.set_defaults(handler=_cmd_verify)

    replay = subcommands.add_parser(
        "replay",
        help="reconstruct the code a run was executed from",
        description=(
            "Without --into, print the steps that would reconstruct the run and change "
            "nothing. With --into, check the recorded commit out into that directory as a "
            "git worktree and apply the recorded patch inside it. Your working tree is "
            "never touched in either case."
        ),
    )
    replay.add_argument("run_id", metavar="<run_id>", help="run ID, unique across all experiments")
    replay.add_argument(
        "--into",
        metavar="DIR",
        help="materialise the worktree here; the directory must be empty or absent",
    )
    replay.add_argument(
        "--script",
        metavar="FILE",
        help="also write the plan as a shell transcript to this file",
    )
    replay.add_argument(
        "--no-patch",
        action="store_true",
        help="check the commit out without the uncommitted changes on top of it",
    )
    replay.set_defaults(handler=_cmd_replay)

    demo = subcommands.add_parser(
        "demo",
        help="generate demo data so the dashboard has something to show",
        description=(
            "Write a set of synthetic runs into the storage root. Nothing is deleted; the "
            "demo data lands alongside anything already there."
        ),
    )
    demo.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_DEMO_RUNS,
        metavar="N",
        help=f"approximate number of runs to generate (default: {DEFAULT_DEMO_RUNS})",
    )
    demo.set_defaults(handler=_cmd_demo)

    path = subcommands.add_parser(
        "path",
        help="print the resolved storage root",
        description=(
            "Print the resolved storage root and nothing else, so it can be substituted "
            "into another command."
        ),
    )
    path.set_defaults(handler=_cmd_path)

    return parser


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def _cmd_path(args: argparse.Namespace) -> int:
    """Print just the root so it can be used in other commands. Warnings go to stderr."""
    storage = Storage()
    print(storage.root)
    if not storage.root.is_dir():
        _warn("that directory does not exist yet; it is created with the first run")
    return 0


def _cmd_ls(args: argparse.Namespace) -> int:
    storage = Storage()
    if args.project is not None:
        return _list_runs(storage, args.project)
    return _list_experiments(storage)


def _list_experiments(storage: Storage) -> int:
    """One row per experiment, using the same aggregation (and quirks) as the dashboard."""
    experiments = storage.list_experiments()
    if not experiments:
        _warn(f"no experiments under {storage.root}")
        _warn(f"run `{PROGRAM} demo` to generate some.")
        return 0

    rows = []
    for experiment in experiments:
        stats = experiment["stats"]
        rows.append(
            [
                str(experiment["name"]),
                str(stats["totalRuns"]),
                str(stats["completedRuns"]),
                str(stats["failedRuns"]),
                str(stats["runningRuns"]),
                str(stats["avgDuration"]),
                _short_time(stats["lastRun"]),
            ]
        )

    _print_table(
        ["EXPERIMENT", "RUNS", "DONE", "FAILED", "RUNNING", "AVG", "LAST RUN"],
        rows,
        right={1, 2, 3, 4, 5},
    )
    total_runs = sum(e["stats"]["totalRuns"] for e in experiments)
    counts = f"{_count(len(experiments), 'experiment')}, {_count(total_runs, 'run')}"
    print()
    print(f"{counts} in {storage.root}")
    return 0


def _list_runs(storage: Storage, project: str) -> int:
    """One row per run directory.

    Reads only metadata and summary; :meth:`Storage.read_run` would parse all of
    ``metrics.jsonl`` for every run.
    """
    if project not in storage.list_projects():
        return _fail(
            f"no experiment named {project!r} under {storage.root}. "
            f"Run `{PROGRAM} ls` to see what is there."
        )

    run_ids = storage.list_runs(project)
    if not run_ids:
        _warn(f"experiment {project!r} has no runs")
        return 0

    rows = []
    for run_id in run_ids:
        rows.append(_run_row(storage, project, run_id))

    _print_table(["RUN", "STATE", "DURATION", "STARTED", "TAGS"], rows)
    print()
    print(f"{_count(len(run_ids), 'run')} in {project}")
    return 0


def _run_row(storage: Storage, project: str, run_id: str) -> list[str]:
    run_dir = storage.run_path(project, run_id)
    if run_dir is None:
        # Exists on disk but 404s everywhere; list it so the user can see why.
        return [run_id, "unaddressable", "", "", ""]

    metadata = storage.read_json(run_dir / METADATA_FILE)
    if not isinstance(metadata, dict):
        # Hidden from the dashboard but still in the run count, so show it here.
        return [run_id, "no metadata", "", "", ""]

    summary = storage.read_json(run_dir / SUMMARY_FILE)
    summary = summary if isinstance(summary, dict) else {}
    state = summary.get("state") or metadata.get("state")
    duration = summary.get("duration")
    tags = metadata.get("tags")

    return [
        run_id,
        _state_label(state),
        _duration_label(duration),
        _short_time(metadata.get("created_at")),
        ", ".join(str(t) for t in tags) if isinstance(tags, list) else "",
    ]


def _cmd_show(args: argparse.Namespace) -> int:
    storage = Storage()
    located = _locate(storage, args.run_id)
    if located is None:
        return 1

    project, run_id = located
    run = storage.read_run(project, run_id)
    if run is None:
        return _fail(
            f"{project}/{run_id} has no readable {METADATA_FILE}. "
            "The dashboard cannot see this run either — that file is what makes a "
            "directory a run."
        )

    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    config = run.get("config") if isinstance(run.get("config"), dict) else {}

    _print_fields(
        [
            ("Run", run_id),
            ("Experiment", project),
            ("State", _state_label(run.get("state"))),
            ("Duration", _duration_label(run.get("duration"))),
            ("Started", run.get("startTime") or ""),
            ("Ended", run.get("endTime") or ""),
            ("Tags", ", ".join(str(t) for t in run.get("tags") or [])),
            ("Notes", run.get("description") or ""),
        ]
    )

    _print_config(config)
    _print_metrics(summary.get("metrics_summary"))

    print()
    print("Recorded")
    print(f"  {_count(len(run.get('metricsHistory') or []), 'logged step')} in metrics.jsonl")
    print(f"  {_count(run.get('artifactsCount', 0), 'artifact')}")
    print(f"  {_count(len(run.get('checkpoints') or []), 'checkpoint')}")
    print(f"  {_count(_system_sample_count(run.get('systemMetrics')), 'system sample')}")
    return 0


# --------------------------------------------------------------------------------------
# Provenance, verification and replay
#
# verify/replay are imported inside the handlers to keep --help and other commands fast.
# --------------------------------------------------------------------------------------


def _cmd_provenance(args: argparse.Namespace) -> int:
    """Print the manifest, or with ``--patch`` write the raw patch bytes to stdout.

    The patch is written unaltered so it still applies with ``git apply``.
    """
    storage = Storage()
    located = _locate(storage, args.run_id)
    if located is None:
        return 1
    project, run_id = located

    manifest = storage.read_provenance(project, run_id)
    if manifest is None:
        return _fail(
            f"{project}/{run_id} has no {PROVENANCE_FILE}. Either it was recorded before "
            "format 1.1, or capture failed and the run was written anyway — which is by "
            "design: a provenance failure never stops a training run."
        )

    if args.patch:
        return _dump_patch(storage, project, run_id, manifest)
    if args.json:
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return 0
    _print_provenance(project, run_id, manifest)
    return 0


def _dump_patch(storage: Storage, project: str, run_id: str, manifest: dict) -> int:
    git = manifest.get("git") if isinstance(manifest.get("git"), dict) else {}
    if not git.get("diff_file"):
        return _fail(
            f"{project}/{run_id} recorded no patch. The tree was clean at capture, or "
            "capture_diff was off."
        )
    patch = storage.read_patch(project, run_id)
    if patch is None:
        return _fail(
            f"the manifest names {git.get('diff_file')} but the file is not in the run "
            "directory. The record is incomplete — somebody deleted it, which is a "
            "supported thing to do to a file that can hold a secret."
        )
    if git.get("diff_truncated"):
        _warn(
            f"this patch was cut at {git.get('diff_bytes')} bytes by the capture limit. "
            "It is evidence of what was uncommitted, not something that will apply."
        )
    sys.stdout.flush()
    sys.stdout.buffer.write(patch)
    sys.stdout.buffer.flush()
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    storage = Storage()
    located = _locate(storage, args.run_id)
    if located is None:
        return 1
    project, run_id = located

    module = _load_optional(f"{__package__}.verify")
    if module is None:
        return _fail("verification is not available in this installation.")

    report = module.verify(
        storage,
        project,
        run_id,
        cwd=args.directory,
        rehash_datasets=not args.no_rehash,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_report(report.to_dict())
    return VERIFY_EXIT_CODES.get(report.verdict.value, 2)


def _cmd_replay(args: argparse.Namespace) -> int:
    """Print the replay plan, or build the worktree in the ``--into`` directory.

    ``--into`` is the only flag that writes, and it has no default.
    """
    storage = Storage()
    located = _locate(storage, args.run_id)
    if located is None:
        return 1
    project, run_id = located

    module = _load_optional(f"{__package__}.replay")
    if module is None:
        return _fail("replay is not available in this installation.")

    if args.into is None:
        plan = module.plan(storage, project, run_id)
        _print_plan(plan.to_dict(), materialised=None)
        return _write_script(storage, args.script, plan)

    target = Path(args.into).expanduser()
    try:
        plan = module.materialise(
            storage, project, run_id, target, apply_patch=not args.no_patch
        )
    except module.ReplayError as exc:
        # ReplayError is always raised before anything is written.
        return _fail(f"{exc}\n\nNothing was created and no repository was modified.")

    _print_plan(plan.to_dict(), materialised=target)
    return _write_script(storage, args.script, plan)


def _write_script(storage: Storage, destination: str | None, plan: Any) -> int:
    """Write the plan as a shell script via :class:`Storage`, if a path was given."""
    if destination is None:
        return 0
    path = Path(destination).expanduser()
    try:
        storage.write_bytes(path, plan.as_script().encode("utf-8"))
    except StorageError as exc:
        return _fail(str(exc))
    print()
    print(f"Wrote the plan as a shell transcript to {path}")
    print("Read it before you run it — it installs packages and re-runs a command.")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    """Generate demo data. The generator is optional, so it's imported lazily."""
    if args.runs < 1:
        return _fail("--runs must be at least 1")

    generate = _load_demo_generator()
    if generate is None:
        return _fail(
            "demo data generator is not available in this installation "
            "(mlexperimenttracker.demo could not be imported)."
        )

    storage = Storage()
    before = len(storage.list_all_runs())
    _call_with_supported_kwargs(generate, runs=args.runs, storage=storage, root=storage.root)
    after = len(storage.list_all_runs())

    print(f"Wrote {after - before} runs to {storage.root}")
    print(f"Run `{PROGRAM} ui` to look at them.")
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    """Serve the dashboard.

    ``--storage`` sets the env var, so the server and any SDK call in-process agree.
    """
    if args.storage:
        os.environ[STORAGE_ENV_VAR] = str(Path(args.storage).expanduser())

    # find_spec so "not installed" is reported before import side effects can fail.
    missing = [name for name in ("fastapi", "uvicorn") if importlib.util.find_spec(name) is None]
    if missing:
        return _fail(f"{', '.join(missing)} not installed.\n\n{_SERVER_EXTRA_HINT}")

    try:
        app = _load_app()
        import uvicorn
    except ModuleNotFoundError as exc:
        if exc.name and not exc.name.startswith(__package__ or "mlexperimenttracker"):
            # A transitive dependency of the server, not the server itself.
            return _fail(f"{exc.name} not installed.\n\n{_SERVER_EXTRA_HINT}")
        return _fail(
            f"the server module is missing from this installation ({exc}). "
            "Reinstall the package."
        )
    except ImportError as exc:
        # Installed but broken (partial install, bad compiled extension, policy block).
        return _fail(f"the server extra is installed but will not import: {exc}")

    storage = Storage()
    url = f"http://{_display_host(args.host)}:{args.port}"

    # Show the storage root: the usual mix-up is writer and server on different roots.
    # Flush so the banner prints before uvicorn's (unbuffered stderr) logs.
    print(f"{PROGRAM} {_version()}")
    print(f"  storage   {storage.root}")
    print(f"  dashboard {url}")
    if not storage.root.is_dir():
        _warn("storage root does not exist yet — the dashboard will be empty.")
        _warn(f"run `{PROGRAM} demo` to generate some data.")
    print(flush=True)

    if not args.no_browser:
        _open_browser_shortly(url)

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        return 130
    return 0


# --------------------------------------------------------------------------------------
# Optional imports
# --------------------------------------------------------------------------------------


def _load_app() -> Any:
    """Build the ASGI app from ``create_app`` (or a module-level ``app``)."""
    module = importlib.import_module(f"{__package__}.server.app")
    factory = getattr(module, "create_app", None)
    if factory is None:
        app = getattr(module, "app", None)
        if app is None:
            raise ModuleNotFoundError(
                "mlexperimenttracker.server.app exposes neither create_app nor app",
                name=f"{__package__}.server.app",
            )
        return app
    storage = Storage()
    return _call_with_supported_kwargs(factory, storage=storage, root=storage.root)


def _load_optional(name: str) -> Any | None:
    """Import one of this package's modules, or ``None`` (e.g. a partial install)."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _load_demo_generator() -> Callable[..., Any] | None:
    """Find ``demo.generate``, whether it's a function or a submodule."""
    try:
        module = importlib.import_module(f"{__package__}.demo")
    except ModuleNotFoundError:
        return None

    target = getattr(module, "generate", None)
    if inspect.ismodule(target):
        target = getattr(target, "generate", None) or getattr(target, "main", None)
    return target if callable(target) else None


def _call_with_supported_kwargs(func: Callable[..., Any], **candidates: Any) -> Any:
    """Call ``func`` with only the keyword arguments it declares."""
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return func()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        accepted = candidates
    else:
        accepted = {name: value for name, value in candidates.items() if name in parameters}
    return func(**accepted)


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def _print_table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], right: set[int] | None = None
) -> None:
    right = right or set()
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: Sequence[str]) -> str:
        parts = [
            cell.rjust(widths[i]) if i in right else cell.ljust(widths[i])
            for i, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    print(line(headers))
    for row in rows:
        print(line(row))


def _print_fields(fields: Sequence[tuple[str, Any]]) -> None:
    width = max(len(label) for label, _ in fields)
    for label, value in fields:
        text = str(value)
        if text:
            print(f"{label.ljust(width)}  {text}")


def _print_config(config: dict) -> None:
    print()
    if not config:
        print("Config (none — no config.json)")
        return
    print(f"Config ({len(config)})")
    width = max(len(str(k)) for k in config)
    for key, value in config.items():
        print(f"  {str(key).ljust(width)}  {_scalar(value)}")


def _print_metrics(metrics_summary: Any) -> None:
    """Final metrics from ``summary.json``, never recomputed from ``metrics.jsonl``.

    Entries the dashboard would silently hide get a note under the table.
    """
    print()
    if not isinstance(metrics_summary, dict) or not metrics_summary:
        print("Final metrics (none — nothing in summary.json)")
        return

    stats = ("mean", "min", "max", "stddev")
    rows: list[list[str]] = []
    notes: list[str] = []
    for name, entry in metrics_summary.items():
        if not isinstance(entry, dict):
            notes.append(
                f"{name} is {_scalar(entry)}, not an object — the dashboard ignores it"
            )
            rows.append([f"  {name}", "", "", "", "", ""])
            continue
        if "latest" not in entry:
            notes.append(f"{name} has no `latest` — the dashboard does not show the metric")
        rows.append(
            [
                f"  {name}",
                _cell(entry, "latest"),
                *[_cell(entry, stat) for stat in stats],
            ]
        )

    print(f"Final metrics ({len(metrics_summary)})")
    _print_table(
        ["  METRIC", "LATEST", "MEAN", "MIN", "MAX", "STDDEV"], rows, right={1, 2, 3, 4, 5}
    )
    for note in notes:
        print(f"  ! {note}")


def _print_provenance(project: str, run_id: str, manifest: dict) -> None:
    """Print the manifest, git commit and dirty state first."""
    git = _mapping(manifest.get("git"))
    _print_fields(
        [
            ("Run", run_id),
            ("Experiment", project),
            ("Captured", manifest.get("captured_at") or ""),
        ]
    )

    print()
    if not git.get("available"):
        print(f"Git (not recorded — {git.get('reason') or 'no reason recorded'})")
    else:
        print("Git")
        untracked = git.get("untracked") if isinstance(git.get("untracked"), list) else []
        untracked_note = _count(len(untracked), "untracked file")
        if git.get("untracked_truncated"):
            untracked_note += " (capped; there were more)"
        rows = [
            ("  Commit", git.get("commit") or ""),
            ("  Branch", git.get("branch") or "(detached HEAD)"),
            ("  Remote", git.get("remote") or "(no origin)"),
            ("  Tree", ("dirty" if git.get("dirty") else "clean") + f", {untracked_note}"),
            ("  Patch", _patch_label(git)),
            ("  Note", git.get("reason") or ""),
        ]
        _print_fields(rows)

    print()
    python = _mapping(manifest.get("python"))
    plat = _mapping(manifest.get("platform"))
    hardware = _mapping(manifest.get("hardware"))
    packages = _mapping(manifest.get("packages"))
    environment = _mapping(manifest.get("environment"))
    command = _mapping(manifest.get("command"))
    argv = command.get("argv") if isinstance(command.get("argv"), list) else []
    _print_fields(
        [
            ("Python", f"{python.get('version') or '?'} {python.get('implementation') or ''}".strip()),
            ("Interpreter", python.get("executable") or ""),
            (
                "Platform",
                " ".join(
                    str(part)
                    for part in (plat.get("system"), plat.get("release"), plat.get("machine"))
                    if part
                ),
            ),
            ("Processor", plat.get("processor") or ""),
            ("Hardware", _hardware_label(hardware)),
            ("Packages", _count(len(packages), "distribution")),
            (
                "Environment",
                ", ".join(f"{k}={v}" for k, v in sorted(environment.items())) or "(none recorded)",
            ),
            ("Command", " ".join(str(part) for part in argv)),
            ("Directory", command.get("cwd") or ""),
        ]
    )

    datasets = manifest.get("datasets") if isinstance(manifest.get("datasets"), list) else []
    print()
    if not datasets:
        print("Datasets (none — nothing was hashed for this run)")
        return
    print(f"Datasets ({len(datasets)})")
    rows = []
    for entry in datasets:
        entry = _mapping(entry)
        digest = entry.get("digest") or entry.get("sha256")
        rows.append(
            [
                f"  {entry.get('name') or entry.get('path') or ''}",
                f"{entry.get('algorithm') or 'sha256'}:{digest}"
                if digest
                else f"! {entry.get('error') or 'not hashed'}",
                _scalar(entry.get("bytes", "")),
                _scalar(entry.get("files", "")),
            ]
        )
    _print_table(["  DATASET", "DIGEST", "BYTES", "FILES"], rows, right={2, 3})


def _patch_label(git: dict) -> str:
    if not git.get("diff_file"):
        return "(none recorded)"
    parts = [str(git.get("diff_file")), f"{_scalar(git.get('diff_bytes', 0))} bytes"]
    digest = git.get("diff_sha256")
    if digest:
        parts.append(f"sha256 {str(digest)[:12]}")
    if git.get("diff_truncated"):
        parts.append("TRUNCATED — will not apply")
    return "  ".join(parts)


def _hardware_label(hardware: dict) -> str:
    parts = [_count(int(hardware.get("cpu_count") or 0), "CPU")]
    gpus = hardware.get("gpus") if isinstance(hardware.get("gpus"), list) else []
    for gpu in gpus:
        gpu = _mapping(gpu)
        memory = gpu.get("memory_total_mb")
        parts.append(f"{gpu.get('name') or 'GPU'}" + (f" ({memory} MB)" if memory else ""))
    return "; ".join(parts)


def _print_report(report: dict) -> None:
    """Print the verdict, the check table, then details for every non-``ok`` check."""
    summary = _mapping(report.get("summary"))
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    verdict = str(report.get("verdict") or "unverifiable")

    _print_fields(
        [
            ("Run", report.get("run_id") or ""),
            ("Experiment", report.get("project") or ""),
            ("Verdict", verdict.upper()),
        ]
    )

    print()
    _print_table(
        ["CHECK", "STATUS"],
        [[str(_mapping(c).get("name") or ""), str(_mapping(c).get("status") or "")] for c in checks],
    )
    print()
    print(
        f"{summary.get('ok', 0)} ok, {summary.get('drift', 0)} drift, "
        f"{summary.get('unknown', 0)} unknown"
    )

    for status, heading in (("drift", "Drifted"), ("unknown", "Unanswered")):
        selected = [_mapping(c) for c in checks if _mapping(c).get("status") == status]
        if not selected:
            continue
        print()
        print(heading)
        for check in selected:
            print(f"  {check.get('name')}")
            if check.get("expected") is not None:
                print(f"    recorded  {_scalar(check.get('expected'))}")
            if check.get("actual") is not None:
                print(f"    found     {_scalar(check.get('actual'))}")
            if check.get("detail"):
                print(f"    {check.get('detail')}")


def _print_plan(plan: dict, materialised: Path | None) -> None:
    """Print the steps, then the warnings separately so they don't read as steps."""
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    warnings = plan.get("warnings") if isinstance(plan.get("warnings"), list) else []

    _print_fields(
        [
            ("Run", plan.get("run_id") or ""),
            ("Experiment", plan.get("project") or ""),
            ("Target", plan.get("target") or "(none given — printing the plan only)"),
        ]
    )

    print()
    if materialised is not None:
        print(f"Materialised the recorded commit at {materialised}")
        print("Your working tree was not touched. What was done, and what is left to do:")
    else:
        print("Nothing has been created. To reconstruct this run:")
    print()

    if not steps:
        print("  (no steps — see the warnings below)")
    for step in steps:
        step = _mapping(step)
        suffix = "" if step.get("required", True) else "   (optional)"
        print(f"  {step.get('order')}. {step.get('description')}{suffix}")
        command = step.get("command")
        if isinstance(command, list) and command:
            print(f"     {_command_line([str(p) for p in command])}")
        else:
            print("     (no command — do this by hand)")

    requirements = plan.get("requirements") if isinstance(plan.get("requirements"), list) else []
    if requirements:
        print()
        print(f"{_count(len(requirements), 'recorded distribution')} in the package set.")

    if warnings:
        print()
        print("What this plan cannot promise")
        for warning in warnings:
            print(f"  ! {warning}")


def _cell(entry: dict, key: str) -> str:
    """Blank for a missing key, ``null`` for an explicit null."""
    return _scalar(entry[key]) if key in entry else ""


def _scalar(value: Any) -> str:
    """Render a value as JSON (``true``/``null``), but strings without quotes."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)


def _state_label(state: Any) -> str:
    """The raw state, plus the dashboard's mapping when it differs.

    Unknown states map to ``running``, so this makes typos visible.
    """
    if not isinstance(state, str) or not state:
        return "(unset -> running)"
    mapped = map_state(state)
    return state if mapped == state else f"{state} -> {mapped}"


def _duration_label(duration: Any) -> str:
    """Seconds formatted like the UI. Non-numbers show as ``0s`` plus the bad value."""
    if duration is None:
        return "0s"
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        return f"0s (not a number: {_scalar(duration)})"
    return format_duration(duration)


def _short_time(value: Any) -> str:
    """Trim an ISO timestamp to minutes."""
    if not isinstance(value, str) or not value:
        return ""
    if value == "N/A":
        return ""
    if "T" in value and len(value) >= 16:
        return value[:16].replace("T", " ")
    return value


def _count(quantity: int, noun: str) -> str:
    return f"{quantity} {noun}" if quantity == 1 else f"{quantity} {noun}s"


def _command_line(parts: Sequence[str]) -> str:
    """Quote an argument list for the current platform's shell (cmd on Windows)."""
    if os.name == "nt":
        return subprocess.list2cmdline(list(parts))
    return " ".join(shlex.quote(part) for part in parts)


def _mapping(value: Any) -> dict:
    """``value`` if it's a dict, else ``{}``. Provenance fields are all optional."""
    return value if isinstance(value, dict) else {}


def _locate(storage: Storage, run_id: str) -> tuple[str, str] | None:
    """Resolve a run ID to ``(project, run_id)``, or warn on stderr and return ``None``."""
    located = storage.find_run(run_id)
    if located is None:
        _warn(
            f"no run {run_id!r} under {storage.root}. "
            f"Run `{PROGRAM} ls --project <experiment>` to see the run IDs."
        )
        return None
    return located


def _system_sample_count(system_metrics: Any) -> int:
    if isinstance(system_metrics, list):
        return len(system_metrics)
    return 0


def _display_host(host: str) -> str:
    """``0.0.0.0`` is a bind address, not somewhere a browser can go."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def _open_browser_shortly(url: str) -> None:
    """Open the browser after a short delay so uvicorn can bind first.

    Daemon timer, so a server that fails to start doesn't keep the process alive.
    """
    timer = threading.Timer(1.0, _open_browser, args=(url,))
    timer.daemon = True
    timer.start()


def _open_browser(url: str) -> None:
    # No browser on a headless box is fine; keep serving (e.g. over an SSH tunnel).
    with contextlib.suppress(Exception):
        webbrowser.open(url)


# --------------------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------------------


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("mlexperimenttracker")
    except PackageNotFoundError:
        # Running from a source tree that was never installed.
        package = sys.modules.get(__package__ or "")
        return str(getattr(package, "__version__", "0+unknown"))


def _fail(message: str) -> int:
    print(f"{PROGRAM}: {message}", file=sys.stderr)
    return 1


def _warn(message: str) -> None:
    print(f"{PROGRAM}: {message}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
