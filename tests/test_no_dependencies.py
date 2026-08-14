"""The base install must import and run with no third-party package available.

This is the property that makes the tracker free to add to a training script, and it is
the easiest one to lose: a single convenience import of ``requests`` or ``pydantic`` at the
top of a module breaks it silently, because the development environment has every optional
extra installed and nothing fails locally.

Checking imports by reading the source is not enough — a transitive import three modules
deep would pass that check. So the property is tested the only way that actually holds it:
a subprocess where the third-party packages are made genuinely unimportable, driving a full
run lifecycle end to end.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

#: Everything the package may only use behind an optional extra. fastapi/uvicorn are the
#: server extra, psutil/nvidia-ml-py the system extra; the rest are their transitive
#: dependencies, blocked too so an accidental import cannot be satisfied indirectly.
BLOCKED = (
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "pydantic_core",
    "psutil",
    "pynvml",
    "nvidia_ml_py",
    "httpx",
    "anyio",
    "numpy",
    "pandas",
    "requests",
    "yaml",
)

_SCRIPT = textwrap.dedent(
    """
    import sys, json, tempfile, pathlib

    BLOCKED = {blocked!r}

    class _Blocker:
        def find_module(self, name, path=None):
            return self.find_spec(name, path)

        def find_spec(self, name, path=None, target=None):
            root = name.split(".")[0]
            if root in BLOCKED:
                raise ImportError(
                    "blocked by the zero-dependency test: " + name
                )
            return None

    # Purge anything already imported so the block cannot be bypassed by a warm cache.
    for mod in list(sys.modules):
        if mod.split(".")[0] in BLOCKED or mod.startswith("mlexperimenttracker"):
            del sys.modules[mod]

    sys.meta_path.insert(0, _Blocker())

    # Prove the blocker actually blocks, so a green test cannot mean "nothing was tested".
    try:
        import fastapi
    except ImportError:
        pass
    else:
        raise SystemExit("blocker is not working: fastapi imported")

    import mlexperimenttracker as met
    from mlexperimenttracker.storage import Storage
    from mlexperimenttracker import provenance

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        run = met.init(
            project="zero-dep",
            name="smoke",
            config={{"learning_rate": 0.001, "batch_size": 32, "epochs": 3}},
            tags=["smoke"],
            storage_path=str(root),
        )
        for step in range(5):
            run.log({{"loss": 1.0 / (step + 1), "accuracy": 0.5 + step * 0.1}}, step=step)
        # Output capture is on by default, so this exercises the tee, the logging handler
        # and the log writer as well as recording a line.
        print("epoch 1/1 done")
        run.log_text("explicit line", level="warning")
        run.log_confusion_matrix(labels=["a", "b"], matrix=[[5, 1], [2, 4]], accuracy=0.75)
        run.log_checkpoint("epoch_4", step=4)
        run.finish()

        storage = Storage(root)
        project, run_id = storage.find_run(run.id)
        read = storage.read_run(project, run_id)

        assert read is not None, "run did not read back"
        assert read["status"] == "completed", read["status"]
        assert read["metrics"]["loss"] == 0.2, read["metrics"]
        assert len(storage.read_metrics(project, run_id)) == 2, "expected loss and accuracy"
        assert len(storage.read_artifacts(project, run_id)) == 1
        assert len(storage.read_checkpoints(project, run_id)) == 1

        logged = storage.read_logs(project, run_id)
        assert "epoch 1/1 done" in [record["message"] for record in logged], logged
        warned = storage.read_logs(project, run_id, level="warning")
        assert "explicit line" in [record["message"] for record in warned], warned
        assert storage.read_logs_text(project, run_id).count("\\n") >= len(logged)

        # Provenance capture is the newest place a convenience dependency could creep in:
        # it wants a git library, a hashing helper and an NVML binding, and all three are
        # stdlib or optional here. pynvml is blocked above, so this also drives the
        # GPU-absent path.
        manifest, patch = provenance.capture(str(root))
        assert manifest.python["version"], "python block is empty"
        assert isinstance(manifest.hardware["gpus"], list)
        assert storage.write_provenance(project, run_id, manifest.to_dict(), patch)
        assert storage.read_provenance(project, run_id)["captured_at"]

    print("ZERO_DEPENDENCY_OK")
    """
).format(blocked=BLOCKED)


@pytest.mark.slow
def test_the_sdk_runs_with_every_third_party_package_blocked(tmp_path: Path) -> None:
    script = tmp_path / "zero_dep_check.py"
    script.write_text(_SCRIPT, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, (
        "the SDK could not run without third-party packages.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "ZERO_DEPENDENCY_OK" in proc.stdout, proc.stdout
