"""Tests for the tracking SDK.

Everything here is asserted by reading the run back through :class:`Storage` rather than
by inspecting the SDK's own state, because the SDK's only real contract is the bytes it
leaves on disk and the shapes the dashboard makes of them. A test that asked the Run
object what it thought it had written would pass while the dashboard showed nothing.

The crash-safety tests run in a **subprocess**. There is no way to prove in-process that
an unhandled exception, an atexit hook or a signal produces a terminal state: the hooks
under test are process-global, the test runner installs its own, and the interesting exit
paths end the interpreter. A child process is the only honest observation point.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

import mlexperimenttracker as met
from mlexperimenttracker import run as run_module
from mlexperimenttracker.contract import (
    METRICS_FILE,
    RESERVED_METRIC_KEYS,
    SUMMARY_FILE,
    RunState,
)
from mlexperimenttracker.storage import Storage

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(autouse=True)
def _no_leaked_runs():
    """A run left live would be finished by this process's atexit hook, into a temporary
    directory that no longer exists, at a point where failures are invisible."""
    yield
    for run in list(run_module._ACTIVE.values()):
        try:
            run.finish(RunState.COMPLETED)
        except Exception:
            run_module._ACTIVE.pop(id(run), None)


def start(tmp_path: Path, **kwargs):
    """Signals are captured only in the subprocess tests: installing a SIGINT handler in
    the pytest process would replace the one pytest itself relies on."""
    kwargs.setdefault("capture_signals", False)
    return met.init(storage_path=tmp_path, **kwargs)


def run_child(tmp_path: Path, body: str, prelude: str = "") -> subprocess.CompletedProcess[str]:
    """Execute a training script in a child interpreter against a temporary storage root.

    The root is passed through the environment rather than as an argument, so this also
    exercises the resolution path the server uses. ``prelude`` runs before ``init``, which
    is where a handler has to be installed for the SDK to have anything to chain to.
    """
    script = tmp_path / "child.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SRC)!r})
            import mlexperimenttracker as met
            """
        )
        + textwrap.dedent(prelude)
        + textwrap.dedent(
            """
            run = met.init(project="crash", run_id="crash_run_1")
            run.log({"loss": 1.0}, step=1)
            """
        )
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["EXPERIMENT_STORAGE_PATH"] = str(tmp_path / "store")
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def child_state(tmp_path: Path) -> str | None:
    summary = Storage(tmp_path / "store").read_json(
        tmp_path / "store" / "crash" / "crash_run_1" / SUMMARY_FILE
    )
    return summary.get("state") if isinstance(summary, dict) else None


# --------------------------------------------------------------------------------------
# The lifecycle, read back the way the dashboard reads it
# --------------------------------------------------------------------------------------


def test_full_lifecycle_round_trip(tmp_path: Path) -> None:
    run = start(
        tmp_path,
        project="cifar10",
        name="resnet50-lr3e4",
        config={"learning_rate": 3e-4, "batch_size": 64, "epochs": 30},
        tags=["baseline", "resnet"],
    )
    for step, (loss, accuracy) in enumerate([(1.9, 0.31), (1.5, 0.44), (1.2, 0.57)]):
        run.log({"loss": loss, "accuracy": accuracy}, step=step)
    run.log({"val_loss": 1.4}, step=2)
    run.log_confusion_matrix(["a", "b"], [[8, 2], [1, 9]], accuracy=0.85)
    run.log_checkpoint("epoch_01", step=2)
    run.finish()

    storage = Storage(tmp_path)
    read = storage.read_run("cifar10", run.id)
    assert read is not None

    # Status, timing and identity as the run detail page sees them.
    assert read["status"] == "completed"
    assert read["state"] == "completed"
    assert isinstance(read["duration"], float) and read["duration"] >= 0
    assert read["durationFormatted"].endswith("s")
    assert read["createdAt"] == read["startTime"]
    assert read["endTime"] is not None
    assert read["tags"] == ["baseline", "resnet"]
    assert read["experimentId"] == "cifar10"

    # Parameters arrive camelCased from a flat config, both of the reader's parsers agree.
    assert read["parameters"] == {"learningRate": 3e-4, "batchSize": 64, "epochs": 30}
    assert storage.read_experiment_runs("cifar10")[0]["parameters"] == read["parameters"]

    # Every metric appears as a latest plus four stat siblings — the write-twice rule.
    assert read["metrics"]["loss"] == pytest.approx(1.2)
    assert read["metrics"]["lossMin"] == pytest.approx(1.2)
    assert read["metrics"]["lossMax"] == pytest.approx(1.9)
    assert read["metrics"]["valLoss"] == pytest.approx(1.4)
    assert set(read["metrics"]) == {
        f"{name}{suffix}"
        for name in ("loss", "accuracy", "valLoss")
        for suffix in ("", "Mean", "Max", "Min", "Stddev")
    }

    # ...and simultaneously as chartable series pivoted out of the wide rows.
    series = {entry["name"]: entry["data"] for entry in storage.read_metrics("cifar10", run.id)}
    assert set(series) == {"loss", "accuracy", "val_loss"}
    assert [point["step"] for point in series["loss"]] == [0, 1, 2]
    assert len(series["val_loss"]) == 1

    assert read["artifactsCount"] == 1
    assert len(read["checkpoints"]) == 1
    assert read["checkpoints"][0]["name"] == "epoch_01"

    # The dashboard aggregation counts it exactly once, in the completed bucket.
    experiment = storage.read_experiment("cifar10")
    assert experiment is not None
    assert experiment["stats"]["totalRuns"] == 1
    assert experiment["stats"]["completedRuns"] == 1
    assert experiment["stats"]["successRate"] == "100%"


def test_metadata_is_written_before_init_returns(tmp_path: Path) -> None:
    run = start(tmp_path, project="p1", tags=["a"], notes="")
    metadata = Storage(tmp_path).read_json(run.path / "metadata.json")
    assert metadata is not None
    assert metadata["state"] == RunState.INITIALIZED.value
    assert metadata["format_version"]
    assert isinstance(metadata["tags"], list)
    assert metadata["platform"] and metadata["python_version"] and metadata["working_directory"]
    # An offset is mandatory: a bare local timestamp is parsed in the server's zone.
    assert metadata["created_at"][-6] in "+-" or metadata["created_at"].endswith("Z")

    # Visible to the reader immediately, before a single metric exists.
    read = Storage(tmp_path).read_run("p1", run.id)
    assert read is not None and read["status"] == "running"
    run.finish()


def test_run_id_embeds_the_project_and_is_addressable(tmp_path: Path) -> None:
    run = start(tmp_path, project="churn-mlp")
    assert run.id.startswith("churn-mlp_")
    parts = run.id.rsplit("_", 2)
    assert parts[1].endswith("Z") and len(parts[2]) == 4
    # Two runs in the same second still differ, and neither collides across projects.
    other = start(tmp_path, project="churn-mlp")
    assert other.id != run.id
    assert Storage(tmp_path).find_run(run.id) == ("churn-mlp", run.id)
    run.finish()
    other.finish()


def test_state_flips_to_running_on_the_first_step(tmp_path: Path) -> None:
    run = start(tmp_path, project="p2")
    assert Storage(tmp_path).read_json(run.path / SUMMARY_FILE) is None
    run.log({"loss": 1.0})
    metadata = Storage(tmp_path).read_json(run.path / "metadata.json")
    summary = Storage(tmp_path).read_json(run.path / SUMMARY_FILE)
    assert metadata is not None and metadata["state"] == "running"
    # The dashboard counters read summary.state exclusively, so it has to be there too.
    assert summary is not None and summary["state"] == "running"
    run.finish()


def test_finish_is_idempotent_and_first_state_wins(tmp_path: Path) -> None:
    run = start(tmp_path, project="p3")
    run.log({"loss": 1.0})
    run.finish(RunState.FAILED)
    run.finish(RunState.COMPLETED)
    summary = Storage(tmp_path).read_json(run.path / SUMMARY_FILE)
    assert summary is not None and summary["state"] == "failed"
    with pytest.raises(RuntimeError):
        run.log({"loss": 0.5})


def test_finish_rejects_a_non_terminal_state(tmp_path: Path) -> None:
    run = start(tmp_path, project="p4")
    with pytest.raises(ValueError, match="terminal"):
        run.finish(RunState.RUNNING)
    run.finish()


def test_finish_preserves_a_description_edited_in_the_ui(tmp_path: Path) -> None:
    run = start(tmp_path, project="p5")
    run.log({"loss": 1.0})
    storage = Storage(tmp_path)
    assert storage.update_run_description("p5", run.id, "edited in the dashboard")
    run.finish()
    summary = storage.read_json(run.path / SUMMARY_FILE)
    assert summary is not None
    assert summary["notes"] == "edited in the dashboard"
    assert summary["state"] == "completed"


def test_tags_edited_in_the_ui_survive_the_running_transition(tmp_path: Path) -> None:
    run = start(tmp_path, project="p6", tags=["baseline"])
    storage = Storage(tmp_path)
    assert storage.update_run_tags("p6", run.id, ["baseline", "added-by-user"])
    run.log({"loss": 1.0})
    metadata = storage.read_json(run.path / "metadata.json")
    assert metadata is not None
    assert metadata["tags"] == ["baseline", "added-by-user"]
    run.finish()


# --------------------------------------------------------------------------------------
# Metric names, values and steps
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("reserved", sorted(RESERVED_METRIC_KEYS))
def test_reserved_metric_keys_are_rejected(tmp_path: Path, reserved: str) -> None:
    run = start(tmp_path, project="p7")
    with pytest.raises(ValueError, match="reserved"):
        run.log({reserved: 1.0})
    # Nothing was written: the row is rejected whole, not partially.
    assert not (run.path / METRICS_FILE).exists()
    run.finish()


def test_stat_suffix_warns_once_per_name(tmp_path: Path) -> None:
    run = start(tmp_path, project="p8")
    with pytest.warns(UserWarning, match="collides with a derived statistic"):
        run.log({"loss_mean": 1.0})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run.log({"loss_mean": 0.9})
    assert caught == []
    run.finish()


def test_non_snake_case_metric_name_warns(tmp_path: Path) -> None:
    run = start(tmp_path, project="p9")
    with pytest.warns(UserWarning, match="snake_case"):
        run.log({"valLoss": 1.0})
    run.finish()


def test_non_finite_values_are_dropped_not_written(tmp_path: Path) -> None:
    run = start(tmp_path, project="p10")
    with pytest.warns(UserWarning, match="non-finite"):
        run.log({"loss": float("nan"), "accuracy": 0.5}, step=0)
    run.finish()
    rows = Storage(tmp_path).read_jsonl(run.path / METRICS_FILE)
    # The line survives with the rest of the row intact — a bare NaN would have cost the
    # whole line, and with it the accuracy logged beside it.
    assert len(rows) == 1
    assert "loss" not in rows[0]
    assert rows[0]["accuracy"] == 0.5


def test_a_string_metric_is_refused(tmp_path: Path) -> None:
    run = start(tmp_path, project="p11")
    with pytest.raises(TypeError, match="must be a number"):
        run.log({"loss": "0.5"})
    run.finish()


def test_step_auto_increments_and_follows_an_explicit_step(tmp_path: Path) -> None:
    run = start(tmp_path, project="p12")
    run.log({"loss": 1.0})
    run.log({"loss": 0.9})
    run.log({"loss": 0.8}, step=100)
    run.log({"loss": 0.7})
    run.finish()
    rows = Storage(tmp_path).read_jsonl(run.path / METRICS_FILE)
    assert [row["step"] for row in rows] == [0, 1, 100, 101]
    # Relative seconds, monotonic, and never a date string.
    assert all(isinstance(row["timestamp"], float) for row in rows)
    assert rows[0]["timestamp"] <= rows[-1]["timestamp"]
    assert rows[0]["absolute_timestamp"] > 1_600_000_000


# --------------------------------------------------------------------------------------
# Streaming statistics
# --------------------------------------------------------------------------------------


def test_welford_matches_the_statistics_module(tmp_path: Path) -> None:
    """The four stats the reader recognises, against the standard library, on the series
    from the data contract's worked example. ``stddev`` is the population deviation."""
    series = [1.9124, 1.5507, 1.2038, 1.0114, 0.8331, 0.7402]
    run = start(tmp_path, project="p13")
    for step, value in enumerate(series):
        run.log({"loss": value}, step=step)
    run.finish()

    summary = Storage(tmp_path).read_json(run.path / SUMMARY_FILE)
    assert summary is not None
    stats = summary["metrics_summary"]["loss"]
    assert stats["latest"] == pytest.approx(series[-1])
    assert stats["mean"] == pytest.approx(statistics.fmean(series), rel=1e-12)
    assert stats["min"] == pytest.approx(min(series))
    assert stats["max"] == pytest.approx(max(series))
    assert stats["stddev"] == pytest.approx(statistics.pstdev(series), rel=1e-12)
    # The fixture in the data contract rounds these to four places; the same numbers.
    assert round(stats["mean"], 4) == 1.2086
    assert round(stats["stddev"], 4) == 0.4106


def test_welford_survives_a_series_that_destroys_a_naive_accumulator(tmp_path: Path) -> None:
    """The reason for Welford rather than count/sum/sum-of-squares.

    Large values with small variance are exactly where ``E[x²] - E[x]²`` cancels away its
    own significant digits — and exactly the shape of a loss curve that has converged, or
    of any metric logged as a running total.
    """
    series = [1e9 + value for value in (1.9124, 1.5507, 1.2038, 1.0114, 0.8331, 0.7402)]
    exact = statistics.pstdev(series)

    run = start(tmp_path, project="p13b")
    for step, value in enumerate(series):
        run.log({"loss": value}, step=step)
    run.finish()
    summary = Storage(tmp_path).read_json(run.path / SUMMARY_FILE)
    welford = summary["metrics_summary"]["loss"]["stddev"]

    total = sum(series)
    total_squares = sum(value * value for value in series)
    naive_variance = total_squares / len(series) - (total / len(series)) ** 2

    assert welford == pytest.approx(exact, rel=1e-6)
    # The naive form does not merely lose digits here; it produces a number of the wrong
    # order of magnitude, or a negative variance it cannot take the root of.
    assert naive_variance < 0 or abs(naive_variance**0.5 - exact) > exact * 0.1


def test_stddev_of_a_constant_series_is_zero(tmp_path: Path) -> None:
    run = start(tmp_path, project="p14")
    for step in range(5):
        run.log({"loss": 0.5}, step=step)
    run.finish()
    summary = Storage(tmp_path).read_json(run.path / SUMMARY_FILE)
    assert summary is not None
    assert summary["metrics_summary"]["loss"]["stddev"] == 0.0


def test_sparse_metrics_aggregate_over_their_own_points(tmp_path: Path) -> None:
    run = start(tmp_path, project="p15")
    run.log({"loss": 1.0, "val_loss": 2.0}, step=0)
    run.log({"loss": 0.0}, step=1)
    run.finish()
    summary = Storage(tmp_path).read_json(run.path / SUMMARY_FILE)
    assert summary is not None
    assert summary["metrics_summary"]["loss"]["mean"] == pytest.approx(0.5)
    assert summary["metrics_summary"]["val_loss"]["mean"] == pytest.approx(2.0)


def test_summary_is_flushed_periodically_not_per_step(tmp_path: Path) -> None:
    run = start(tmp_path, project="p16", summary_interval=1000.0)
    run.log({"loss": 1.0})
    first = Storage(tmp_path).read_json(run.path / SUMMARY_FILE)
    run.log({"loss": 0.1})
    second = Storage(tmp_path).read_json(run.path / SUMMARY_FILE)
    assert first is not None and second is not None
    # The second step is in metrics.jsonl but not yet in the summary...
    assert second["metrics_summary"]["loss"]["latest"] == pytest.approx(1.0)
    run.finish()
    # ...and finish() is what guarantees the final numbers regardless of cadence.
    final = Storage(tmp_path).read_json(run.path / SUMMARY_FILE)
    assert final is not None
    assert final["metrics_summary"]["loss"]["latest"] == pytest.approx(0.1)


# --------------------------------------------------------------------------------------
# Crash safety — the property the format cannot recover without
# --------------------------------------------------------------------------------------


def test_context_manager_records_failure(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with start(tmp_path, project="p17") as run:
            run.log({"loss": 1.0}, step=0)
            raise RuntimeError("boom")

    read = Storage(tmp_path).read_run("p17", run.id)
    assert read is not None
    assert read["state"] == "failed"
    assert read["status"] == "failed"
    # Metrics logged before the crash survive, aggregates included.
    assert read["metrics"]["loss"] == pytest.approx(1.0)


def test_context_manager_records_completion(tmp_path: Path) -> None:
    with start(tmp_path, project="p18") as run:
        run.log({"loss": 1.0})
    assert Storage(tmp_path).read_run("p18", run.id)["state"] == "completed"


def test_context_manager_records_a_keyboard_interrupt_as_interrupted(tmp_path: Path) -> None:
    with pytest.raises(KeyboardInterrupt):
        with start(tmp_path, project="p19") as run:
            raise KeyboardInterrupt
    read = Storage(tmp_path).read_run("p19", run.id)
    assert read["state"] == "interrupted"
    # ...which the dashboard files under "archived", its only vocabulary for a kill.
    assert read["status"] == "archived"


def test_unhandled_exception_lands_in_failed_not_running(tmp_path: Path) -> None:
    """The single most valuable correctness property of the SDK.

    Without the excepthook this run reads as ``running`` forever: there is no heartbeat and
    no staleness rule in the format, so nothing will ever correct it.
    """
    result = run_child(tmp_path, "raise RuntimeError('training exploded')")
    assert result.returncode != 0
    assert "training exploded" in result.stderr
    assert child_state(tmp_path) == "failed"


def test_a_script_that_forgets_to_finish_still_terminates_the_run(tmp_path: Path) -> None:
    result = run_child(tmp_path, "pass")
    assert result.returncode == 0, result.stderr
    assert child_state(tmp_path) == "completed"


def test_a_signal_lands_in_interrupted_and_the_signal_still_propagates(tmp_path: Path) -> None:
    result = run_child(
        tmp_path,
        """
        import signal
        signal.raise_signal(signal.SIGINT)
        """,
    )
    assert child_state(tmp_path) == "interrupted"
    # Chained, not swallowed: the default handler still raised KeyboardInterrupt.
    assert result.returncode != 0
    assert "KeyboardInterrupt" in result.stderr


def test_a_previously_installed_signal_handler_is_chained_to(tmp_path: Path) -> None:
    """The application's own handler must still run, and still decide the exit.

    A tracker that replaced it would change how the program behaves under Ctrl-C, which is
    a far worse trade than losing a run's status.
    """
    result = run_child(
        tmp_path,
        "signal.raise_signal(signal.SIGINT)",
        prelude="""
        import signal, sys

        def mine(signum, frame):
            print("user handler ran")
            sys.exit(0)

        signal.signal(signal.SIGINT, mine)
        """,
    )
    assert "user handler ran" in result.stdout
    assert result.returncode == 0
    assert child_state(tmp_path) == "interrupted"


def test_crash_metrics_survive_with_their_aggregates(tmp_path: Path) -> None:
    run_child(
        tmp_path,
        """
        run.log({"loss": 0.5}, step=2)
        raise ValueError("late failure")
        """,
    )
    read = Storage(tmp_path / "store").read_run("crash", "crash_run_1")
    assert read is not None
    assert read["state"] == "failed"
    assert read["metrics"]["loss"] == pytest.approx(0.5)
    assert read["metrics"]["lossMax"] == pytest.approx(1.0)
    assert len(read["metricsHistory"]) == 2


# --------------------------------------------------------------------------------------
# Artifacts — the payloads the React components hardcode
# --------------------------------------------------------------------------------------


def test_confusion_matrix_payload_uses_camel_case_f1(tmp_path: Path) -> None:
    run = start(tmp_path, project="p20")
    run.log_confusion_matrix(
        ["retained", "churned"],
        [[812, 74], [63, 251]],
        accuracy=0.8858,
        precision=[0.928, 0.7723],
        recall=[0.9165, 0.7994],
        f1_score=[0.9222, 0.7856],
    )
    run.finish()

    artifact = Storage(tmp_path).read_artifacts("p20", run.id)[0]
    assert artifact["type"] == "confusion_matrix"
    assert artifact["version"] == "1"
    payload = artifact["metadata"]
    # The one camelCase key in the contract; the component reads metadata with no
    # transform, so f1_score would simply not be found.
    assert "f1Score" in payload and "f1_score" not in payload
    assert payload["labels"] == ["retained", "churned"]
    # Counts stay whole so the UI does not render 812.0.
    assert payload["matrix"] == [[812, 74], [63, 251]]
    assert payload["accuracy"] == pytest.approx(0.8858)


def test_confusion_matrix_shape_is_validated(tmp_path: Path) -> None:
    run = start(tmp_path, project="p21")
    with pytest.raises(ValueError, match="2x2"):
        run.log_confusion_matrix(["a", "b"], [[1, 2, 3], [4, 5, 6]])
    with pytest.raises(ValueError, match="one value per label"):
        run.log_confusion_matrix(["a", "b"], [[1, 2], [3, 4]], precision=[0.5])
    run.finish()


def test_roc_curve_is_one_artifact_line_per_class(tmp_path: Path) -> None:
    run = start(tmp_path, project="p22")
    for class_name, auc in (("retained", 0.91), ("churned", 0.87)):
        run.log_roc_curve([0.0, 0.5, 1.0], [0.0, 0.8, 1.0], [1.0, 0.5, 0.0], auc, class_name)
    run.finish()

    artifacts = Storage(tmp_path).read_artifacts("p22", run.id)
    assert [a["name"] for a in artifacts] == ["roc_curve_retained", "roc_curve_churned"]
    assert {a["metadata"]["className"] for a in artifacts} == {"retained", "churned"}
    assert all(a["type"] == "roc_curve" for a in artifacts)
    assert artifacts[0]["metadata"]["auc"] == pytest.approx(0.91)


def test_roc_curve_rejects_mismatched_arrays(tmp_path: Path) -> None:
    run = start(tmp_path, project="p23")
    with pytest.raises(ValueError, match="same length"):
        run.log_roc_curve([0.0, 1.0], [0.0], [1.0, 0.0], 0.5)
    run.finish()


def test_feature_importance_accepts_three_shapes_and_sorts(tmp_path: Path) -> None:
    run = start(tmp_path, project="p24")
    run.log_feature_importance({"tenure": 0.2, "charges": 0.5}, name="from_mapping")
    run.log_feature_importance([("tenure", 0.2, 0.01), ("charges", 0.5)], name="from_tuples")
    run.log_feature_importance(
        [{"name": "tenure", "importance": 0.2, "std": 0.01}], name="from_dicts"
    )
    run.finish()

    artifacts = {a["name"]: a["metadata"] for a in Storage(tmp_path).read_artifacts("p24", run.id)}
    assert [f["name"] for f in artifacts["from_mapping"]["features"]] == ["charges", "tenure"]
    assert artifacts["from_tuples"]["features"][1]["std"] == pytest.approx(0.01)
    assert "std" not in artifacts["from_tuples"]["features"][0]
    assert artifacts["from_dicts"]["features"][0]["importance"] == pytest.approx(0.2)


def test_a_free_form_artifact_passes_metadata_through_verbatim(tmp_path: Path) -> None:
    run = start(tmp_path, project="p25")
    run.log_artifact("weights", "model", {"anyKey": [1, 2], "nested": {"a": 1}}, version="3")
    run.finish()
    artifact = Storage(tmp_path).read_artifacts("p25", run.id)[0]
    assert artifact["_id"] == "weights_3"
    assert artifact["metadata"] == {"anyKey": [1, 2], "nested": {"a": 1}}


# --------------------------------------------------------------------------------------
# Checkpoints
# --------------------------------------------------------------------------------------


def test_checkpoint_sidecar_records_the_real_weight_size(tmp_path: Path) -> None:
    weights = tmp_path / "epoch_02.pt"
    weights.write_bytes(b"\x00" * 4096)

    run = start(tmp_path, project="p26")
    sidecar = run.log_checkpoint("epoch_02", step=782, path=weights)
    run.finish()

    payload = Storage(tmp_path).read_json(sidecar)
    assert payload is not None
    assert payload["checkpoint_name"] == "epoch_02"
    assert payload["step"] == 782
    # The server reports the sidecar's size today; the true number is written for the fix.
    assert payload["size_bytes"] == 4096

    listed = Storage(tmp_path).read_checkpoints("p26", run.id)
    assert len(listed) == 1 and listed[0]["step"] == 782


def test_checkpoints_are_listed_newest_first(tmp_path: Path) -> None:
    run = start(tmp_path, project="p27")
    run.log_checkpoint("epoch_01", step=1)
    run.log_checkpoint("epoch_02", step=2)
    run.finish()
    listed = Storage(tmp_path).read_checkpoints("p27", run.id)
    assert [c["name"] for c in listed] == ["epoch_02", "epoch_01"]


def test_a_checkpoint_name_that_is_not_a_filename_is_refused(tmp_path: Path) -> None:
    run = start(tmp_path, project="p28")
    with pytest.raises(ValueError, match="single path component"):
        run.log_checkpoint("../escape", step=1)
    run.finish()


# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------


def test_config_is_flattened_to_scalars_both_readers_can_see(tmp_path: Path) -> None:
    run = start(
        tmp_path,
        project="p29",
        config={
            "learning_rate": 3e-4,
            "model": {"encoder": {"layers": 12}},
            "hidden sizes": [256, 256],
            "mixed-Precision": True,
            "early_stopping": None,
            "device": "cuda:0",
        },
    )
    run.finish()

    config = Storage(tmp_path).read_json(run.path / "config.json")
    assert config == {
        "learning_rate": 3e-4,
        # Depth two would be discarded entirely by the run-detail reader.
        "model_encoder_layers": 12,
        # A list would flatten to hiddenSizes0/hiddenSizes1 and vanish in the comparison table.
        "hidden_sizes": "256,256",
        "mixed_precision": True,
        "early_stopping": None,
        "device": "cuda:0",
    }
    # Both of the reader's disagreeing config parsers now see the same twelve-ish keys.
    read = Storage(tmp_path).read_run("p29", run.id)
    assert read["parameters"] == Storage(tmp_path).read_experiment_runs("p29")[0]["parameters"]


def test_config_keys_that_would_collide_in_the_ui_warn_before_they_are_written(
    tmp_path: Path,
) -> None:
    with pytest.warns(UserWarning, match="set twice"):
        run = start(tmp_path, project="p30", config={"learning_rate": 1, "learning-rate": 2})
    run.finish()


def test_no_config_means_no_config_file(tmp_path: Path) -> None:
    run = start(tmp_path, project="p31")
    run.finish()
    assert not (run.path / "config.json").exists()


# --------------------------------------------------------------------------------------
# Naming and containment
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../evil", "a/b", "a\\b", "", ".hidden", "with space", "C:evil"])
def test_unaddressable_project_names_are_refused_at_init(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        start(tmp_path, project=bad)


def test_a_case_variant_project_adopts_the_existing_directory(tmp_path: Path) -> None:
    first = start(tmp_path, project="MyProject")
    first.finish()
    second = start(tmp_path, project="myproject")
    second.finish()
    # One project on every platform, not one on Windows and two on Linux.
    assert Storage(tmp_path).list_projects() == ["MyProject"]
    assert second.project == "MyProject"


def test_an_explicit_run_id_is_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="run id"):
        start(tmp_path, project="p32", run_id="../escape")


# --------------------------------------------------------------------------------------
# System metrics
# --------------------------------------------------------------------------------------


class _FakeMemory:
    percent = 52.6
    used = 8_340 * 1024 * 1024
    available = 8_044 * 1024 * 1024


class _FakeDisk:
    percent = 71.4


class _FakePsutil:
    @staticmethod
    def cpu_percent(interval=None):  # noqa: ANN001 - mirrors the psutil signature
        return 76.25

    @staticmethod
    def virtual_memory():
        return _FakeMemory()

    @staticmethod
    def disk_usage(path):  # noqa: ANN001
        return _FakeDisk()


def test_system_metrics_are_written_as_shape_a(tmp_path: Path, monkeypatch) -> None:
    """Shape A is an array of homogeneous samples; anything else renders as unavailable."""
    monkeypatch.setattr("mlexperimenttracker.system._psutil_cache", _FakePsutil)
    run = start(tmp_path, project="p33", system_metrics=True, system_metrics_interval=0.5)
    run.log({"loss": 1.0})
    run.finish()

    samples = json.loads((run.path / "system_metrics.json").read_text(encoding="utf-8"))
    assert isinstance(samples, list) and samples
    first = samples[0]
    assert first["cpu_percent"] == pytest.approx(76.2, abs=0.1)
    assert first["memory_percent"] == pytest.approx(52.6)
    assert first["memory_used_mb"] == 8340
    assert first["memory_available_mb"] == 8044
    assert first["disk_usage_percent"] == pytest.approx(71.4)
    # Epoch seconds here, unlike the relative seconds in metrics.jsonl.
    assert first["timestamp"] > 1_600_000_000
    # Homogeneous: the reader picks its columns from the first sample alone.
    assert all(sample.keys() == first.keys() for sample in samples)
    # And the reader hands it to the component untouched.
    assert Storage(tmp_path).read_system_metrics("p33", run.id) == samples


def test_system_metrics_degrade_to_nothing_without_psutil(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("mlexperimenttracker.system._psutil_cache", None)
    monkeypatch.setattr("mlexperimenttracker.system._GpuProbe.open", staticmethod(lambda: None))
    run = start(tmp_path, project="p34", system_metrics=True)
    run.log({"loss": 1.0})
    run.finish()
    # No file at all rather than an empty array: the run itself is unaffected.
    assert not (run.path / "system_metrics.json").exists()
    assert Storage(tmp_path).read_run("p34", run.id)["state"] == "completed"


def test_system_metrics_are_off_unless_asked_for(tmp_path: Path) -> None:
    run = start(tmp_path, project="p35")
    run.log({"loss": 1.0})
    run.finish()
    assert not (run.path / "system_metrics.json").exists()


# --------------------------------------------------------------------------------------
# Public surface
# --------------------------------------------------------------------------------------


def test_the_public_surface_stays_small() -> None:
    """``hash_path`` joined the surface with format 1.1: a dataset digest is worth
    computing without a run — to check what is on a machine before starting one — and it
    is the only part of provenance capture a caller has a reason to reach directly."""
    assert met.__all__ == ["Run", "hash_path", "init", "__version__"]
    assert met.__version__
    assert callable(met.init)
    assert callable(met.hash_path)
