"""The ``mlexp`` command.

The command exists to collapse an install story. Reading a run used to mean two package
managers, three installs and two long-running processes; it now means ``pip install`` and
``mlexp ui``. Everything here is in service of that: the subcommands are the smallest set
that lets somebody install the package, see that data exists, look at one run, and open
the dashboard — without reading any documentation first.

``argparse`` rather than a CLI framework, because the base install has no dependencies and
a command-line parser is not worth breaking that for.

The heavy halves are imported inside the command that needs them. ``fastapi`` and
``uvicorn`` live in an extra, the demo generator is optional, and neither should be able to
turn ``mlexp path`` into an ImportError.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.util
import inspect
import json
import os
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .contract import METADATA_FILE, STORAGE_ENV_VAR, SUMMARY_FILE, format_duration, map_state
from .storage import Storage, StorageError

__all__ = ["main"]

PROGRAM = "mlexp"

#: Loopback by default because there is no authentication anywhere in the product and
#: three endpoints write to disk — the trust boundary is the interface, not a login form.
DEFAULT_HOST = "127.0.0.1"

#: The port the Express backend this package replaces has always used, so existing notes,
#: bookmarks and ``.env`` files keep pointing at the right place. Pass ``--port`` to run
#: the two side by side while parity is still being checked.
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

    Every expected failure returns 1 with a sentence on stderr. A traceback out of this
    function is a bug: the user of a tracker is in the middle of something else, and a
    stack trace from a viewer is an interruption they did not budget for.
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
    """Print the root alone — no label, no banner — because the useful thing to do with
    it is substitute it into another command. Anything else goes to stderr."""
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
    """One row per experiment, with the same counters the dashboard shows.

    The numbers come from the same aggregation the API serves, quirks included — a run
    whose ``metadata.json`` is unreadable still counts toward the total, and a run in
    ``initialized`` or ``interrupted`` falls into no state bucket. A CLI that quietly
    computed better numbers than the dashboard would make the two impossible to compare,
    which is the one thing this command is for.
    """
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
    """One row per run directory, reading only the two small JSON files.

    Deliberately not :meth:`Storage.read_run`, which also parses the whole of
    ``metrics.jsonl`` for every run — a listing that gets slower the longer you train is
    a listing people stop using.
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
        # Unaddressable on disk, and therefore a 404 on every endpoint. Say so rather
        # than omitting the directory, because it is visibly there in the file manager.
        return [run_id, "unaddressable", "", "", ""]

    metadata = storage.read_json(run_dir / METADATA_FILE)
    if not isinstance(metadata, dict):
        # The one file whose absence hides a run completely. It still inflates the
        # experiment's run count, so it has to be listed to be explicable.
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
    located = storage.find_run(args.run_id)
    if located is None:
        return _fail(
            f"no run {args.run_id!r} under {storage.root}. "
            f"Run `{PROGRAM} ls --project <experiment>` to see the run IDs."
        )

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


def _cmd_demo(args: argparse.Namespace) -> int:
    """Generate demo data.

    Imported here rather than at module scope for two reasons: a training script that
    imports the SDK should not pay for a data generator it will never call, and the
    generator is optional enough that its absence must not break ``mlexp path``.
    """
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

    ``--storage`` is applied by setting the environment variable rather than by threading
    a path through the application factory. That is the variable both halves of the
    product already resolve, so one mechanism configures the server, anything it imports,
    and any SDK call made in the same process — and there is only one answer to "where is
    the data" to get wrong.
    """
    if args.storage:
        os.environ[STORAGE_ENV_VAR] = str(Path(args.storage).expanduser())

    # find_spec rather than an import, so that "not installed" is reported before any of
    # the server's own import side effects can fail for an unrelated reason.
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
        # Present but unusable — a partial install, a broken compiled extension, or a
        # machine policy blocking one. Worth its own message: reinstalling the extra is
        # the fix for the first and useless for the rest.
        return _fail(f"the server extra is installed but will not import: {exc}")

    storage = Storage()
    url = f"http://{_display_host(args.host)}:{args.port}"

    # The Express server prints its own resolved root at startup for the same reason: the
    # commonest confusion in this product is two halves pointed at two different roots.
    # Flushed, because stdout is block-buffered when captured while uvicorn logs to an
    # unbuffered stderr — without this the banner lands after the lines it introduces.
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
    """Build the ASGI application.

    The factory is looked up by name and handed the storage root only if it asks for one,
    because the environment variable set by ``--storage`` already answers that question
    for anything the server constructs.
    """
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


def _load_demo_generator() -> Callable[..., Any] | None:
    """Resolve ``mlexperimenttracker.demo.generate`` whether it is a function on the
    package or a module inside it. The generator is being written alongside this file and
    either shape is a reasonable thing to have chosen."""
    try:
        module = importlib.import_module(f"{__package__}.demo")
    except ModuleNotFoundError:
        return None

    target = getattr(module, "generate", None)
    if inspect.ismodule(target):
        target = getattr(target, "generate", None) or getattr(target, "main", None)
    return target if callable(target) else None


def _call_with_supported_kwargs(func: Callable[..., Any], **candidates: Any) -> Any:
    """Call ``func`` with only the keyword arguments it actually declares.

    Lets this module stay agnostic about whether a collaborator's function takes a
    ``Storage``, a root path or neither, without pinning either side to a signature that
    would then be awkward to change.
    """
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
    """The final metrics, read from ``summary.json`` and never derived here.

    Nothing in this product computes an aggregate from ``metrics.jsonl`` — the writer
    records every metric twice, per step and pre-aggregated — so a metric with a chart but
    no number here is a writer bug, and computing the number in this command would hide
    exactly that. The two ways an entry can be silently invisible in the dashboard get a
    note under the table rather than a wide cell inside it.
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


def _cell(entry: dict, key: str) -> str:
    """Absent and ``null`` are different things in a stats table: absent is a stat the
    writer did not record, ``null`` is one it recorded as nothing."""
    return _scalar(entry[key]) if key in entry else ""


def _scalar(value: Any) -> str:
    """Render a JSON scalar the way it sits on disk, minus the quotes on strings.

    ``true``/``false``/``null`` rather than ``True``/``False``/``None``: the file is JSON,
    and somebody comparing this output against the file should not have to translate.
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)


def _state_label(state: Any) -> str:
    """The state as written, plus what the dashboard makes of it when the two differ.

    Every unrecognised value maps to ``running``, so a typo produces a run that reads as
    alive forever. Showing both columns is the cheapest way to catch that.
    """
    if not isinstance(state, str) or not state:
        return "(unset -> running)"
    mapped = map_state(state)
    return state if mapped == state else f"{state} -> {mapped}"


def _duration_label(duration: Any) -> str:
    """Seconds, formatted the way the UI formats them.

    ``duration`` must be a JSON number of seconds; the reader renders anything else as
    ``0s``, so an ISO-8601 duration or a stringified ``timedelta`` looks like an instant
    run rather than like a mistake. Say which it was.
    """
    if duration is None:
        return "0s"
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        return f"0s (not a number: {_scalar(duration)})"
    return format_duration(duration)


def _short_time(value: Any) -> str:
    """Trim an ISO timestamp to minutes. The seconds are in the file; nobody scans a
    listing for them."""
    if not isinstance(value, str) or not value:
        return ""
    if value == "N/A":
        return ""
    if "T" in value and len(value) >= 16:
        return value[:16].replace("T", " ")
    return value


def _count(quantity: int, noun: str) -> str:
    return f"{quantity} {noun}" if quantity == 1 else f"{quantity} {noun}s"


def _system_sample_count(system_metrics: Any) -> int:
    if isinstance(system_metrics, list):
        return len(system_metrics)
    return 0


def _display_host(host: str) -> str:
    """``0.0.0.0`` is a bind address, not somewhere a browser can go."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def _open_browser_shortly(url: str) -> None:
    """Open the browser from a timer, after uvicorn has had a moment to bind.

    A daemon thread so that a failure to start the server does not leave the process
    alive waiting to open a tab at it.
    """
    timer = threading.Timer(1.0, _open_browser, args=(url,))
    timer.daemon = True
    timer.start()


def _open_browser(url: str) -> None:
    # A headless machine has no browser to open, and that is not a reason to stop serving
    # the dashboard to whatever is going to connect to it over an SSH tunnel instead.
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
