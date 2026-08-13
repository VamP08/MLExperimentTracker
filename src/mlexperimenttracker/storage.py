"""Every filesystem access in the product, read and write, in one module.

Two reasons it is one module rather than several. First, containment: user-supplied
project names and run IDs arrive from URL path segments, and a traversal check is only
worth anything if there is exactly one place that can open a file — so
:meth:`Storage.resolve_within` is a choke point, not a utility. Second, parity: the read
methods reproduce the shapes the existing React frontend already consumes, including the
shapes that are defects, and keeping them adjacent makes the divergences visible instead
of scattering them across the codebase.

The reading half never raises. Missing and malformed degrade to ``None`` / ``[]`` / ``{}``
exactly as the reader they replace does, because the frontend has no error path for most
of these calls — an exception surfaces as a blank page, a degraded value surfaces as a
blank field.
"""

from __future__ import annotations

import json
import math
import ntpath
import os
import posixpath
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .contract import (
    ARTIFACTS_DIR,
    ARTIFACTS_FILE,
    CHECKPOINTS_DIR,
    CONFIG_DROP_KEYS,
    CONFIG_FILE,
    DEFAULT_STORAGE_DIRNAME,
    METADATA_FILE,
    METRICS_FILE,
    PATCH_FILE,
    PROVENANCE_FILE,
    RESERVED_METRIC_KEYS,
    STAT_KEYS,
    STORAGE_ENV_VAR,
    SUMMARY_FILE,
    SYSTEM_METRICS_FILE,
    camel_case,
    format_duration,
    format_duration_no_hours,
    map_state,
)

__all__ = ["Storage", "StorageError"]


class StorageError(Exception):
    """Raised only by the writing half. Reads degrade instead of raising."""


class Storage:
    """Rooted access to the experiment tree.

    The root is resolved once, at construction, so that a later ``chdir`` or a mutated
    environment cannot move it out from under an in-flight run.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        if root is None:
            env_root = os.environ.get(STORAGE_ENV_VAR)
            if env_root:
                root = env_root
            else:
                # The reader consults USERPROFILE then HOME; Path.home() covers both, and
                # raises rather than silently writing to a relative path when neither is set.
                root = Path.home() / DEFAULT_STORAGE_DIRNAME
        self.root: Path = Path(root).expanduser().resolve()

    def __repr__(self) -> str:
        return f"Storage(root={str(self.root)!r})"

    # ----------------------------------------------------------------------------------
    # Containment — a security boundary, not a convenience
    # ----------------------------------------------------------------------------------

    def resolve_within(self, *segments: str) -> Path | None:
        """Join ``segments`` under the root, or return ``None`` if they do not belong there.

        Path parameters reach this function already URL-decoded, so ``%2f`` is a real
        separator by the time it is seen. Each segment must therefore be a single path
        component: no separator of either flavour, no drive prefix, no ``.`` or ``..``,
        no NUL, nothing absolute. The resolved-prefix check afterwards is defence in
        depth for whatever the per-segment rules did not anticipate.

        Returns ``None`` rather than raising, because every caller treats an
        unaddressable name as "not found" — that is what makes the check impossible to
        forget at a call site.
        """
        for segment in segments:
            if not isinstance(segment, str) or not segment:
                return None
            if segment in (".", ".."):
                return None
            if "\0" in segment:
                return None
            if "/" in segment or "\\" in segment:
                return None
            # Both flavours are checked on every platform: a POSIX server can be handed a
            # Windows-shaped payload, and posixpath would happily call "C:foo" a filename.
            if ntpath.isabs(segment) or posixpath.isabs(segment):
                return None
            if ntpath.splitdrive(segment)[0]:
                return None

        try:
            target = Path(self.root, *segments).resolve()
        except (OSError, ValueError):
            # A name the platform cannot even resolve is not addressable either, and this
            # function is the one place that must never raise into a caller.
            return None
        if target != self.root and self.root not in target.parents:
            return None
        return target

    def project_path(self, project: str) -> Path | None:
        return self.resolve_within(project)

    def run_path(self, project: str, run_id: str) -> Path | None:
        return self.resolve_within(project, run_id)

    # ----------------------------------------------------------------------------------
    # Discovery
    # ----------------------------------------------------------------------------------

    def list_projects(self) -> list[str]:
        """Immediate subdirectories of the root. Loose files at the root are invisible,
        which is what makes a project a directory and nothing else."""
        return self._list_subdirectories(self.root)

    def list_runs(self, project: str) -> list[str]:
        """Immediate subdirectories of a project. There is no third level — a run nested
        under a date or sweep folder cannot be seen by the reader at all."""
        project_dir = self.project_path(project)
        if project_dir is None:
            return []
        return self._list_subdirectories(project_dir)

    def list_all_runs(self) -> list[tuple[str, str]]:
        """Every ``(project, run_id)`` pair, in sorted order."""
        pairs: list[tuple[str, str]] = []
        for project in self.list_projects():
            for run_id in self.list_runs(project):
                pairs.append((project, run_id))
        return pairs

    def find_run(self, run_id: str) -> tuple[str, str] | None:
        """First project containing a run of this name.

        Run IDs are only unique by convention, and this resolves collisions the way the
        reader does — first match wins — which means an edit aimed at one project can
        land in another. The one change made here is to iterate projects in sorted order
        rather than in directory order, so at least the winner is the same on every
        machine and every call.
        """
        for project in self.list_projects():
            run_dir = self.run_path(project, run_id)
            if run_dir is not None and run_dir.is_dir():
                return (project, run_id)
        return None

    def _list_subdirectories(self, directory: Path) -> list[str]:
        try:
            with os.scandir(directory) as entries:
                names = [e.name for e in entries if e.is_dir()]
        except OSError:
            return []
        return sorted(names)

    # ----------------------------------------------------------------------------------
    # Reading — run detail
    # ----------------------------------------------------------------------------------

    def read_run(self, project: str, run_id: str) -> dict | None:
        """The full run object served by ``GET /api/run/:id``.

        ``metadata.json`` is load-bearing: without it the run is invisible, and that is
        the only file whose absence hides anything. Everything else degrades to an empty
        table, a zero duration or a missing chart.
        """
        run_dir = self.run_path(project, run_id)
        if run_dir is None or not run_dir.is_dir():
            return None

        metadata = self._read_json_value(run_dir / METADATA_FILE)
        if not isinstance(metadata, dict):
            return None

        summary_raw = self._read_json_value(run_dir / SUMMARY_FILE)
        summary = summary_raw if isinstance(summary_raw, dict) else None
        config = self._read_json_value(run_dir / CONFIG_FILE)
        metrics_history = self.read_jsonl(run_dir / METRICS_FILE)
        artifact_records = self.read_jsonl(run_dir / ARTIFACTS_FILE)
        system_metrics = self._read_json_value(run_dir / SYSTEM_METRICS_FILE)

        artifacts = self._process_artifacts(run_dir, artifact_records)
        duration = (summary.get("duration") if summary else None) or 0
        start_time = metadata.get("created_at")
        state = (summary.get("state") if summary else None) or metadata.get("state")
        notes = (summary.get("notes") if summary else None) or metadata.get("notes")

        return {
            "_id": run_id,
            "name": notes or f"Run {run_id}",
            "experimentId": project,
            "experimentName": project,
            "status": map_state(state if isinstance(state, str) else None),
            "description": notes or "",
            "tags": metadata.get("tags") or [],
            "duration": duration,
            "durationFormatted": format_duration(duration),
            "createdAt": start_time,
            "startTime": start_time,
            "endTime": summary.get("end_time") if summary else None,
            "state": state,
            "platform": metadata.get("platform"),
            "pythonVersion": metadata.get("python_version"),
            "workingDirectory": metadata.get("working_directory"),
            "artifacts": artifacts,
            "artifactsCount": len(artifacts),
            "parameters": _extract_parameters(config),
            "metrics": _extract_metrics(summary),
            "metricsHistory": metrics_history,
            "systemMetrics": system_metrics,
            "checkpoints": self._read_checkpoints_in(run_dir),
            "config": config,
            "summary": summary_raw,
        }

    def read_latest_run(self) -> dict | None:
        """The run with the greatest ``created_at``.

        A run whose ``created_at`` is absent or unparseable can never win the comparison,
        which is the reader's behaviour and worth preserving: it means a malformed run
        cannot hijack the landing page.
        """
        latest: dict | None = None
        latest_at: float | None = None
        for project, run_id in self.list_all_runs():
            run = self.read_run(project, run_id)
            if run is None:
                continue
            if latest is None:
                latest, latest_at = run, _parse_iso(run.get("createdAt"))
                continue
            current_at = _parse_iso(run.get("createdAt"))
            if current_at is not None and latest_at is not None and current_at > latest_at:
                latest, latest_at = run, current_at
        return latest

    # ----------------------------------------------------------------------------------
    # Reading — experiments
    # ----------------------------------------------------------------------------------

    def read_experiment(self, project: str) -> dict | None:
        if project not in self.list_projects():
            return None
        return self._build_experiment(project)

    def list_experiments(self) -> list[dict]:
        return [self._build_experiment(p) for p in self.list_projects()]

    def read_latest_experiment(self) -> dict | None:
        latest: dict | None = None
        latest_at = float("-inf")
        for experiment in self.list_experiments():
            current_at = _parse_iso(experiment.get("createdAt"))
            current_at = float("-inf") if current_at is None else current_at
            if latest is None or current_at > latest_at:
                latest, latest_at = experiment, current_at
        return latest

    def _build_experiment(self, project: str) -> dict:
        """Aggregate one project the way the dashboard does.

        Three of the counters below are wrong on purpose. ``totalRuns`` counts
        directories, so a run with an unreadable ``metadata.json`` still depresses the
        success rate while contributing nothing else; the state buckets read
        ``summary.state`` literally and never consult ``metadata.state`` or the state
        mapping, so ``initialized`` and ``interrupted`` fall into no bucket at all. Both
        are reader defects. They are reproduced because the frontend's numbers have to
        keep adding up the same way, and fixing them belongs with a frontend change.
        """
        run_ids = self.list_runs(project)
        total_runs = len(run_ids)
        completed_runs = failed_runs = running_runs = 0
        total_duration = 0.0
        tags: dict[Any, None] = {}
        description = ""
        runs_data: list[dict] = []
        last_activity: float | None = None

        for run_id in run_ids:
            run_dir = self.run_path(project, run_id)
            if run_dir is None:
                continue
            metadata = self._read_json_value(run_dir / METADATA_FILE)
            summary_raw = self._read_json_value(run_dir / SUMMARY_FILE)
            summary = summary_raw if isinstance(summary_raw, dict) else None
            state = summary.get("state") if summary else None

            if isinstance(metadata, dict):
                runs_data.append(
                    {
                        "_id": run_id,
                        "name": metadata.get("name") or run_id,
                        # Raw, unmapped, so an interrupted run never reads as archived here.
                        "status": state or "unknown",
                        "startedAt": metadata.get("created_at") or _js_iso(_now()),
                    }
                )

                if state == "completed":
                    completed_runs += 1
                elif state == "failed":
                    failed_runs += 1
                elif state == "running":
                    running_runs += 1

                created_at = _parse_iso(metadata.get("created_at"))
                if created_at is not None and (last_activity is None or created_at > last_activity):
                    last_activity = created_at

                # A non-list `tags` throws inside the reader's forEach and returns 500
                # for the whole dashboard — one malformed run takes down the landing
                # page for every project. Ignored here instead.
                raw_tags = metadata.get("tags")
                if isinstance(raw_tags, list):
                    for tag in raw_tags:
                        if isinstance(tag, (str, int, float, bool)):
                            tags.setdefault(tag, None)

                if not description:
                    description = metadata.get("notes") or (summary.get("notes") if summary else "") or ""

            # Deliberately outside the metadata guard: the reader sums duration for runs
            # it otherwise ignores, so a hidden run still moves the average.
            duration = summary.get("duration") if summary else None
            if _is_number(duration):
                total_duration += float(duration)

        success_rate = _js_round(completed_runs / total_runs * 100) if total_runs > 0 else 0
        avg_duration = total_duration / total_runs if total_runs > 0 else 0

        return {
            "_id": project,
            "name": project,
            "description": description or f"Experiment: {project}",
            "tags": list(tags),
            "runs": runs_data,
            # Hardcoded empty by the reader: there is no event stream in the format.
            "activityTimeline": [],
            "createdAt": _js_iso_from_epoch(last_activity),
            "stats": {
                "totalRuns": total_runs,
                "completedRuns": completed_runs,
                "failedRuns": failed_runs,
                "runningRuns": running_runs,
                "successRate": f"{success_rate}%",
                "avgDuration": format_duration_no_hours(avg_duration),
                "lastRun": _js_iso_from_epoch(last_activity) or "N/A",
            },
        }

    def read_experiment_runs(self, project: str) -> list[dict]:
        """The comparison-table rows served by ``GET /api/experiment/:id/runs``.

        Two things differ from :meth:`read_run` on purpose. Metrics carry only ``latest``,
        with no stat siblings. Parameters come from the *other* config reader — top-level
        scalars only, nested objects dropped rather than flattened — so a run whose
        hyperparameters are nested shows twelve parameters on its detail page and none
        here. The frontend depends on both shapes as they are; unifying the two readers
        is a change worth making, but it changes what the comparison table displays.
        """
        rows: list[dict] = []
        for run_id in self.list_runs(project):
            run_dir = self.run_path(project, run_id)
            if run_dir is None:
                continue
            metadata = self._read_json_value(run_dir / METADATA_FILE)
            if not isinstance(metadata, dict):
                continue
            summary_raw = self._read_json_value(run_dir / SUMMARY_FILE)
            summary = summary_raw if isinstance(summary_raw, dict) else None
            config = self._read_json_value(run_dir / CONFIG_FILE)

            state = (summary.get("state") if summary else None) or metadata.get("state")
            duration = (summary.get("duration") if summary else None) or 0

            rows.append(
                {
                    "_id": run_id,
                    # Always synthesised here, never metadata.name — the same run is
                    # therefore named differently on the dashboard and in this table.
                    "name": f"Run {run_id}",
                    "status": map_state(state if isinstance(state, str) else None),
                    # A formatted string, where the run-detail path emits a number.
                    "duration": format_duration_no_hours(duration),
                    "startTime": metadata.get("created_at"),
                    "parameters": _extract_top_level_scalars(config),
                    "metrics": _extract_latest_metrics(summary),
                }
            )
        return rows

    # ----------------------------------------------------------------------------------
    # Reading — metrics, system metrics, checkpoints, artifacts
    # ----------------------------------------------------------------------------------

    def read_metrics(self, project: str, run_id: str) -> list[dict]:
        """Pivot the wide rows into one series per metric.

        Every non-reserved key on a line is a metric, with no type check, so a string
        logged under a stray key becomes a series whose values are strings. Series appear
        in the order their names were first seen, matching the reader.
        """
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return []
        series: dict[str, list[dict]] = {}
        for row in self.read_jsonl(run_dir / METRICS_FILE):
            for key, value in row.items():
                if key in RESERVED_METRIC_KEYS:
                    continue
                series.setdefault(key, []).append(
                    {"step": row.get("step"), "value": value, "timestamp": row.get("timestamp")}
                )
        return [{"name": name, "data": data} for name, data in series.items()]

    def read_metrics_timeseries(
        self, project: str, run_id: str, metric: str | None = None
    ) -> list[dict]:
        """Without ``metric``, the raw wide rows verbatim; with it, one row per step that
        carries the key. ``absolute_timestamp`` is surfaced only on this path."""
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return []
        rows = self.read_jsonl(run_dir / METRICS_FILE)
        if not metric:
            return rows
        return [
            {
                "timestamp": row.get("timestamp"),
                "absolute_timestamp": row.get("absolute_timestamp"),
                "step": row.get("step"),
                "value": row[metric],
            }
            for row in rows
            if metric in row
        ]

    def read_system_metrics(self, project: str, run_id: str) -> object:
        """Verbatim, because the schema lives entirely in the frontend component that
        renders it. Absent or malformed becomes ``{}``, which that component reads as
        "no system metrics available"."""
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return {}
        value = self._read_json_value(run_dir / SYSTEM_METRICS_FILE)
        return {} if value is None else value

    def read_checkpoints(self, project: str, run_id: str) -> list[dict]:
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return []
        return self._read_checkpoints_in(run_dir)

    def _read_checkpoints_in(self, run_dir: Path) -> list[dict]:
        """Newest first, from the ``*.json`` sidecars only.

        Real weight files are invisible to the reader, and ``size`` is the sidecar's size
        rather than the checkpoint's — both are reader limitations that a writer cannot
        work around. Sidecars with an unparseable ``created_at`` sort last instead of
        landing in an unspecified position.
        """
        checkpoints: list[dict] = []
        for entry in self._list_files(run_dir / CHECKPOINTS_DIR):
            if not entry.name.endswith(".json"):
                continue
            data = self._read_json_value(entry)
            if not isinstance(data, dict):
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            checkpoints.append(
                {
                    "name": data.get("checkpoint_name") or entry.name[: -len(".json")],
                    "path": str(entry),
                    "createdAt": data.get("created_at"),
                    "step": data.get("step"),
                    "size": size,
                }
            )
        checkpoints.sort(
            key=lambda c: _parse_iso(c.get("createdAt")) or float("-inf"), reverse=True
        )
        return checkpoints

    def read_artifacts(self, project: str, run_id: str) -> list[dict]:
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return []
        return self._process_artifacts(run_dir, self.read_jsonl(run_dir / ARTIFACTS_FILE))

    def _process_artifacts(self, run_dir: Path, records: list[dict]) -> list[dict]:
        """Declared artifacts first, then a placeholder per loose file not already named.

        The dedup is exact string equality on ``name``, so ``confusion_matrix.png`` on
        disk does not match an ``artifacts.jsonl`` entry called ``confusion_matrix`` and
        both are listed. Nothing here is downloadable — the format has no path field —
        so the placeholders exist only to admit that the files are there.
        """
        artifacts: list[dict] = []
        for record in records:
            artifacts.append(
                {
                    "_id": f"{_js_str(record.get('name'))}_{_js_str(record.get('version'))}",
                    "name": record.get("name"),
                    "type": record.get("type"),
                    "version": record.get("version"),
                    "createdAt": record.get("created_at"),
                    "fileCount": record.get("file_count") or 0,
                    # Passed through with no key transform: this is the only payload in
                    # the format that reaches the UI exactly as written.
                    "metadata": record.get("metadata") or {},
                }
            )

        known = {a["name"] for a in artifacts}
        for entry in self._list_files(run_dir / ARTIFACTS_DIR):
            if entry.name in known:
                continue
            artifacts.append(
                {
                    "_id": entry.name,
                    "name": entry.name,
                    "type": "unknown",
                    "version": "latest",
                    "createdAt": _js_iso(_now()),
                    "fileCount": 0,
                    "metadata": {},
                }
            )
        return artifacts

    def export_metrics_csv(self, project: str, run_id: str) -> str:
        """Union of every key across every row, alphabetically, one row per line.

        Unlike the reader this quotes per RFC 4180. The reader joins raw values with
        commas, so a single string metric containing a comma shifts every column to its
        right — silently, in a file the user opens in a spreadsheet and believes. That is
        a data-corruption bug, not a formatting preference, so it is fixed here rather
        than reproduced; the output is byte-identical whenever no value needs quoting.
        """
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return ""
        rows = self.read_jsonl(run_dir / METRICS_FILE)
        if not rows:
            return ""

        keys = sorted({key for row in rows for key in row})
        lines = [",".join(_csv_field(key) for key in keys)]
        for row in rows:
            lines.append(",".join(_csv_field(_csv_value(row.get(key))) for key in keys))
        return "\n".join(lines) + "\n"

    # ----------------------------------------------------------------------------------
    # Writing
    # ----------------------------------------------------------------------------------

    def create_run(self, project: str, run_id: str, metadata: dict) -> Path:
        """Create the run directory and write ``metadata.json`` before anything else.

        Nothing else about a run matters until that file exists: without it the run
        detail endpoint answers 404 and the experiment aggregation skips the run while
        still counting it. Raises rather than degrading, because a writer that cannot
        create its own directory has nowhere to go.
        """
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            raise StorageError(
                f"project/run name is not addressable: {project!r}/{run_id!r}. "
                "Names must be a single path component of [A-Za-z0-9._-]."
            )
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"could not create run directory {run_dir}: {exc}") from exc
        self.write_json(run_dir / METADATA_FILE, metadata)
        return run_dir

    def read_json(self, path: Path) -> Any | None:
        """``None`` for both missing and malformed — the reader cannot tell them apart
        either, which is why a writer bug looks exactly like an absent feature.

        The return type is ``Any`` rather than ``dict`` because it genuinely is: a valid
        ``system_metrics.json`` is a JSON *array* of samples, and it has to pass through
        verbatim. Callers that require a mapping check for one.
        """
        return self._read_json_value(path)

    def write_json(self, path: Path, data: dict | list) -> None:
        """Write the whole file atomically.

        These files are rewritten wholesale on every state transition, and a crash
        part-way through a plain write leaves truncated JSON — which every reader treats
        as an absent file, so the run loses its status and all of its metrics at once.
        ``NaN`` and ``Infinity`` are rejected here rather than written, because both are
        illegal JSON that the reader drops silently.
        """
        try:
            payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise StorageError(f"value is not representable as JSON for {path}: {exc}") from exc
        self.write_bytes(path, payload.encode("utf-8"))

    def write_bytes(self, path: Path, payload: bytes) -> None:
        """The atomic write underneath :meth:`write_json`, for content that is not JSON.

        Only ``uncommitted.patch`` uses it directly today. It is bytes rather than text
        because a patch is bytes: ``git diff --binary`` emits literal binary hunks, and
        decoding them to run them back through an encoder would corrupt exactly the
        patches that most need to survive.
        """
        directory = path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(directory), prefix=f".{path.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, path)
            except BaseException:
                _unlink_quietly(tmp_name)
                raise
        except OSError as exc:
            raise StorageError(f"could not write {path}: {exc}") from exc

    def read_jsonl(self, path: Path) -> list[dict]:
        """One object per line, dropping unparseable lines individually.

        That tolerance is what makes append-and-flush streaming safe: a torn final line
        from an interrupted write costs one step, not the file. Lines that parse to
        something other than an object are dropped too — the reader keeps them, and a
        bare string line then becomes chart series named after its character indices.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return []
        records: list[dict] = []
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    def append_jsonl(self, path: Path, record: dict) -> None:
        """Append one newline-terminated line and flush.

        Flushed but not fsynced: an fsync per logged step would dominate a training loop,
        and the reader already tolerates a torn last line, so handing the bytes to the OS
        is enough for the dashboard to see the step while the process is still alive.
        """
        try:
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise StorageError(f"record is not representable as JSON: {exc}") from exc
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
        except OSError as exc:
            raise StorageError(f"could not append to {path}: {exc}") from exc

    def write_provenance(
        self, project: str, run_id: str, manifest: dict, patch: bytes | None
    ) -> bool:
        """Write ``provenance.json`` and, when there is one, ``uncommitted.patch``.

        The patch goes down **first**. The manifest names it and carries its hash, so
        writing the manifest first would leave a window — one crash wide — in which a run
        advertises a patch that is not there, and a verifier cannot tell that from a patch
        somebody deleted. In the other order the worst case is an orphaned patch file,
        which reads as absent because nothing looks for a patch except through the
        manifest.

        Returns ``False`` rather than raising for an unaddressable name or a failed write:
        this is called from the capture path, and the whole point of that path is that a
        provenance failure never reaches the training run.
        """
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return False
        try:
            if patch:
                self.write_bytes(run_dir / PATCH_FILE, patch)
            self.write_json(run_dir / PROVENANCE_FILE, manifest)
        except StorageError:
            return False
        return True

    def read_provenance(self, project: str, run_id: str) -> dict | None:
        """The manifest, or ``None`` for absent, malformed or unaddressable.

        Absence is the normal case for every run written before format 1.1 and for every
        run whose capture failed, so it is not an error and never logged as one.
        """
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return None
        value = self._read_json_value(run_dir / PROVENANCE_FILE)
        return value if isinstance(value, dict) else None

    def read_patch(self, project: str, run_id: str) -> bytes | None:
        """The raw patch bytes, or ``None``. Never decoded — see :meth:`write_bytes`."""
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return None
        try:
            return (run_dir / PATCH_FILE).read_bytes()
        except (OSError, ValueError):
            return None

    def update_run_tags(self, project: str, run_id: str, tags: list[str]) -> bool:
        """Read, merge, write. A blind overwrite would destroy tags a user edited in the
        UI and any key a future version added; there is no locking, so this narrows the
        race rather than closing it."""
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return False
        path = run_dir / METADATA_FILE
        metadata = self._read_json_value(path)
        if not isinstance(metadata, dict):
            return False
        metadata["tags"] = list(tags)
        metadata["updated_at"] = _js_iso(_now())
        try:
            self.write_json(path, metadata)
        except StorageError:
            return False
        return True

    def update_run_description(self, project: str, run_id: str, description: str) -> bool:
        """Writes ``notes`` into ``summary.json``, creating the file if absent.

        ``notes`` is simultaneously the run's display name and its description on the run
        page, so editing a description renames the run. That is a reader defect worth
        fixing; until it is, this method inherits it.
        """
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return False
        path = run_dir / SUMMARY_FILE
        summary = self._read_json_value(path)
        if not isinstance(summary, dict):
            summary = {}
        summary["notes"] = description
        summary["updated_at"] = _js_iso(_now())
        try:
            self.write_json(path, summary)
        except StorageError:
            return False
        return True

    def update_experiment_description(self, project: str, name: str, description: str) -> bool:
        """Writes ``project_metadata.json``, which no reader opens.

        Kept for interface parity — the endpoint exists — but the description a user sets
        here will not survive a reload, because the displayed experiment description is
        derived from the first run with non-empty notes. Unlike the reader, this refuses
        to create the project directory as a side effect of a description edit: a write
        that materialises a project nobody asked for is how the original became an
        arbitrary-directory-creation bug.
        """
        project_dir = self.project_path(project)
        if project_dir is None or not project_dir.is_dir():
            return False
        path = project_dir / "project_metadata.json"
        data = self._read_json_value(path)
        if not isinstance(data, dict):
            data = {}
        data["name"] = name
        data["description"] = description
        data["updated_at"] = _js_iso(_now())
        try:
            self.write_json(path, data)
        except StorageError:
            return False
        return True

    # ----------------------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------------------

    def _read_json_value(self, path: Path) -> Any:
        """Parse a whole-file JSON document, or ``None``. Arrays are returned as lists —
        ``system_metrics.json`` is an array in its preferred shape."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def _list_files(self, directory: Path) -> list[Path]:
        """Loose files only, non-recursive, sorted. Subdirectories are dropped, which is
        why a directory-per-artifact layout produces nothing at all."""
        try:
            with os.scandir(directory) as entries:
                files = [Path(e.path) for e in entries if e.is_file()]
        except OSError:
            return []
        return sorted(files, key=lambda p: p.name)


# --------------------------------------------------------------------------------------
# Config and metrics extraction
# --------------------------------------------------------------------------------------


def _extract_parameters(config: Any) -> dict:
    """Config reader A, used by the run-detail view.

    Top-level scalars pass through; top-level objects are flattened exactly one level;
    depth two and beyond is discarded entirely, so ``{"model": {"encoder": {...}}}``
    yields nothing. Arrays flatten by numeric index, producing ``layers0``/``layers1``.
    Object-valued ``storage``, ``system`` and ``logging`` are dropped — a *scalar* by
    those names survives, because the drop list is only consulted for objects.
    """
    if not isinstance(config, dict):
        return {}
    parameters: dict[str, Any] = {}
    for key, value in config.items():
        if isinstance(value, (dict, list)):
            if key in CONFIG_DROP_KEYS:
                continue
            for sub_key, sub_value in _entries(value):
                if not isinstance(sub_value, (dict, list)):
                    parameters[camel_case(f"{key}_{sub_key}")] = sub_value
        else:
            parameters[camel_case(key)] = value
    return parameters


def _extract_top_level_scalars(config: Any) -> dict:
    """Config reader B, used by the comparison table. Top-level scalars only; nested
    objects are not flattened, they are dropped."""
    if not isinstance(config, dict):
        return {}
    return {
        camel_case(key): value
        for key, value in config.items()
        if not isinstance(value, (dict, list))
    }


def _extract_metrics(summary: Any) -> dict:
    """Flatten ``metrics_summary`` for the run-detail view.

    Nothing is computed here — no reader in the product derives an aggregate from
    ``metrics.jsonl``, so these numbers exist only because the writer wrote them twice.
    A metric appears only if its entry carries ``latest``; each recognised stat becomes a
    sibling key, so ``loss`` with all four stats yields five keys.
    """
    if not isinstance(summary, dict):
        return {}
    metrics_summary = summary.get("metrics_summary")
    if not isinstance(metrics_summary, dict):
        return {}
    metrics: dict[str, Any] = {}
    for key, value in metrics_summary.items():
        if not isinstance(value, dict):
            continue
        if "latest" in value:
            metrics[camel_case(key)] = value["latest"]
        for stat in STAT_KEYS:
            if stat in value:
                metrics[camel_case(f"{key}_{stat}")] = value[stat]
    return metrics


def _extract_latest_metrics(summary: Any) -> dict:
    """The comparison table's narrower view: ``latest`` only, no stat suffixes."""
    if not isinstance(summary, dict):
        return {}
    metrics_summary = summary.get("metrics_summary")
    if not isinstance(metrics_summary, dict):
        return {}
    return {
        camel_case(key): value["latest"]
        for key, value in metrics_summary.items()
        if isinstance(value, dict) and "latest" in value
    }


def _entries(value: dict | list) -> Iterable[tuple[str, Any]]:
    """Object entries the way JavaScript sees them — an array is an object whose keys are
    its stringified indices."""
    if isinstance(value, dict):
        return list(value.items())
    return [(str(index), item) for index, item in enumerate(value)]


# --------------------------------------------------------------------------------------
# Small conversions that have to match the reader exactly
# --------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _js_round(value: float) -> int:
    if math.isnan(value) or math.isinf(value):
        return 0
    return math.floor(value + 0.5)


def _js_str(value: Any) -> str:
    """String interpolation the way a template literal does it, so a malformed artifact
    line produces the same composite id here as it does in the reader."""
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _js_number_str(value)
    return str(value)


def _js_number_str(value: float) -> str:
    """JavaScript has one number type, so ``1.0`` prints as ``1``. Matters for CSV
    export, where the reader's output is the format users diff against."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return repr(value)


def _js_iso(moment: datetime) -> str:
    """``Date.prototype.toISOString`` — UTC, milliseconds, trailing ``Z``."""
    utc = moment.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _js_iso_from_epoch(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _js_iso(datetime.fromtimestamp(epoch, tz=timezone.utc))


def _parse_iso(value: Any) -> float | None:
    """Epoch seconds for an ISO-8601 string, or ``None`` if it cannot be parsed.

    A bare timestamp with no offset is read as UTC rather than as the server's local
    zone. The reader does the opposite, which silently shifts a run viewed on a machine
    in another timezone — an ambiguity the writer avoids by always emitting an offset.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, float):
        return _js_number_str(value)
    return str(value)


def _csv_field(text: str) -> str:
    if any(ch in text for ch in (",", '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
