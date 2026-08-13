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

The public surface is deliberately three names. Everything else — the on-disk contract,
the storage layer, the resource sampler — is an implementation detail of a format that is
specified in ``docs/DATA-CONTRACT.md`` and read by a server, not by user code.

The SDK half has no dependencies beyond the standard library, so adding it to a training
environment cannot break that environment's dependency resolution. ``psutil`` and
``pynvml`` are optional extras used only when system-metric sampling is turned on.
"""

from __future__ import annotations

from .run import Run, init

__version__ = "0.1.0"

__all__ = ["Run", "init", "__version__"]
