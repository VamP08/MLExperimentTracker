"""Local-first experiment tracking: a writer, and a server that reads what it wrote.

Two lines in a training script:

    import mlexperimenttracker as met

    run = met.init(project="cifar10", config={"learning_rate": 3e-4})
    run.log({"loss": loss, "accuracy": acc}, step=step)
    run.finish()

or, preferably, the context-manager form — which is the only exit path that knows *why*
the run ended, and therefore the only one that records failure exactly rather than by
inference:

    with met.init(project="cifar10") as run:
        run.log({"loss": 0.3}, step=1)

Every run also records **what it ran against**, because a metric nobody can reproduce is a
number rather than a result. ``init()`` writes a ``provenance.json`` manifest — the commit,
the uncommitted diff as a patch beside it, the resolved package versions, the machine, the
allowlisted environment and the command line — and datasets are hashed into it on request:

    with met.init(project="cifar10", datasets=["data/train.csv"]) as run:
        run.log_dataset("data/val.csv", name="validation")

That capture is on by default and never fails the run: a missing ``git`` binary, a
directory that is not a repository or an unwritable manifest costs the record, not the
training job. Two flags exist because the diff of a dirty tree can hold a secret —
``capture_diff=False`` keeps the manifest and drops the patch, ``provenance=False`` writes
neither — and :func:`hash_path` is exported for hashing a dataset without a run at all.

Everything else — the on-disk contract, the storage layer, the resource sampler — is an
implementation detail of a format that is specified in ``DATA-CONTRACT.md`` and read by a
server, not by user code.

The SDK half has no dependencies beyond the standard library, so adding it to a training
environment cannot break that environment's dependency resolution. ``psutil`` and
``pynvml`` are optional extras used only when system-metric sampling is turned on.
"""

from __future__ import annotations

from .provenance import hash_path
from .run import Run, init

__version__ = "0.1.0"

__all__ = ["Run", "hash_path", "init", "__version__"]
