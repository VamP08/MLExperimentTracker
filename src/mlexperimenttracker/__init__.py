"""Local-first experiment tracking: a writer, and a server that reads what it wrote.

Two lines in a training script:

    import mlexperimenttracker as met

    run = met.init(project="cifar10", config={"learning_rate": 3e-4})
    run.log({"loss": loss, "accuracy": acc}, step=step)
    run.finish()

or use it as a context manager, which records failures exactly:

    with met.init(project="cifar10") as run:
        run.log({"loss": 0.3}, step=1)

``init()`` also writes a ``provenance.json`` manifest (commit, uncommitted patch, packages,
environment, command line). Datasets can be hashed into it:

    with met.init(project="cifar10", datasets=["data/train.csv"]) as run:
        run.log_dataset("data/val.csv", name="validation")

Capture never fails the run. ``capture_diff=False`` drops the patch (it can hold secrets),
``provenance=False`` skips the manifest. The SDK is stdlib only; ``psutil`` and ``pynvml``
are optional extras for system metrics.
"""

from __future__ import annotations

from .provenance import hash_path
from .run import Run, init

__version__ = "0.2.1"

__all__ = ["Run", "hash_path", "init", "__version__"]
