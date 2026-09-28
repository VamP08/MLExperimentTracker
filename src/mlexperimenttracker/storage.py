"""All filesystem reads and writes, in one place.

Project names and run IDs come from URL segments, so every path goes through
:meth:`Storage.resolve_within`. Reads return the shapes the React frontend expects and
never raise: missing or malformed files become ``None`` / ``[]`` / ``{}``.
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
    LOG_LEVELS,
    LOGS_FILE,
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

# Per-project file written by PATCH /api/experiment/{id}. Lives beside the run dirs, not in
# one, so it's not in contract. The SDK never writes it.
PROJECT_METADATA_FILE = "project_metadata.json"


class StorageError(Exception):
    """Raised only by the writing half. Reads degrade instead of raising."""


class Storage:
    """Access to the experiment tree under one root.

    The root is resolved once so a later chdir or env change can't move it mid-run.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        if root is None:
            env_root = os.environ.get(STORAGE_ENV_VAR)
            if env_root:
                root = env_root
            else:
                # Path.home() covers USERPROFILE and HOME, and raises if neither is set.
                root = Path.home() / DEFAULT_STORAGE_DIRNAME
        self.root: Path = Path(root).expanduser().resolve()

    def __repr__(self) -> str:
        return f"Storage(root={str(self.root)!r})"

    # ----------------------------------------------------------------------------------
    # Containment (security boundary)
    # ----------------------------------------------------------------------------------

    def resolve_within(self, *segments: str) -> Path | None:
        """Join ``segments`` under the root, or ``None`` if they would escape it.

        Segments arrive URL-decoded, so each must be a single path component: no
        separators, drive prefix, ``.``/``..``, NUL or absolute path. The resolved-prefix
        check after that is a backstop. Callers treat ``None`` as not found.
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
            # Check both flavours everywhere: posixpath treats "C:foo" as a plain filename.
            if ntpath.isabs(segment) or posixpath.isabs(segment):
                return None
            if ntpath.splitdrive(segment)[0]:
                return None

        try:
            target = Path(self.root, *segments).resolve()
        except (OSError, ValueError):
            # Unresolvable name: not addressable. This must never raise.
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
        """Immediate subdirectories of the root. Loose files are ignored."""
        return self._list_subdirectories(self.root)

    def list_runs(self, project: str) -> list[str]:
        """Immediate subdirectories of a project. Deeper nesting is not seen."""
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
        """First project (in sorted order) containing a run of this name.

        Run IDs are only unique by convention, so on a collision the first match wins.
        Sorting keeps the winner stable across machines.
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
        """The full run object for ``GET /api/run/:id``.

        Returns ``None`` without ``metadata.json``; every other file is optional.
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
        """The run with the greatest ``created_at``. Unparseable dates never win."""
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

    def _read_project_description(self, project: str) -> str:
        """The description set via ``PATCH /api/experiment/{id}``, or ``""``.

        Anything other than a non-empty string gives ``""`` so the caller falls back to
        the derived description. Clearing the box brings the default back.
        """
        project_dir = self.project_path(project)
        if project_dir is None:
            return ""
        data = self._read_json_value(project_dir / PROJECT_METADATA_FILE)
        if not isinstance(data, dict):
            return ""
        description = data.get("description")
        return description if isinstance(description, str) else ""

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
        """Aggregate one project for the dashboard.

        Known quirks kept so the frontend numbers stay consistent: ``totalRuns`` counts
        directories (runs without metadata still lower the success rate), and the state
        buckets read ``summary.state`` raw, so ``initialized``/``interrupted`` land in none.
        A stored description beats the one derived from run notes.
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

                # Ignore non-list tags so one bad run can't break the whole dashboard.
                raw_tags = metadata.get("tags")
                if isinstance(raw_tags, list):
                    for tag in raw_tags:
                        if isinstance(tag, (str, int, float, bool)):
                            tags.setdefault(tag, None)

                if not description:
                    description = metadata.get("notes") or (summary.get("notes") if summary else "") or ""

            # Outside the metadata check on purpose: runs without metadata still count
            # toward the average duration.
            duration = summary.get("duration") if summary else None
            if _is_number(duration):
                total_duration += float(duration)

        # A user-set description wins over the derived one.
        description = self._read_project_description(project) or description

        success_rate = _js_round(completed_runs / total_runs * 100) if total_runs > 0 else 0
        avg_duration = total_duration / total_runs if total_runs > 0 else 0

        return {
            "_id": project,
            "name": project,
            "description": description or f"Experiment: {project}",
            "tags": list(tags),
            "runs": runs_data,
            # Always empty: the format has no event stream.
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
        """Comparison-table rows for ``GET /api/experiment/:id/runs``.

        Unlike :meth:`read_run`: metrics are ``latest`` only, and parameters are top-level
        scalars only (nested config is dropped, not flattened). The frontend relies on both.
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
                    # Not metadata.name, so this differs from the dashboard's name.
                    "name": f"Run {run_id}",
                    "status": map_state(state if isinstance(state, str) else None),
                    # A string here; read_run returns a number.
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
        """Pivot the wide rows into one series per metric, in first-seen order.

        Every non-reserved key is a metric; values are not type-checked.
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
        """Raw rows without ``metric``; with it, one row per step that has the key."""
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
        """``system_metrics.json`` as-is, or ``{}`` if absent or malformed."""
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
        """Checkpoints newest first, from the ``*.json`` sidecars only.

        ``size`` is the sidecar's size, not the weights'. Bad ``created_at`` sorts last.
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
        # Tie-break on step: checkpoints in the same clock tick share created_at, and the
        # stable sort would return them oldest-first. This was flaky on fast CI runners.
        checkpoints.sort(
            key=lambda c: (
                _parse_iso(c.get("createdAt")) or float("-inf"),
                c.get("step") if isinstance(c.get("step"), (int, float)) else float("-inf"),
            ),
            reverse=True,
        )
        return checkpoints

    def read_artifacts(self, project: str, run_id: str) -> list[dict]:
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return []
        return self._process_artifacts(run_dir, self.read_jsonl(run_dir / ARTIFACTS_FILE))

    def _process_artifacts(self, run_dir: Path, records: list[dict]) -> list[dict]:
        """Declared artifacts first, then a placeholder per loose file not already named.

        Dedup is exact match on ``name``, so ``x.png`` on disk and an entry ``x`` both show.
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
                    # Passed through as written, no camelCase transform.
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

    def read_logs(
        self,
        project: str,
        run_id: str,
        *,
        level: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Captured run output, oldest first, optionally filtered and paged.

        ``level`` matches one level exactly (case-insensitive), not "this and above".
        An unknown level matches nothing. ``offset``/``limit`` are clamped, not validated:
        negative offset is 0, limit <= 0 gives no rows. A torn last line is dropped.
        """
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return []
        records = self.read_jsonl(run_dir / LOGS_FILE)

        if level is not None:
            wanted = level.strip().lower() if isinstance(level, str) else ""
            if wanted not in LOG_LEVELS:
                return []
            records = [
                record
                for record in records
                if isinstance(record.get("level"), str)
                and record["level"].strip().lower() == wanted
            ]

        start = max(0, int(offset))
        if limit is None:
            return records[start:]
        count = int(limit)
        if count <= 0:
            return []
        return records[start : start + count]

    def read_logs_text(self, project: str, run_id: str) -> str:
        """Logs as plain text for download: elapsed seconds, level, source, message.

        Uses elapsed time (same clock as metrics). Shared by the CLI and the API.
        """
        return "".join(_log_line(record) for record in self.read_logs(project, run_id))

    def export_metrics_csv(self, project: str, run_id: str) -> str:
        """Metrics as CSV: all keys sorted as columns, one row per line.

        Quotes per RFC 4180 so a value with a comma can't shift columns.
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
        """Create the run directory and write ``metadata.json`` first.

        Raises :class:`StorageError` on a bad name or a failed write.
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
        """Parsed JSON, or ``None`` if missing or malformed.

        Not always a dict (``system_metrics.json`` is an array); callers check.
        """
        return self._read_json_value(path)

    def write_json(self, path: Path, data: dict | list) -> None:
        """Write the whole file atomically, so a crash can't leave truncated JSON.

        Rejects NaN/Infinity (invalid JSON) with :class:`StorageError`.
        """
        try:
            payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise StorageError(f"value is not representable as JSON for {path}: {exc}") from exc
        self.write_bytes(path, payload.encode("utf-8"))

    def write_bytes(self, path: Path, payload: bytes) -> None:
        """Atomic write (temp file + replace) for raw bytes.

        Bytes, not text, because ``git diff --binary`` patches must not be re-encoded.
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
        """One object per line. Bad or non-object lines are skipped individually.

        So a torn last line from a live writer costs one step, not the file.
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
        """Append one line and flush.

        No fsync: too slow per step, and readers already tolerate a torn last line.
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

    def append_log(self, project: str, run_id: str, record: dict) -> bool:
        """Append one record to ``logs.jsonl``. Never raises; returns whether it landed.

        Called from log capture in the training process, so failures must not raise.
        Won't create the run dir, so a bad run ID can't leave a run without metadata.
        The record is written as given; ``LogWriter`` owns validation.
        """
        if not isinstance(record, dict):
            return False
        run_dir = self.run_path(project, run_id)
        if run_dir is None or not run_dir.is_dir():
            return False
        try:
            self.append_jsonl(run_dir / LOGS_FILE, record)
        except StorageError:
            return False
        return True

    def write_provenance(
        self, project: str, run_id: str, manifest: dict, patch: bytes | None
    ) -> bool:
        """Write ``provenance.json`` and, if given, ``uncommitted.patch``.

        The patch goes first so a crash can't leave a manifest naming a missing patch.
        Returns ``False`` instead of raising; provenance must never break a training run.
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
        """The manifest, or ``None``. Normal for pre-1.1 runs or failed captures."""
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return None
        value = self._read_json_value(run_dir / PROVENANCE_FILE)
        return value if isinstance(value, dict) else None

    def read_patch(self, project: str, run_id: str) -> bytes | None:
        """The raw patch bytes, or ``None``. Never decoded."""
        run_dir = self.run_path(project, run_id)
        if run_dir is None:
            return None
        try:
            return (run_dir / PATCH_FILE).read_bytes()
        except (OSError, ValueError):
            return None

    def update_run_tags(self, project: str, run_id: str, tags: list[str]) -> bool:
        """Read-modify-write the tags in ``metadata.json``, keeping other keys.

        No locking, so concurrent writers can still race.
        """
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
        """Write ``notes`` into ``summary.json``, creating it if absent.

        ``notes`` is also the run's display name, so this renames the run too.
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
        """Read-modify-write ``project_metadata.json`` (read by the experiment view).

        Won't create a missing project dir, so an edit can't create arbitrary directories.
        """
        project_dir = self.project_path(project)
        if project_dir is None or not project_dir.is_dir():
            return False
        path = project_dir / PROJECT_METADATA_FILE
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
        """Parse a whole-file JSON document, or ``None``."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def _list_files(self, directory: Path) -> list[Path]:
        """Loose files only, non-recursive, sorted by name."""
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
    """Config parameters for the run-detail view.

    Scalars pass through; objects/arrays are flattened one level (``layers0``); deeper
    levels are dropped. Object-valued ``CONFIG_DROP_KEYS`` are skipped, scalars are kept.
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
    """Config for the comparison table: top-level scalars only."""
    if not isinstance(config, dict):
        return {}
    return {
        camel_case(key): value
        for key, value in config.items()
        if not isinstance(value, (dict, list))
    }


def _extract_metrics(summary: Any) -> dict:
    """Flatten ``metrics_summary`` for the run-detail view (nothing computed here).

    ``latest`` becomes ``loss``, each stat becomes a sibling key like ``lossMean``.
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
    """``latest`` only, for the comparison table."""
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
    """Key/value pairs, JS-style: arrays use stringified indices as keys."""
    if isinstance(value, dict):
        return list(value.items())
    return [(str(index), item) for index, item in enumerate(value)]


# --------------------------------------------------------------------------------------
# JS-compatible conversions
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
    """Stringify like a JS template literal (``undefined``, ``true``, ``1``)."""
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _js_number_str(value)
    return str(value)


def _js_number_str(value: float) -> str:
    """Format a float like JS does, so ``1.0`` prints as ``1``."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return repr(value)


def _js_iso(moment: datetime) -> str:
    """Like JS ``toISOString``: UTC, milliseconds, trailing ``Z``."""
    utc = moment.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _js_iso_from_epoch(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _js_iso(datetime.fromtimestamp(epoch, tz=timezone.utc))


def _parse_iso(value: Any) -> float | None:
    """Epoch seconds for an ISO-8601 string, or ``None``. No offset means UTC."""
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


def _log_line(record: dict) -> str:
    """One log record as a line of text. Missing or odd fields render blank."""
    timestamp = record.get("timestamp")
    stamp = f"{float(timestamp):10.3f}s" if _is_number(timestamp) else " " * 11
    level = record.get("level")
    level = level.upper() if isinstance(level, str) else ""
    source = record.get("source")
    source = source if isinstance(source, str) else ""
    message = record.get("message")
    if not isinstance(message, str):
        message = "" if message is None else _csv_value(message)
    # Widths fit the longest level ("critical") and source ("logging") so messages line up.
    return f"[{stamp}] {level:<8} {source:<7} {message}\n"


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
