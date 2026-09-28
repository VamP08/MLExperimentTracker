"""Tests for output capture: logs.jsonl, the stream tee, the logging handler and the routes.

Captured output must still reach the terminal, and sys.stdout/stderr must be restored after
finish, on success and on crash. Excepthook/atexit paths are tested in a subprocess.
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import mlexperimenttracker as met
from mlexperimenttracker import contract
from mlexperimenttracker import run as run_module
from mlexperimenttracker.contract import LOGS_FILE, RunState
from mlexperimenttracker.logs import LogWriter, StreamTee, install
from mlexperimenttracker.storage import Storage

SRC = Path(__file__).resolve().parents[1] / "src"

PROJECT = "logging-project"


@pytest.fixture(autouse=True)
def _no_leaked_runs():
    """Finish leftover runs so atexit doesn't write into a deleted tmp dir."""
    yield
    for run in list(run_module._ACTIVE.values()):
        try:
            run.finish(RunState.COMPLETED)
        except Exception:
            run_module._ACTIVE.pop(id(run), None)


@pytest.fixture(autouse=True)
def _streams_are_returned():
    """Fail any test that leaves sys.stdout/stderr replaced."""
    stdout, stderr = sys.stdout, sys.stderr
    yield
    assert sys.stdout is stdout, "a test left sys.stdout replaced"
    assert sys.stderr is stderr, "a test left sys.stderr replaced"


def start(tmp_path: Path, **kwargs):
    """Start a run without signal capture (it would replace pytest's SIGINT handler)."""
    kwargs.setdefault("capture_signals", False)
    kwargs.setdefault("provenance", False)
    kwargs.setdefault("project", PROJECT)
    return met.init(storage_path=tmp_path, **kwargs)


def read_lines(tmp_path: Path, run_id: str) -> list[dict]:
    """Parse logs.jsonl directly, bypassing Storage, to separate reader and writer bugs."""
    path = tmp_path / PROJECT / run_id / LOGS_FILE
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]


def run_child(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "child.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SRC)!r})
            import mlexperimenttracker as met
            """
        )
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**_child_env(tmp_path)},
    )


def _child_env(tmp_path: Path) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["EXPERIMENT_STORAGE_PATH"] = str(tmp_path)
    return env


# --------------------------------------------------------------------------------------
# The format
# --------------------------------------------------------------------------------------


def test_the_format_version_carries_the_log_file() -> None:
    assert contract.FORMAT_VERSION == "1.2"
    assert contract.LOGS_FILE == "logs.jsonl"
    assert contract.LOG_LEVELS == ("debug", "info", "warning", "error", "critical")
    assert contract.LOG_SOURCES == ("stdout", "stderr", "logging", "user")


def test_a_record_round_trips_with_every_field_the_contract_names(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    with start(tmp_path, capture_output=False, capture_logging=False) as run:
        run.log_text("Epoch 1/100")

        records = storage.read_logs(PROJECT, run.id)
        assert len(records) == 1
        record = records[0]
        assert record["message"] == "Epoch 1/100"
        assert record["level"] == "info"
        assert record["source"] == "user"
        assert isinstance(record["timestamp"], float)
        assert isinstance(record["absolute_timestamp"], float)
        # Seconds since run start plus epoch seconds, same as metrics.jsonl.
        assert 0.0 <= record["timestamp"] < 60.0
        assert abs(record["absolute_timestamp"] - time.time()) < 60.0


def test_a_1_1_directory_still_reads_as_a_run(tmp_path: Path) -> None:
    """A log file doesn't change what the existing read paths return."""
    storage = Storage(tmp_path)
    storage.create_run(
        PROJECT,
        "logs_compat_run",
        {"created_at": "2026-08-13T10:00:00+05:30", "format_version": "1.1", "tags": []},
    )
    before = storage.read_run(PROJECT, "logs_compat_run")

    assert storage.append_log(
        PROJECT,
        "logs_compat_run",
        {"timestamp": 0.1, "absolute_timestamp": 1.0, "level": "info", "message": "x", "source": "user"},
    )

    assert storage.read_run(PROJECT, "logs_compat_run") == before
    assert storage.read_artifacts(PROJECT, "logs_compat_run") == []


# --------------------------------------------------------------------------------------
# Storage: filtering, paging, tolerance
# --------------------------------------------------------------------------------------


def _seed(storage: Storage, run_id: str = "logs_seed_run") -> str:
    storage.create_run(PROJECT, run_id, {"created_at": "2026-08-13T10:00:00+05:30"})
    for index, level in enumerate(["debug", "info", "info", "warning", "error", "critical"]):
        storage.append_log(
            PROJECT,
            run_id,
            {
                "timestamp": float(index),
                "absolute_timestamp": 1786506300.0 + index,
                "level": level,
                "message": f"line {index}",
                "source": "stdout",
            },
        )
    return run_id


def test_the_level_filter_selects_one_severity_exactly(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    run_id = _seed(storage)

    assert [r["message"] for r in storage.read_logs(PROJECT, run_id, level="info")] == [
        "line 1",
        "line 2",
    ]
    assert len(storage.read_logs(PROJECT, run_id, level="error")) == 1
    # Case-insensitive since it comes from a query string.
    assert len(storage.read_logs(PROJECT, run_id, level="ERROR")) == 1
    # An unknown level matches nothing, not everything.
    assert storage.read_logs(PROJECT, run_id, level="trace") == []


def test_pagination_walks_the_file_and_clamps_nonsense(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    run_id = _seed(storage)

    assert [r["message"] for r in storage.read_logs(PROJECT, run_id, limit=2)] == [
        "line 0",
        "line 1",
    ]
    assert [r["message"] for r in storage.read_logs(PROJECT, run_id, limit=2, offset=2)] == [
        "line 2",
        "line 3",
    ]
    assert len(storage.read_logs(PROJECT, run_id, offset=4)) == 2
    assert storage.read_logs(PROJECT, run_id, offset=99) == []
    assert storage.read_logs(PROJECT, run_id, limit=0) == []
    assert storage.read_logs(PROJECT, run_id, limit=-5) == []
    assert len(storage.read_logs(PROJECT, run_id, offset=-5)) == 6
    # Offset applies after filtering.
    assert [
        r["message"] for r in storage.read_logs(PROJECT, run_id, level="info", offset=1)
    ] == ["line 2"]


def test_a_torn_final_line_costs_that_line_and_nothing_else(tmp_path: Path) -> None:
    """An interrupted append leaves half a line; only that record is lost."""
    storage = Storage(tmp_path)
    run_id = _seed(storage)
    path = tmp_path / PROJECT / run_id / LOGS_FILE
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"timestamp": 9.0, "level": "info", "mess')

    records = storage.read_logs(PROJECT, run_id)
    assert len(records) == 6
    assert records[-1]["message"] == "line 5"
    assert "line 5" in storage.read_logs_text(PROJECT, run_id)


def test_the_text_rendering_is_one_line_per_record(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    run_id = _seed(storage)

    text = storage.read_logs_text(PROJECT, run_id)
    lines = text.splitlines()
    assert len(lines) == 6
    assert "INFO" in lines[1]
    assert "stdout" in lines[1]
    assert lines[1].endswith("line 1")
    assert text.endswith("\n")


@pytest.mark.parametrize(
    ("project", "run_id"),
    [("../escape", "run"), ("proj", "../escape"), ("proj", "a/b"), ("", "run")],
)
def test_an_unaddressable_name_is_refused_by_every_log_method(
    project: str, run_id: str, tmp_path: Path
) -> None:
    storage = Storage(tmp_path)
    assert storage.append_log(project, run_id, {"message": "x"}) is False
    assert storage.read_logs(project, run_id) == []
    assert storage.read_logs_text(project, run_id) == ""


def test_appending_never_creates_a_run_directory(tmp_path: Path) -> None:
    """A dir with logs but no metadata.json would still count against the success rate."""
    storage = Storage(tmp_path)
    assert storage.append_log(PROJECT, "never_created", {"message": "x"}) is False
    assert not (tmp_path / PROJECT / "never_created").exists()


# --------------------------------------------------------------------------------------
# The tee
# --------------------------------------------------------------------------------------


def test_print_is_captured_and_still_reaches_the_real_stdout(tmp_path: Path, capsys) -> None:
    storage = Storage(tmp_path)
    with start(tmp_path) as run:
        print("Epoch 1/100")
        print("to stderr", file=sys.stderr)

        messages = [r["message"] for r in storage.read_logs(PROJECT, run.id)]
        assert "Epoch 1/100" in messages
        assert "to stderr" in messages

        sources = {r["message"]: r["source"] for r in storage.read_logs(PROJECT, run.id)}
        assert sources["Epoch 1/100"] == "stdout"
        assert sources["to stderr"] == "stderr"
        # stderr is warning, not error, since progress bars (tqdm) write there too.
        levels = {r["message"]: r["level"] for r in storage.read_logs(PROJECT, run.id)}
        assert levels["to stderr"] == "warning"

    captured = capsys.readouterr()
    assert "Epoch 1/100" in captured.out, "capture swallowed the user's stdout"
    assert "to stderr" in captured.err, "capture swallowed the user's stderr"


def test_a_partial_line_is_buffered_and_flushed_on_close(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    run = start(tmp_path)
    print("half ", end="")
    print("a line", end="")

    # Nothing yet; the line isn't finished.
    assert [r["message"] for r in storage.read_logs(PROJECT, run.id)] == []

    run.finish()
    assert [r["message"] for r in storage.read_logs(PROJECT, run.id)] == ["half a line"]


def test_a_progress_bar_records_only_what_the_terminal_shows(tmp_path: Path) -> None:
    """Frames overwritten by \\r are not recorded, only the final line."""
    storage = Storage(tmp_path)
    run = start(tmp_path)
    for percent in (10, 50, 100):
        print(f"\rprogress {percent}%", end="")
    print()

    messages = [r["message"] for r in storage.read_logs(PROJECT, run.id)]
    assert messages == ["progress 100%"]
    run.finish()


def test_the_tee_survives_bytes_and_objects(tmp_path: Path) -> None:
    """Bytes or odd objects written by a library must not crash the tee."""
    storage = Storage(tmp_path)
    writer = LogWriter(storage, PROJECT, "tee_run", start_time=time.time())
    storage.create_run(PROJECT, "tee_run", {"created_at": "2026-08-13T10:00:00+05:30"})

    sink = io.StringIO()
    tee = StreamTee(sink, writer, source="stdout")
    tee.write("text\n")
    tee.writelines(["a\n", "b\n"])
    tee._capture(b"bytes\n")
    tee._capture(object())
    tee.detach()

    messages = [r["message"] for r in storage.read_logs(PROJECT, "tee_run")]
    assert messages[:3] == ["text", "a", "b"]
    assert "bytes" in messages
    assert sink.getvalue() == "text\na\nb\n"


def test_the_tee_delegates_what_it_does_not_implement(tmp_path: Path) -> None:
    """Progress bars check isatty/encoding, so those must pass through."""
    storage = Storage(tmp_path)
    writer = LogWriter(storage, PROJECT, "attr_run", start_time=time.time())
    tee = StreamTee(io.StringIO(), writer)

    assert tee.isatty() is False
    assert tee.stream is not None
    assert tee.source == "stdout"
    with pytest.raises(AttributeError):
        getattr(tee, "no_such_attribute")  # noqa: B009 - the lookup is the assertion


# --------------------------------------------------------------------------------------
# The logging handler
# --------------------------------------------------------------------------------------


def test_logging_calls_are_captured(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    root = logging.getLogger()
    previous = root.level
    # pytest already configured the root logger; the unconfigured case is tested in a
    # subprocess below.
    root.setLevel(logging.INFO)
    try:
        with start(tmp_path) as run:
            logging.info("epoch %d done", 3)
            logging.warning("loss is nan")
            logging.error("training diverged")

            captured = {
                r["message"]: r for r in storage.read_logs(PROJECT, run.id)
                if r["source"] == "logging"
            }
    finally:
        root.setLevel(previous)

    assert captured["epoch 3 done"]["level"] == "info"
    assert captured["loss is nan"]["level"] == "warning"
    assert captured["training diverged"]["level"] == "error"


def test_the_handler_is_removed_and_no_other_handler_is_touched(tmp_path: Path) -> None:
    root = logging.getLogger()
    before = list(root.handlers)

    run = start(tmp_path)
    assert len(root.handlers) == len(before) + 1
    run.finish()

    assert root.handlers == before


@pytest.mark.slow
def test_an_unconfigured_logging_info_is_captured(tmp_path: Path) -> None:
    """Fresh root logger is at WARNING, so capture lowers it to INFO (only in that case)."""
    result = run_child(
        tmp_path,
        """
        import logging
        run = met.init(project="logging-project", run_id="unconfigured_run", provenance=False)
        logging.info("from an unconfigured logger")
        run.finish()
        print("CHILD_OK")
        """,
    )
    assert result.returncode == 0, result.stderr
    messages = [r["message"] for r in read_lines(tmp_path, "unconfigured_run")]
    assert "from an unconfigured logger" in messages


# --------------------------------------------------------------------------------------
# The size budget
# --------------------------------------------------------------------------------------


def test_the_budget_truncates_and_records_that_it_did(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    with start(tmp_path, capture_output=False, capture_logging=False, log_limit_bytes=1024) as run:
        for index in range(400):
            run.log_text(f"line {index} " + "x" * 60)

        records = storage.read_logs(PROJECT, run.id)

    assert 0 < len(records) < 400, "the budget did not stop the writer"
    last = records[-1]
    assert last["level"] == "warning"
    assert "log capture stopped" in last["message"]
    assert "1024" in last["message"]
    # One notice, not one per suppressed line.
    assert sum("log capture stopped" in r["message"] for r in records) == 1

    size = (tmp_path / PROJECT / run.id / LOGS_FILE).stat().st_size
    assert size < 1024 + len(json.dumps(last).encode("utf-8")) + 64


def test_the_budget_is_never_silent(tmp_path: Path) -> None:
    """Truncation always writes a notice so a cut-off log isn't mistaken for the run ending."""
    storage = Storage(tmp_path)
    writer = LogWriter(storage, PROJECT, "budget_run", start_time=time.time(), max_bytes=0)
    storage.create_run(PROJECT, "budget_run", {"created_at": "2026-08-13T10:00:00+05:30"})

    writer.write("anything")
    assert writer.truncated is True
    records = storage.read_logs(PROJECT, "budget_run")
    assert len(records) == 1
    assert "log capture stopped" in records[0]["message"]


# --------------------------------------------------------------------------------------
# Installation and restoration
# --------------------------------------------------------------------------------------


def test_the_streams_are_restored_after_finish(tmp_path: Path) -> None:
    stdout, stderr = sys.stdout, sys.stderr

    run = start(tmp_path)
    assert sys.stdout is not stdout, "capture did not install"
    assert isinstance(sys.stdout, StreamTee)
    run.finish()

    assert sys.stdout is stdout, "finish() left sys.stdout replaced"
    assert sys.stderr is stderr, "finish() left sys.stderr replaced"


def test_the_streams_are_restored_after_an_exception(tmp_path: Path) -> None:
    stdout, stderr = sys.stdout, sys.stderr

    with pytest.raises(RuntimeError):
        with start(tmp_path) as run:
            print("before the failure")
            raise RuntimeError("boom")

    assert sys.stdout is stdout
    assert sys.stderr is stderr
    storage = Storage(tmp_path)
    records = storage.read_logs(PROJECT, run.id)
    assert "before the failure" in [r["message"] for r in records]
    assert storage.read_run(PROJECT, run.id)["status"] == "failed"

    # A caught exception never hits the excepthook, so the context manager logs the
    # traceback itself before finish() closes the log.
    crash = records[-1]
    assert crash["level"] == "error"
    assert crash["source"] == "stderr"
    assert "RuntimeError: boom" in crash["message"]
    assert "test_logs.py" in crash["message"]


def test_the_last_partial_line_is_recorded_before_the_traceback(tmp_path: Path) -> None:
    """A pending partial line is flushed before the traceback is written."""
    storage = Storage(tmp_path)
    with pytest.raises(ValueError):
        with start(tmp_path, run_id="ordering_run") as run:
            print("progress: 41%", end="")
            raise ValueError("stopped")

    messages = [r["message"] for r in storage.read_logs(PROJECT, run.id)]
    assert messages[-2] == "progress: 41%"
    assert messages[-1].startswith("Traceback")


def test_two_sequential_runs_do_not_corrupt_stdout(tmp_path: Path, capsys) -> None:
    """The second run must wrap the real stdout, not the first run's tee."""
    storage = Storage(tmp_path)
    stdout = sys.stdout

    first = start(tmp_path, run_id="sequential_one")
    print("first run")
    first.finish()
    assert sys.stdout is stdout

    second = start(tmp_path, run_id="sequential_two")
    assert isinstance(sys.stdout, StreamTee)
    assert sys.stdout.stream is stdout, "the second run wrapped the first run's tee"
    print("second run")
    second.finish()
    assert sys.stdout is stdout

    print("after both runs")

    assert [r["message"] for r in storage.read_logs(PROJECT, "sequential_one")] == ["first run"]
    assert [r["message"] for r in storage.read_logs(PROJECT, "sequential_two")] == ["second run"]
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["first run", "second run", "after both runs"]


def test_installation_is_idempotent(tmp_path: Path) -> None:
    run = start(tmp_path)
    tee = sys.stdout

    again = install(run, capture_output=True, capture_logging=True)
    assert sys.stdout is tee, "a second install wrapped the streams twice"

    again()
    assert sys.stdout is tee.stream
    # The run's own uninstall is now a no-op.
    run.finish()
    assert sys.stdout is tee.stream


def test_capture_can_be_turned_off_entirely(tmp_path: Path, capsys) -> None:
    storage = Storage(tmp_path)
    stdout = sys.stdout
    root_handlers = list(logging.getLogger().handlers)

    with start(tmp_path, capture_output=False, capture_logging=False) as run:
        assert sys.stdout is stdout
        assert logging.getLogger().handlers == root_handlers
        print("not captured")
        # log_text is explicit, so it still works.
        run.log_text("captured on purpose", level="warning")

        messages = [r["message"] for r in storage.read_logs(PROJECT, run.id)]

    assert messages == ["captured on purpose"]
    assert "not captured" in capsys.readouterr().out


def test_log_text_refuses_a_finished_run(tmp_path: Path) -> None:
    run = start(tmp_path)
    run.finish()
    with pytest.raises(RuntimeError):
        run.log_text("too late")


@pytest.mark.slow
def test_an_unhandled_exception_restores_the_streams_in_a_real_process(tmp_path: Path) -> None:
    """Excepthook path: traceback reaches the terminal and the log keeps earlier output."""
    result = run_child(
        tmp_path,
        """
        run = met.init(project="logging-project", run_id="crash_logs_run", provenance=False)
        print("printed before the crash")
        raise RuntimeError("boom")
        """,
    )
    assert result.returncode != 0
    assert "printed before the crash" in result.stdout
    assert "RuntimeError: boom" in result.stderr, "the traceback did not reach the terminal"

    messages = [r["message"] for r in read_lines(tmp_path, "crash_logs_run")]
    assert "printed before the crash" in messages
    assert any("RuntimeError: boom" in m for m in messages), (
        "the traceback was printed after the streams were restored"
    )


# --------------------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path):
    from fastapi.testclient import TestClient

    from mlexperimenttracker.server.app import create_app

    storage = Storage(tmp_path)
    _seed(storage, "api_logs_run")
    return TestClient(create_app(storage))


def test_the_logs_route_returns_a_bare_array(client) -> None:
    response = client.get("/api/run/api_logs_run/logs")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 6
    assert body[0]["message"] == "line 0"


def test_the_logs_route_filters_and_pages(client) -> None:
    assert len(client.get("/api/run/api_logs_run/logs?level=info").json()) == 2
    assert len(client.get("/api/run/api_logs_run/logs?limit=2").json()) == 2
    assert client.get("/api/run/api_logs_run/logs?limit=2&offset=4").json()[0]["message"] == "line 4"
    assert client.get("/api/run/api_logs_run/logs?level=nonsense").json() == []


def test_an_unknown_run_is_a_404_with_the_usual_body(client) -> None:
    for path in ("/api/run/missing/logs", "/api/run/missing/logs/download"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"message": "Run not found"}


def test_the_download_route_is_a_plain_text_attachment(client) -> None:
    response = client.get("/api/run/api_logs_run/logs/download")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    assert "api_logs_run_logs.txt" in response.headers["content-disposition"]
    assert response.text.splitlines()[0].endswith("line 0")


def test_a_run_with_no_logs_downloads_an_empty_file(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from mlexperimenttracker.server.app import create_app

    storage = Storage(tmp_path)
    storage.create_run(PROJECT, "silent_run", {"created_at": "2026-08-13T10:00:00+05:30"})
    client = TestClient(create_app(storage))

    assert client.get("/api/run/silent_run/logs").json() == []
    response = client.get("/api/run/silent_run/logs/download")
    assert response.status_code == 200
    assert response.text == ""
