"""A believable experiment archive, generated from nothing but a seed.

A fresh clone of this project shows an empty dashboard, because the storage root does
not exist until something writes to it. That is an accurate rendering of no data and a
useless first impression, so this module exists to make ``mlexp demo && mlexp ui`` the
shortest path from clone to a populated UI.

Two constraints shape everything here.

*It has to be deterministic.* Every number comes from a :class:`random.Random` seeded by
the caller and every timestamp is an offset from a fixed base instant, so the same seed
produces byte-identical files on any machine. That makes the generated tree usable as a
committed test fixture (:func:`write_fixture_tree`) rather than only as a demo, and it
means a diff in the fixture is a real change in the writer.

*It has to be plausible, not merely non-empty.* Random noise in a chart is obvious
within seconds and reads worse than no data at all, so the curves are produced by a
small training model rather than by sampling: the learning rate drives how fast loss
decays and whether it diverges, the batch size drives how noisy the curve is, the LR
schedule produces the plateaus, and validation metrics carry an overfitting gap that
opens in the last third of training. The runs also cover every state in the vocabulary,
including the two that only appear when something goes wrong, because status handling is
the part of a dashboard nobody can demonstrate with happy-path data.

Metrics are written twice — per step into ``metrics.jsonl`` and pre-aggregated into
``summary.json`` — because no reader in the product derives an aggregate from the series
(DATA-CONTRACT §4.1). The aggregates here are computed from the values actually written
to disk, so a reviewer who exports the CSV and checks the mean finds the number the UI
showed them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any

from .contract import (
    ARTIFACTS_FILE,
    CHECKPOINTS_DIR,
    CONFIG_FILE,
    FORMAT_VERSION,
    METRICS_FILE,
    SUMMARY_FILE,
    SYSTEM_METRICS_FILE,
    RunState,
)
from .storage import Storage

__all__ = ["generate", "write_fixture_tree"]


# --------------------------------------------------------------------------------------
# Fixed points
# --------------------------------------------------------------------------------------

#: Every run timestamp is this instant plus a whole number of hours. Wall-clock time is
#: never read, which is the whole reason the output is reproducible. The offset is
#: non-UTC on purpose: the reader parses ISO strings with ``new Date``, and a bare local
#: timestamp silently shifts by the viewer's zone, so the demo data exercises the case
#: that has to keep working.
_BASE_TIME = datetime(2026, 8, 5, 9, 14, 3, 482000, tzinfo=timezone(timedelta(hours=5, minutes=30)))

#: How many metric rows per epoch. Enough for a curve to have a shape, few enough that a
#: twelve-epoch run is still a file a human can read.
_POINTS_PER_EPOCH = 4

#: Decay constant of the loss curve, tuned so a well-configured run lands just above its
#: floor by the last epoch rather than converging halfway through.
_DECAY = 6.0

#: Learning rate above this multiple of the project's good rate stops training and starts
#: destroying it. Divergence is inferred from the configured rate rather than declared per
#: run, so the relationship a viewer sees between config and chart is real. The further
#: past the threshold, the sooner the blow-up arrives.
_DIVERGE_RATIO = 8.0
_DIVERGE_START = 0.30
_DIVERGE_RATE = 5.5

#: Gradient noise scales with 1/sqrt(batch), which is why the small-batch runs look ragged
#: and the large-batch runs look smooth. Same reason it does in a real training loop.
_LOSS_NOISE = 0.55
_ACC_NOISE = 0.18

#: Sampling cadence for ``system_metrics.json``. The file is strict JSON and rewritten in
#: full on every tick, and the UI only ever renders the first twenty rows, so a coarse
#: interval costs nothing and keeps the file small.
_SYSTEM_INTERVAL_SECONDS = 30.0
_MAX_SYSTEM_SAMPLES = 40

_TOTAL_MEMORY_MB = 16384


@dataclass(frozen=True)
class _Project:
    """A sweep with a character: a dataset, a model, and the numbers that follow."""

    name: str
    dataset: str
    model: str
    classes: tuple[str, ...]
    train_size: int
    eval_size: int
    good_lr: float
    floor_loss: float
    ceiling_accuracy: float
    ref_batch: int
    step_seconds: float
    startup_seconds: float
    gpu_busy: float
    gpu_memory_per_sample_mb: float
    platform: str
    python_version: str
    working_directory: str
    notes: str
    tags: tuple[str, ...]
    importance_name: str
    features: tuple[str, ...]
    extra_config: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class _RunPlan:
    """One run's identity, hyperparameters and fate.

    ``stop_fraction`` below 1.0 is what makes a failed or interrupted run look like one:
    the series simply ends, mid-epoch, exactly as an append-per-step writer leaves it when
    the process dies.
    """

    project: _Project
    name: str
    state: RunState
    hours: float
    learning_rate: float
    batch_size: int
    epochs: int
    optimizer: str
    weight_decay: float
    dropout: float
    overfit: float
    tags: tuple[str, ...]
    notes: str = ""
    stop_fraction: float = 1.0
    write_summary: bool = True


_CIFAR = _Project(
    name="cifar10-cnn",
    dataset="cifar10",
    model="resnet18",
    classes=(
        "airplane",
        "automobile",
        "bird",
        "cat",
        "deer",
        "dog",
        "frog",
        "horse",
        "ship",
        "truck",
    ),
    train_size=45000,
    eval_size=10000,
    good_lr=0.003,
    floor_loss=0.24,
    ceiling_accuracy=0.936,
    ref_batch=128,
    step_seconds=0.11,
    startup_seconds=11.4,
    gpu_busy=93.0,
    gpu_memory_per_sample_mb=13.0,
    platform="Linux-6.8.0-45-generic-x86_64-with-glibc2.39",
    python_version="3.12.4",
    working_directory="/srv/experiments/vision/cifar10",
    notes="CIFAR-10 sweep: ResNet-18 trained from scratch, step LR schedule, crop and flip augmentation.",
    tags=("vision", "cifar10", "resnet18"),
    importance_name="occlusion_sensitivity",
    features=(
        "region_center",
        "region_mid_left",
        "region_mid_right",
        "region_top_center",
        "region_bottom_center",
        "region_top_left",
        "region_top_right",
        "region_bottom_left",
        "region_bottom_right",
    ),
    extra_config=(
        ("augmentation", "random_crop+hflip"),
        ("image_size", 32),
        ("num_workers", 8),
    ),
)

_AGNEWS = _Project(
    name="agnews-distilbert",
    dataset="ag_news",
    model="distilbert-base-uncased",
    classes=("world", "sports", "business", "sci_tech"),
    train_size=120000,
    eval_size=7600,
    good_lr=0.00003,
    floor_loss=0.16,
    ceiling_accuracy=0.947,
    ref_batch=32,
    step_seconds=0.046,
    startup_seconds=23.8,
    gpu_busy=84.0,
    gpu_memory_per_sample_mb=41.0,
    platform="Windows-11-10.0.26200-SP0",
    python_version="3.12.4",
    working_directory="D:/experiments/text/agnews",
    notes="AG News sweep: DistilBERT fine-tuning, 128-token windows, linear warmup then decay.",
    tags=("nlp", "ag-news", "distilbert"),
    importance_name="attention_head_importance",
    features=(
        "layer6_head4",
        "layer5_head11",
        "layer6_head9",
        "layer4_head2",
        "layer3_head7",
        "layer5_head0",
        "layer2_head10",
        "layer1_head5",
    ),
    extra_config=(
        ("max_seq_length", 128),
        ("warmup_ratio", 0.06),
        ("tokenizer", "distilbert-base-uncased"),
    ),
)


#: Ordered so that the first run of each project carries the sweep description — the
#: experiment description the dashboard shows is the first non-empty ``notes`` it finds
#: (DATA-CONTRACT §3.9), and ``notes`` doubles as that run's display name, so exactly one
#: run per project pays that price and the rest keep their generated names.
_PLANS: tuple[_RunPlan, ...] = (
    _RunPlan(
        project=_CIFAR,
        name="resnet18-bs128-lr3e-3",
        state=RunState.COMPLETED,
        hours=0,
        learning_rate=0.003,
        batch_size=128,
        epochs=10,
        optimizer="sgd_momentum",
        weight_decay=0.0005,
        dropout=0.1,
        overfit=0.17,
        tags=("baseline",),
        notes=_CIFAR.notes,
    ),
    _RunPlan(
        project=_AGNEWS,
        name="distilbert-bs32-lr3e-5",
        state=RunState.COMPLETED,
        hours=5,
        learning_rate=0.00003,
        batch_size=32,
        epochs=3,
        optimizer="adamw",
        weight_decay=0.01,
        dropout=0.1,
        overfit=0.11,
        tags=("baseline",),
        notes=_AGNEWS.notes,
    ),
    _RunPlan(
        project=_CIFAR,
        name="resnet18-bs128-lr5e-2",
        state=RunState.FAILED,
        hours=26,
        learning_rate=0.05,
        batch_size=128,
        epochs=10,
        optimizer="sgd_momentum",
        weight_decay=0.0005,
        dropout=0.1,
        overfit=0.0,
        tags=("sweep", "diverged"),
        stop_fraction=0.55,
    ),
    _RunPlan(
        project=_AGNEWS,
        name="distilbert-bs16-lr5e-5",
        state=RunState.INTERRUPTED,
        hours=31,
        learning_rate=0.00005,
        batch_size=16,
        epochs=4,
        optimizer="adamw",
        weight_decay=0.01,
        dropout=0.15,
        overfit=0.14,
        tags=("sweep", "preempted"),
        stop_fraction=0.68,
    ),
    _RunPlan(
        project=_CIFAR,
        name="resnet18-bs32-lr1e-3",
        state=RunState.COMPLETED,
        hours=49,
        learning_rate=0.001,
        batch_size=32,
        epochs=8,
        optimizer="sgd_momentum",
        weight_decay=0.0005,
        dropout=0.1,
        overfit=0.13,
        tags=("sweep", "small-batch"),
    ),
    _RunPlan(
        project=_AGNEWS,
        name="distilbert-bs64-lr5e-5",
        state=RunState.COMPLETED,
        hours=54,
        learning_rate=0.00005,
        batch_size=64,
        epochs=4,
        optimizer="adamw",
        weight_decay=0.01,
        dropout=0.1,
        overfit=0.2,
        tags=("sweep", "lr-tuned"),
    ),
    _RunPlan(
        project=_CIFAR,
        name="resnet18-bs256-lr6e-3",
        state=RunState.COMPLETED,
        hours=73,
        learning_rate=0.006,
        batch_size=256,
        epochs=12,
        optimizer="sgd_momentum",
        weight_decay=0.0005,
        dropout=0.1,
        overfit=0.22,
        tags=("sweep", "large-batch"),
    ),
    _RunPlan(
        project=_CIFAR,
        name="resnet18-bs128-lr3e-3-cosine",
        state=RunState.RUNNING,
        hours=96,
        learning_rate=0.003,
        batch_size=128,
        epochs=12,
        optimizer="sgd_momentum",
        weight_decay=0.0005,
        dropout=0.1,
        overfit=0.18,
        tags=("sweep", "in-progress"),
        stop_fraction=0.46,
        write_summary=False,
    ),
)

#: A deliberately smaller archive for the test suite: five runs, three epochs at most,
#: still covering all four terminal-or-not states. Small enough to commit and to read in
#: a diff when something about the writer changes.
_FIXTURE_SEED = 7
_FIXTURE_PLANS: tuple[_RunPlan, ...] = (
    _RunPlan(
        project=_CIFAR,
        name="resnet18-bs128-lr3e-3",
        state=RunState.COMPLETED,
        hours=0,
        learning_rate=0.003,
        batch_size=128,
        epochs=3,
        optimizer="sgd_momentum",
        weight_decay=0.0005,
        dropout=0.1,
        overfit=0.12,
        tags=("baseline",),
        notes=_CIFAR.notes,
    ),
    _RunPlan(
        project=_CIFAR,
        name="resnet18-bs128-lr5e-2",
        state=RunState.FAILED,
        hours=6,
        learning_rate=0.05,
        batch_size=128,
        epochs=3,
        optimizer="sgd_momentum",
        weight_decay=0.0005,
        dropout=0.1,
        overfit=0.0,
        tags=("sweep", "diverged"),
        stop_fraction=0.55,
    ),
    _RunPlan(
        project=_CIFAR,
        name="resnet18-bs64-lr2e-3",
        state=RunState.INTERRUPTED,
        hours=12,
        learning_rate=0.002,
        batch_size=64,
        epochs=3,
        optimizer="sgd_momentum",
        weight_decay=0.0005,
        dropout=0.1,
        overfit=0.1,
        tags=("sweep", "preempted"),
        stop_fraction=0.66,
    ),
    _RunPlan(
        project=_AGNEWS,
        name="distilbert-bs32-lr3e-5",
        state=RunState.COMPLETED,
        hours=18,
        learning_rate=0.00003,
        batch_size=32,
        epochs=2,
        optimizer="adamw",
        weight_decay=0.01,
        dropout=0.1,
        overfit=0.09,
        tags=("baseline",),
        notes=_AGNEWS.notes,
    ),
    _RunPlan(
        project=_AGNEWS,
        name="distilbert-bs64-lr5e-5",
        state=RunState.RUNNING,
        hours=24,
        learning_rate=0.00005,
        batch_size=64,
        epochs=3,
        optimizer="adamw",
        weight_decay=0.01,
        dropout=0.1,
        overfit=0.12,
        tags=("sweep", "in-progress"),
        stop_fraction=0.72,
        write_summary=False,
    ),
)


# --------------------------------------------------------------------------------------
# Public surface
# --------------------------------------------------------------------------------------


def generate(storage: Storage | str | Path, runs: int = 8, seed: int = 0) -> list[str]:
    """Write ``runs`` runs across two projects and return their IDs, newest plan last.

    The first eight runs are hand-planned so that every state in the vocabulary appears
    — completed, failed part-way, interrupted and still-running — because the states are
    the part of the UI that cannot be demonstrated with successful runs alone. Beyond
    eight, the archive is extended backwards in time with further sweep points, so the
    hand-planned runs stay the recent ones no matter how many are asked for.

    Re-running with the same arguments over an existing tree overwrites rather than
    appends: the two line-oriented files are truncated first, so the generator is
    idempotent and a demo directory never accumulates duplicate steps.
    """
    store = storage if isinstance(storage, Storage) else Storage(storage)
    rng = Random(seed)
    return [_write_run(store, plan, Random(rng.getrandbits(64))) for plan in _plans(runs)]


def write_fixture_tree(dest: Path) -> None:
    """Write the committed test fixture: five short runs, four states, two projects.

    Separate from :func:`generate` because a fixture answers to different pressures — it
    is read in diffs, so it must stay small, and it must not change when the demo's plan
    list is retuned for a better-looking dashboard. Regenerating it and finding a diff
    means the writer changed, which is exactly the signal the test suite wants.
    """
    store = Storage(dest)
    rng = Random(_FIXTURE_SEED)
    for plan in _FIXTURE_PLANS:
        _write_run(store, plan, Random(rng.getrandbits(64)))


def _plans(runs: int) -> list[_RunPlan]:
    if runs <= 0:
        return []
    if runs <= len(_PLANS):
        return list(_PLANS[:runs])

    plans = list(_PLANS)
    #: Extra runs are older than every hand-planned one, so a large archive reads as a
    #: sweep with history rather than as eight good runs followed by filler.
    lr_grid = (0.0005, 0.0015, 0.004, 0.008, 0.012)
    batch_grid = (32, 64, 128, 256)
    epoch_grid = (6, 8, 10, 12)
    for index in range(runs - len(_PLANS)):
        project = _CIFAR if index % 2 == 0 else _AGNEWS
        learning_rate = lr_grid[index % len(lr_grid)]
        if project is _AGNEWS:
            learning_rate = round(learning_rate * 0.01, 8)
        batch_size = batch_grid[(index // 2) % len(batch_grid)]
        epochs = epoch_grid[index % len(epoch_grid)]
        if project is _AGNEWS:
            epochs = 2 + index % 3
        plans.append(
            _RunPlan(
                project=project,
                name=f"{project.model.split('-')[0]}-bs{batch_size}-lr{learning_rate:g}",
                state=RunState.COMPLETED,
                hours=-24.0 * (index + 1) - 3.0 * (index % 5),
                learning_rate=learning_rate,
                batch_size=batch_size,
                epochs=epochs,
                optimizer="sgd_momentum" if project is _CIFAR else "adamw",
                weight_decay=0.0005 if project is _CIFAR else 0.01,
                dropout=0.1,
                overfit=0.08 + 0.01 * (index % 6),
                tags=("sweep", "grid"),
            )
        )
    return plans


# --------------------------------------------------------------------------------------
# One run, end to end
# --------------------------------------------------------------------------------------


def _write_run(store: Storage, plan: _RunPlan, rng: Random) -> str:
    """Write a whole run in the order a live SDK would: identity, config, steps, then the
    summary last, because the summary is the file that declares the run over."""
    project = plan.project
    created = _BASE_TIME + timedelta(hours=plan.hours)
    created_epoch = created.timestamp()
    #: The ID embeds the project because run IDs are resolved by scanning every project
    #: and taking the first match — a bare counter collides across projects and sends
    #: reads, and edits, to the wrong run (DATA-CONTRACT §2.2).
    run_id = (
        f"{project.name}"
        f"_{created.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"_{rng.randrange(16 ** 4):04x}"
    )
    config_seed = rng.randrange(1000, 9999)

    rows, epoch_ends = _training_series(plan, created_epoch, rng)
    last_elapsed = float(rows[-1]["timestamp"]) if rows else 0.0
    duration = round(last_elapsed + _teardown_seconds(plan.state), 1)

    metadata = {
        "format_version": FORMAT_VERSION,
        "created_at": _iso(created),
        "name": plan.name,
        # `running`, on every run, including the finished ones — because that is the last
        # thing the SDK writes here: it flips this field on the first logged step and
        # records the terminal state in summary.json, which is the file the dashboard
        # counters read. The run that has no summary at all therefore falls back to this
        # field and reads as running, which is both correct and the reason a crashed run
        # reads as running forever.
        "state": RunState.RUNNING.value,
        "tags": list(project.tags + plan.tags),
        "notes": plan.notes,
        "platform": project.platform,
        "python_version": project.python_version,
        "working_directory": project.working_directory,
    }
    run_dir = store.create_run(project.name, run_id, metadata)

    # Both line-oriented files are appended to, so a re-run over an existing tree has to
    # start from empty or the series doubles.
    for name in (METRICS_FILE, ARTIFACTS_FILE):
        _truncate(run_dir / name)

    store.write_json(run_dir / CONFIG_FILE, _config(plan, config_seed))

    for row in rows:
        store.append_jsonl(run_dir / METRICS_FILE, row)

    for filename, sidecar in _checkpoints(plan, rows, epoch_ends, created):
        store.write_json(run_dir / CHECKPOINTS_DIR / f"{filename}.json", sidecar)

    store.write_json(
        run_dir / SYSTEM_METRICS_FILE, _system_samples(plan, created_epoch, duration, rng)
    )

    if plan.state is RunState.COMPLETED:
        finished = created + timedelta(seconds=duration)
        for record in _evaluation_artifacts(plan, rows, finished, rng):
            store.append_jsonl(run_dir / ARTIFACTS_FILE, record)

    if plan.write_summary:
        store.write_json(
            run_dir / SUMMARY_FILE,
            {
                "state": plan.state.value,
                "duration": duration,
                "end_time": _iso(created + timedelta(seconds=duration)),
                "notes": plan.notes,
                "metrics_summary": _metrics_summary(rows),
            },
        )
    return run_id


def _teardown_seconds(state: RunState) -> float:
    """Time between the last logged step and the run ending: a traceback, a signal
    handler's final flush, or a normal shutdown that still has to save a checkpoint."""
    if state is RunState.FAILED:
        return 0.6
    if state is RunState.INTERRUPTED:
        return 1.3
    return 4.2


def _config(plan: _RunPlan, config_seed: int) -> dict[str, Any]:
    """Flat, lowercase, top-level scalars only.

    The comparison table hardcodes ``learning_rate``, ``batch_size`` and ``epochs``
    (DATA-CONTRACT §4.5), and the experiment-side config reader drops nested objects
    entirely rather than flattening them, so anything worth comparing has to be a scalar
    at the top level.
    """
    config: dict[str, Any] = {
        "learning_rate": plan.learning_rate,
        "batch_size": plan.batch_size,
        "epochs": plan.epochs,
        "optimizer": plan.optimizer,
        "weight_decay": plan.weight_decay,
        "lr_schedule": "step",
        "dropout": plan.dropout,
        "dataset": plan.project.dataset,
        "model": plan.project.model,
        "num_classes": len(plan.project.classes),
        "seed": config_seed,
        "device": "cuda:0",
        "mixed_precision": True,
        "gradient_clip": 1.0,
        "early_stopping": None,
    }
    config.update(plan.project.extra_config)
    return config


# --------------------------------------------------------------------------------------
# The training model
# --------------------------------------------------------------------------------------


def _training_series(
    plan: _RunPlan, created_epoch: float, rng: Random
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """Generate the wide metric rows, and the row index at which each epoch ended.

    The curve is not sampled noise. Loss decays as the integral of the LR schedule, so
    the two schedule drops show up as plateaus; the decay rate saturates with learning
    rate, so a higher rate learns faster right up to the point where it destroys the run;
    the noise amplitude falls as 1/sqrt(batch), so small-batch runs are visibly ragged;
    and validation metrics carry an additive penalty that only starts growing past the
    halfway mark, which is what an overfitting gap looks like on a chart.

    Validation metrics appear only on epoch boundaries. Sparse keys are fully supported
    by the reader, and a validation series with its own, shorter x-positions is what real
    logging produces.
    """
    project = plan.project
    class_count = len(project.classes)
    chance = 1.0 / class_count

    ratio = plan.learning_rate / project.good_lr
    penalty = abs(math.log10(ratio)) if ratio > 0 else 3.0
    # Saturating in the rate, then penalised above the sweet spot: a rate ten times too
    # high does not learn ten times faster, it learns worse and then stops learning at
    # all. Both halves matter — without the penalty the diverging run would post the best
    # accuracy in the sweep right up to the step where it explodes.
    speed = (1.6 * ratio / (1.0 + 0.6 * ratio)) / (1.0 + 1.5 * max(0.0, math.log10(ratio)))
    floor = project.floor_loss * (1.0 + 0.9 * penalty)
    ceiling = max(project.ceiling_accuracy * (1.0 - 0.12 * penalty), chance + 0.05)
    # Cross-entropy of a uniform prediction: where a correctly initialised model starts.
    start_loss = math.log(class_count) * 1.04
    diverges = ratio >= _DIVERGE_RATIO
    diverge_start = max(0.10, _DIVERGE_START * _DIVERGE_RATIO / ratio) if diverges else 1.0

    loss_noise = _LOSS_NOISE / math.sqrt(plan.batch_size)
    acc_noise = _ACC_NOISE / math.sqrt(plan.batch_size)

    steps_per_epoch = max(1, project.train_size // plan.batch_size)
    step_seconds = project.step_seconds * (plan.batch_size / project.ref_batch) ** 0.75

    def clean(progress: float) -> tuple[float, float]:
        """Loss and accuracy with no noise, at a given fraction of planned training."""
        schedule = (
            min(progress, 0.5)
            + 0.35 * max(0.0, min(progress, 0.8) - 0.5)
            + 0.12 * max(0.0, progress - 0.8)
        )
        # Each schedule drop takes a bite out of the remaining gap and then flattens it,
        # which is the shape a step LR schedule actually draws.
        remaining = math.exp(-_DECAY * speed * schedule)
        if progress > 0.5:
            remaining *= 0.85
        if progress > 0.8:
            remaining *= 0.85
        return (
            floor + (start_loss - floor) * remaining,
            ceiling - (ceiling - chance) * remaining,
        )

    diverge_loss, diverge_acc = clean(diverge_start)

    rows: list[dict[str, Any]] = []
    epoch_ends: dict[int, int] = {}
    for epoch in range(1, plan.epochs + 1):
        for point in range(1, _POINTS_PER_EPOCH + 1):
            position = (epoch - 1) + point / _POINTS_PER_EPOCH
            progress = position / plan.epochs
            step = int(round(position * steps_per_epoch))
            # Rounded once, then reused, so the two timestamps on a row describe the same
            # instant instead of differing by half a tenth of a second.
            elapsed = round(position * steps_per_epoch * step_seconds + project.startup_seconds, 1)

            loss, accuracy = clean(progress)
            if diverges and progress > diverge_start:
                overshoot = progress - diverge_start
                loss = min(diverge_loss * math.exp(_DIVERGE_RATE * overshoot), 5000.0)
                accuracy = chance + (diverge_acc - chance) * math.exp(-6.0 * overshoot)

            row: dict[str, Any] = {
                "step": step,
                # Relative seconds since the run started, and the same instant as epoch
                # seconds. The reader wants both, under those two names, in that order.
                "timestamp": elapsed,
                "absolute_timestamp": round(created_epoch + elapsed, 2),
                "loss": _clamp_loss(loss * (1.0 + rng.normalvariate(0.0, loss_noise))),
                "accuracy": _clamp_accuracy(accuracy + rng.normalvariate(0.0, acc_noise)),
            }

            if point == _POINTS_PER_EPOCH:
                gap = plan.overfit * (max(0.0, progress - 0.5) / 0.5) ** 2
                row["val_loss"] = _clamp_loss(
                    (loss * 1.05 + gap) * (1.0 + rng.normalvariate(0.0, loss_noise * 0.6))
                )
                row["val_accuracy"] = _clamp_accuracy(
                    accuracy - 0.028 - 0.55 * gap + rng.normalvariate(0.0, acc_noise * 0.6)
                )
                epoch_ends[epoch] = len(rows)
            rows.append(row)

    # A run that died has a file that simply stops, mid-epoch, with no terminal marker in
    # it — there is no in-band way to say "this is where it ended".
    kept = max(1, int(round(len(rows) * plan.stop_fraction)))
    rows = rows[:kept]
    epoch_ends = {epoch: index for epoch, index in epoch_ends.items() if index < kept}
    return rows, epoch_ends


def _clamp_loss(value: float) -> float:
    return round(min(max(value, 0.0001), 9999.0), 4)


def _clamp_accuracy(value: float) -> float:
    return round(min(max(value, 0.0), 0.9995), 4)


def _metrics_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Aggregate every series from the rows that were actually written.

    This is the write-twice rule (DATA-CONTRACT §4.1): no reader computes an aggregate, so
    a metric with a chart and no summary entry shows a blank number, and a summary entry
    without ``latest`` shows nothing at all. Computing the stats here from the same
    rounded values that reached the file is what makes the dashboard's numbers survive a
    reviewer recomputing them from the CSV export.

    ``stddev`` is the **population** standard deviation. Nothing on the read side
    validates the choice, so it is only true because it is written down.
    """
    series: dict[str, list[float]] = {}
    for row in rows:
        for key, value in row.items():
            if key in ("step", "timestamp", "absolute_timestamp"):
                continue
            series.setdefault(key, []).append(float(value))

    summary: dict[str, dict[str, float]] = {}
    for name, values in series.items():
        count = len(values)
        mean = sum(values) / count
        variance = sum((value - mean) ** 2 for value in values) / count
        summary[name] = {
            "latest": values[-1],
            "mean": round(mean, 6),
            "min": min(values),
            "max": max(values),
            "stddev": round(math.sqrt(variance), 6),
        }
    return summary


# --------------------------------------------------------------------------------------
# Checkpoints, system metrics, artifacts
# --------------------------------------------------------------------------------------


def _checkpoints(
    plan: _RunPlan,
    rows: list[dict[str, Any]],
    epoch_ends: dict[int, int],
    created: datetime,
) -> list[tuple[str, dict[str, Any]]]:
    """Sidecars for the last few epochs plus the best one by validation loss.

    Only ``*.json`` files in this directory are listed, and the size the UI reports is the
    sidecar's rather than the weights' — a reader limitation a writer cannot work around.
    Writing the true weight size into the sidecar is free and makes fixing that a one-line
    change on the read side; nothing reads it today.
    """
    if not epoch_ends:
        return []

    ordered = sorted(epoch_ends)
    keep = set(ordered[-3:])
    best_epoch = min(ordered, key=lambda epoch: rows[epoch_ends[epoch]]["val_loss"])

    sidecars: list[tuple[str, dict[str, Any]]] = []
    for epoch in sorted(keep | {best_epoch}):
        row = rows[epoch_ends[epoch]]
        name = f"epoch_{epoch:02d}"
        sidecars.append(
            (
                name,
                {
                    "checkpoint_name": name,
                    "created_at": _iso(created + timedelta(seconds=float(row["timestamp"]) + 0.4)),
                    "step": row["step"],
                    "size_bytes": _weights_bytes(plan),
                },
            )
        )
    best_row = rows[epoch_ends[best_epoch]]
    sidecars.append(
        (
            "best",
            {
                "checkpoint_name": "best",
                "created_at": _iso(created + timedelta(seconds=float(best_row["timestamp"]) + 0.6)),
                "step": best_row["step"],
                "size_bytes": _weights_bytes(plan),
            },
        )
    )
    return sidecars


def _weights_bytes(plan: _RunPlan) -> int:
    """Parameter count times four bytes, near enough for the two models involved."""
    parameters = 11_181_642 if plan.project is _CIFAR else 66_955_010
    return parameters * 4


def _system_samples(
    plan: _RunPlan, created_epoch: float, duration: float, rng: Random
) -> list[dict[str, Any]]:
    """Correlated CPU / memory / GPU traces, as an array of homogeneous samples.

    Homogeneity is load-bearing: the table renders a column only when the field is present
    on the **first** sample, so a GPU that appears mid-run never gets one. The first and
    last samples are deliberately cool — data loading before the first step and teardown
    after the last — which is what makes the mean/max/min cards read as a real run rather
    than as a constant.

    ``timestamp`` here is epoch seconds, the opposite convention to ``metrics.jsonl``'s
    relative seconds. Same key name, two meanings, two files in the same directory.
    """
    interval = _SYSTEM_INTERVAL_SECONDS
    while duration / interval > _MAX_SYSTEM_SAMPLES:
        interval *= 2

    count = max(2, int(duration // interval) + 1)
    gpu_memory = round(512 + plan.batch_size * plan.project.gpu_memory_per_sample_mb)
    samples: list[dict[str, Any]] = []
    for index in range(count):
        offset = index * interval
        warm = min(1.0, offset / max(interval, 1.0))
        cooling = index == count - 1 and duration - offset < interval * 1.5

        if index == 0:
            gpu = rng.uniform(0.0, 4.0)
        elif cooling:
            gpu = plan.project.gpu_busy * rng.uniform(0.10, 0.22)
        else:
            gpu = plan.project.gpu_busy * warm + rng.normalvariate(0.0, 2.4)

        gpu = min(max(gpu, 0.0), 100.0)
        cpu = min(max(28.0 + 0.55 * gpu + rng.normalvariate(0.0, 2.8), 3.0), 100.0)
        memory_percent = min(
            max(43.5 + 11.0 * warm + 0.6 * math.log1p(index) + rng.normalvariate(0.0, 0.5), 5.0),
            99.0,
        )
        used_mb = round(_TOTAL_MEMORY_MB * memory_percent / 100.0)
        samples.append(
            {
                "timestamp": round(created_epoch + offset, 1),
                "cpu_percent": round(cpu, 1),
                "memory_percent": round(memory_percent, 1),
                "memory_used_mb": used_mb,
                "memory_available_mb": _TOTAL_MEMORY_MB - used_mb,
                "gpu_utilization": round(gpu, 1),
                "gpu_memory_used_mb": 312 if index == 0 else gpu_memory,
                "disk_usage_percent": round(71.4 + 0.02 * index, 2),
            }
        )
    return samples


def _evaluation_artifacts(
    plan: _RunPlan, rows: list[dict[str, Any]], finished: datetime, rng: Random
) -> list[dict[str, Any]]:
    """The three payloads the frontend knows how to draw, inlined into ``metadata``.

    There is no path and no URL field in the format, so a visualization either travels
    inside the artifact record or does not reach the UI at all. Every payload here is
    internally consistent — the confusion matrix's accuracy is its own trace over its own
    total, each ROC curve's AUC is the trapezoidal area of the points it ships — because
    the first thing anyone does with a demo dashboard is check whether the numbers agree.

    Only completed runs get these: a run that crashed never reached its evaluation pass,
    and inventing artifacts for it would be the one dishonest thing in the tree.
    """
    project = plan.project
    class_count = len(project.classes)
    final_accuracy = float(rows[-1].get("val_accuracy", rows[-1]["accuracy"]))

    matrix, accuracy, precision, recall, f1 = _confusion_matrix(
        rng, class_count, project.eval_size // class_count, final_accuracy
    )

    records: list[dict[str, Any]] = [
        {
            "name": "confusion_matrix",
            "type": "confusion_matrix",
            "version": "1",
            "created_at": _iso(finished),
            "file_count": 0,
            "metadata": {
                "labels": list(project.classes),
                "matrix": matrix,
                "accuracy": accuracy,
                "precision": precision,
                "recall": recall,
                # The one camelCase key in the entire contract: artifact metadata reaches
                # the component with no key transform, so f1_score would not be found.
                "f1Score": f1,
            },
        }
    ]

    for index, label in enumerate(project.classes):
        fpr, tpr, thresholds, auc = _roc_curve(rng, recall[index])
        records.append(
            {
                "name": f"roc_curve_{label}",
                "type": "roc_curve",
                "version": "1",
                "created_at": _iso(finished + timedelta(milliseconds=60 + 40 * index)),
                "file_count": 0,
                "metadata": {
                    "fpr": fpr,
                    "tpr": tpr,
                    "thresholds": thresholds,
                    "auc": auc,
                    # One record per class; the component groups them by this label.
                    "className": label,
                },
            }
        )

    records.append(
        {
            "name": project.importance_name,
            "type": "feature_importance",
            "version": "1",
            "created_at": _iso(finished + timedelta(milliseconds=60 + 40 * class_count)),
            "file_count": 0,
            "metadata": {"features": _feature_importance(rng, project.features)},
        }
    )
    return records


def _confusion_matrix(
    rng: Random, class_count: int, per_class: int, accuracy: float
) -> tuple[list[list[int]], float, list[float], list[float], list[float]]:
    """A matrix whose own cells produce the accuracy, precision, recall and F1 reported.

    Errors are biased toward neighbouring class indices, which is a stand-in for the real
    thing — cats and dogs, business and sci/tech — and makes the heatmap look like a model
    rather than like a uniform smear.
    """
    matrix = [[0] * class_count for _ in range(class_count)]
    for true_class in range(class_count):
        class_accuracy = min(max(accuracy + rng.normalvariate(0.0, 0.045), 0.05), 0.995)
        correct = min(per_class, max(0, int(round(per_class * class_accuracy))))
        matrix[true_class][true_class] = correct

        remainder = per_class - correct
        if remainder <= 0:
            continue
        weights = [
            0.0
            if other == true_class
            else (1.0 / (1.0 + abs(true_class - other))) * (0.6 + rng.random())
            for other in range(class_count)
        ]
        total_weight = sum(weights)
        exact = [remainder * weight / total_weight for weight in weights]
        counts = [int(value) for value in exact]
        # Largest-remainder apportionment, so the row sums to per_class exactly and the
        # reported accuracy is the matrix's own trace rather than an approximation of it.
        short = remainder - sum(counts)
        order = sorted(
            (other for other in range(class_count) if other != true_class),
            key=lambda other: (-(exact[other] - counts[other]), other),
        )
        for other in order[:short]:
            counts[other] += 1
        for other in range(class_count):
            if other != true_class:
                matrix[true_class][other] = counts[other]

    total = sum(sum(row) for row in matrix)
    correct_total = sum(matrix[index][index] for index in range(class_count))
    precision: list[float] = []
    recall: list[float] = []
    f1: list[float] = []
    for index in range(class_count):
        column = sum(matrix[row][index] for row in range(class_count))
        row_total = sum(matrix[index])
        p = matrix[index][index] / column if column else 0.0
        r = matrix[index][index] / row_total if row_total else 0.0
        precision.append(round(p, 4))
        recall.append(round(r, 4))
        f1.append(round(2 * p * r / (p + r), 4) if (p + r) else 0.0)
    return matrix, round(correct_total / total, 4), precision, recall, f1


#: Where the ROC curve is sampled. Dense near the origin because that is the only part of
#: the curve anyone reads, and it is where a good classifier's shape lives.
_ROC_FPR = (0.0, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)


def _roc_curve(rng: Random, class_recall: float) -> tuple[list[float], list[float], list[float], float]:
    """A power-law ROC shaped by the class's recall, with its AUC measured off the points.

    ``tpr = fpr ** (1/k)`` has area ``k / (k + 1)``, so a target AUC fixes the exponent.
    The AUC written into the payload is then re-measured by the trapezoid rule over the
    sampled points, so it matches the curve the component actually draws rather than the
    ideal it came from.
    """
    target = min(max(0.5 + 0.5 * class_recall**0.7 + rng.normalvariate(0.0, 0.008), 0.55), 0.998)
    k = target / (1.0 - target)

    fpr = [round(value, 4) for value in _ROC_FPR]
    tpr = [round(value ** (1.0 / k), 4) for value in _ROC_FPR]
    thresholds = [round(max(0.0, 1.0 - value**0.35), 4) for value in _ROC_FPR]

    auc = 0.0
    for index in range(1, len(fpr)):
        auc += (fpr[index] - fpr[index - 1]) * (tpr[index] + tpr[index - 1]) / 2.0
    return fpr, tpr, thresholds, round(auc, 4)


def _feature_importance(rng: Random, features: tuple[str, ...]) -> list[dict[str, Any]]:
    """Importances that decay geometrically and sum to one, in descending order."""
    weights = [(0.62**index) * (0.85 + 0.3 * rng.random()) for index in range(len(features))]
    weights.sort(reverse=True)
    total = sum(weights)
    return [
        {
            "name": name,
            "importance": round(weight / total, 4),
            "std": round(weight / total * rng.uniform(0.04, 0.12), 4),
        }
        for name, weight in zip(features, weights, strict=True)
    ]


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    """ISO 8601 with an explicit offset, milliseconds, no microsecond tail.

    The offset is not decorative: the reader parses these with ``new Date``, which reads
    an offsetless timestamp as local time in whatever zone the viewer's machine is in.
    """
    return moment.isoformat(timespec="milliseconds")


def _truncate(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
