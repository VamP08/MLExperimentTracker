"""Time the dashboard's storage walk as the number of runs grows.

    python scripts/bench_storage.py 100 1000 3000

Generates demo runs into a temp directory, copies them up to each size, and times
``Storage.list_experiments()`` (what ``/api/dashboard`` does). Reports the best of three
passes, so the OS file cache is warm.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from mlexperimenttracker.demo import generate
from mlexperimenttracker.storage import Storage


def main(sizes: list[int]) -> None:
    work = Path(tempfile.mkdtemp())
    try:
        seed_root = work / "seed"
        generate(seed_root, runs=12)
        seeds = [run for project in seed_root.iterdir() for run in project.iterdir()]

        for size in sizes:
            root = work / f"n{size}"
            for i in range(size):
                src = seeds[i % len(seeds)]
                shutil.copytree(src, root / f"project-{i % 10}" / f"{src.name}-{i}")
            storage = Storage(root)
            best = float("inf")
            for _ in range(3):
                start = time.perf_counter()
                storage.list_experiments()
                best = min(best, time.perf_counter() - start)
            print(f"{size:>6} runs  {best * 1000:8.0f} ms")
            shutil.rmtree(root)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main([int(arg) for arg in sys.argv[1:]] or [100, 1000])
