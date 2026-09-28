"""HTTP API and host for the built React bundle.

Routes and response shapes match the old Express server because the frontend depends on
them, odd ones included. Path params go through Storage's containment check (bad names
404), all disk reads run in a threadpool, and errors never expose paths or tracebacks.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.types import Scope

from ..storage import Storage

__all__ = ["create_app", "serve", "DEFAULT_HOST", "DEFAULT_PORT", "STATIC_DIR"]

logger = logging.getLogger(__name__)

# There is no auth; loopback is the trust boundary. Don't default to 0.0.0.0.
DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 5000

# For hitting the API directly from a dev frontend. Vite proxies /api normally, and the
# served bundle is same-origin.
DEV_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

# Filled at release by building the frontend into the package; absent in a checkout.
STATIC_DIR: Path = Path(__file__).resolve().parent / "static"

_CSV_CHUNK = 64 * 1024
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


# --------------------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------------------


class _JSONResponse(JSONResponse):
    """Strict JSON: NaN/Infinity become ``null`` so ``JSON.parse`` accepts the body.

    The sanitising walk only runs if a strict dump fails.
    """

    def render(self, content: Any) -> bytes:
        try:
            return _dump(content)
        except ValueError:
            return _dump(_finite(content))


def _dump(content: Any) -> bytes:
    return json.dumps(
        content, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite(item) for item in value]
    return value


def _error(status: int, message: str) -> HTTPException:
    """Build an HTTPException with the API's ``{"message": ...}`` body."""
    return HTTPException(status_code=status, detail={"message": message})


# --------------------------------------------------------------------------------------
# Small ports of JavaScript semantics
# --------------------------------------------------------------------------------------


def _js_or(value: Any, fallback: Any) -> Any:
    """JS ``value || fallback``. Unlike Python, ``[]`` and ``{}`` count as truthy."""
    if value is None or value is False or value == "":
        return fallback
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
        return fallback
    return value


def _attachment_filename(stem: str, extension: str) -> str:
    """Make a Content-Disposition filename safe.

    Storage already rejects separators and NUL but not quotes or newlines. IDs from the
    SDK's charset pass through unchanged.
    """
    return f"{_UNSAFE_FILENAME.sub('_', stem)}.{extension}"


def _package_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("mlexperimenttracker")
    except PackageNotFoundError:
        # Uninstalled source tree, e.g. the test suite.
        return "0.0.0+source"


# --------------------------------------------------------------------------------------
# Storage access, always off the event loop
# --------------------------------------------------------------------------------------


async def _read(func: Callable[..., Any], *args: Any) -> Any:
    return await run_in_threadpool(func, *args)


def _locate_run(storage: Storage, run_id: str) -> tuple[str, dict] | None:
    """Resolve a run ID to ``(project, run)``, or ``None``.

    First project with a matching directory wins; if its metadata is unreadable the run is
    not found. The SDK puts the project in generated IDs to avoid shadowing.
    """
    located = storage.find_run(run_id)
    if located is None:
        return None
    project, _ = located
    run = storage.read_run(project, run_id)
    if run is None:
        return None
    return project, run


def _read_logs(
    storage: Storage,
    project: str,
    run_id: str,
    level: str | None,
    limit: int | None,
    offset: int,
) -> list[dict]:
    """Positional adapter: ``Storage.read_logs`` filters are keyword-only."""
    return storage.read_logs(project, run_id, level=level, limit=limit, offset=offset)


def _verify_report(storage: Storage, project: str, run_id: str) -> Any:
    """Compare a run's recorded provenance with the current state.

    Imported lazily so the rest of the API works without the verifier. 501 if missing.
    """
    try:
        from ..verify import verify
    except ImportError as exc:
        raise _error(501, "Verification is not available in this installation") from exc

    return verify(storage, project, run_id).to_dict()


def _health(storage: Storage) -> dict:
    return {
        "status": "ok",
        "version": _package_version(),
        "storage_root": str(storage.root),
        "projects": len(storage.list_projects()),
        "runs": len(storage.list_all_runs()),
    }


# --------------------------------------------------------------------------------------
# Static assets
# --------------------------------------------------------------------------------------


class _SPAStaticFiles(StaticFiles):
    """Static files with an SPA fallback to ``index.html`` for client-side routes.

    Unmatched ``/api`` paths are a JSON 404 under every method. This check comes before
    the method check, otherwise an encoded path like ``/api/experiment/%2fetc%2fpasswd``
    would get a misleading 405 from the file server.
    """

    # A last segment with an extension is an asset, not a client-side route.
    _ASSET_PATH = re.compile(r"\.[A-Za-z0-9]{1,8}$")

    async def get_response(self, path: str, scope: Scope) -> Any:
        request_path = scope.get("path", "")
        if request_path.startswith("/api"):
            raise HTTPException(status_code=404, detail={"message": "Not Found"})
        # Missing assets 404 instead of getting index.html with a 200.
        if self._ASSET_PATH.search(request_path.rsplit("/", 1)[-1]):
            return await super().get_response(path, scope)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


# --------------------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------------------


def create_app(storage: Storage | None = None, static_dir: Path | None = None) -> FastAPI:
    """Build the app around a :class:`Storage` (default root if omitted).

    ``static_dir`` overrides where the built frontend is served from.
    """
    store = storage if storage is not None else Storage()
    bundle = STATIC_DIR if static_dir is None else Path(static_dir)

    app = FastAPI(
        title="ML Experiment Tracker",
        version=_package_version(),
        default_response_class=_JSONResponse,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.storage = store

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEV_ORIGINS),
        allow_methods=["GET", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    _register_errors(app)
    _register_health(app, store)
    _register_dashboard(app, store)
    # Order matters: fixed paths like /api/experiment/all must be registered before the
    # parameterised routes that would otherwise match them.
    _register_experiments(app, store)
    _register_runs(app, store)
    _register_ui(app, bundle)
    return app


def _register_health(app: FastAPI, store: Storage) -> None:
    @app.get("/api/health", response_model=None)
    async def health() -> dict:
        """Status, version, storage root and project/run counts."""
        return await _read(_health, store)


def _register_dashboard(app: FastAPI, store: Storage) -> None:
    @app.get("/api/dashboard", response_model=None)
    async def dashboard() -> dict:
        experiments = await _read(store.list_experiments)
        return {
            "success": True,
            "message": "Dashboard data retrieved successfully",
            "data": experiments,
        }


def _register_experiments(app: FastAPI, store: Storage) -> None:
    @app.get("/api/experiment/all", response_model=None)
    async def all_experiments() -> list[dict]:
        return await _read(store.list_experiments)

    @app.get("/api/experiment/{experiment_id}/runs", response_model=None)
    async def experiment_runs(experiment_id: str) -> list[dict]:
        """Never 404s; an unknown project returns an empty list."""
        return await _read(store.read_experiment_runs, experiment_id)

    @app.get("/api/experiment/{experiment_id}", response_model=None)
    async def experiment(experiment_id: str) -> dict:
        found = await _read(store.read_experiment, experiment_id)
        if found is None:
            raise _error(404, "Experiment not found")
        return found

    @app.get("/api/experiment", response_model=None)
    async def latest_experiment() -> dict:
        found = await _read(store.read_latest_experiment)
        if found is None:
            raise _error(404, "No experiments found")
        return found

    @app.patch("/api/experiment/{experiment_id}", response_model=None)
    async def update_experiment(experiment_id: str, request: Request) -> dict:
        """Save the description to ``project_metadata.json``.

        A stored description overrides the one derived from the first run's notes; an
        empty one restores the derived value.
        """
        body = await _body(request)
        description = _string_field(body, "description")
        updated = await _read(
            store.update_experiment_description, experiment_id, experiment_id, description
        )
        if not updated:
            raise _error(404, "Experiment not found")
        return {"message": "Description updated", "description": description}


def _register_runs(app: FastAPI, store: Storage) -> None:
    async def locate(run_id: str) -> tuple[str, dict]:
        located = await _read(_locate_run, store, run_id)
        if located is None:
            raise _error(404, "Run not found")
        return located

    @app.get("/api/run/{run_id}/metrics/timeseries", response_model=None)
    async def metrics_timeseries(run_id: str, metric: str | None = None) -> list[dict]:
        """With ``metric``, one row per step for that key; without it, the raw rows."""
        project, _ = await locate(run_id)
        return await _read(store.read_metrics_timeseries, project, run_id, metric)

    @app.get("/api/run/{run_id}/metrics/export", response_model=None)
    async def export_metrics(run_id: str, format: str = "csv") -> Any:
        project, _ = await locate(run_id)
        if format == "csv":
            csv = await _read(store.export_metrics_csv, project, run_id)
            return StreamingResponse(
                _chunks(csv),
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": "attachment; filename="
                    f'"{_attachment_filename("metrics_" + run_id, "csv")}"'
                },
            )
        if format == "json":
            rows = await _read(store.read_metrics_timeseries, project, run_id, None)
            return _JSONResponse(
                content=rows,
                headers={
                    "Content-Disposition": "attachment; filename="
                    f'"{_attachment_filename("metrics_" + run_id, "json")}"'
                },
            )
        raise _error(400, "Unsupported format. Use 'csv' or 'json'")

    @app.get("/api/run/{run_id}/metrics", response_model=None)
    async def metrics(run_id: str) -> list[dict]:
        project, _ = await locate(run_id)
        return await _read(store.read_metrics, project, run_id)

    @app.get("/api/run/{run_id}/logs/download", response_model=None)
    async def download_logs(run_id: str) -> Response:
        """The whole log as a plain-text attachment. No logs gives an empty file, not 404."""
        project, _ = await locate(run_id)
        text = await _read(store.read_logs_text, project, run_id)
        return Response(
            content=text.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename="
                f'"{_attachment_filename(run_id + "_logs", "txt")}"'
            },
        )

    @app.get("/api/run/{run_id}/logs", response_model=None)
    async def logs(
        run_id: str,
        level: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Captured output, oldest first. Optional paging; an unknown ``level`` returns []."""
        project, _ = await locate(run_id)
        return await _read(_read_logs, store, project, run_id, level, limit, offset)

    @app.get("/api/run/{run_id}/system-metrics", response_model=None)
    async def system_metrics(run_id: str) -> Any:
        _, run = await locate(run_id)
        return _js_or(run.get("systemMetrics"), {})

    @app.get("/api/run/{run_id}/checkpoints", response_model=None)
    async def checkpoints(run_id: str) -> list[dict]:
        _, run = await locate(run_id)
        return _js_or(run.get("checkpoints"), [])

    @app.get("/api/run/{run_id}/artifacts", response_model=None)
    async def artifacts(run_id: str) -> list[dict]:
        _, run = await locate(run_id)
        return _js_or(run.get("artifacts"), [])

    @app.get("/api/run/{run_id}/provenance", response_model=None)
    async def provenance(run_id: str) -> dict:
        """The provenance manifest as stored. 404 if none was recorded."""
        project, _ = await locate(run_id)
        manifest = await _read(store.read_provenance, project, run_id)
        if manifest is None:
            raise _error(404, "No provenance recorded")
        return manifest

    @app.get("/api/run/{run_id}/patch", response_model=None)
    async def patch(run_id: str) -> Response:
        """The captured diff as raw bytes, so ``git apply`` still works. Empty counts as 404."""
        project, _ = await locate(run_id)
        data = await _read(store.read_patch, project, run_id)
        if not data:
            raise _error(404, "No patch recorded")
        return Response(
            content=data,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename="
                f'"{_attachment_filename(run_id + "_uncommitted", "patch")}"'
            },
        )

    @app.get("/api/run/{run_id}/verify", response_model=None)
    async def verify(run_id: str) -> Any:
        """Drift between the recorded provenance and now. Same 404 as /provenance if none."""
        project, _ = await locate(run_id)
        if await _read(store.read_provenance, project, run_id) is None:
            raise _error(404, "No provenance recorded")
        return await _read(_verify_report, store, project, run_id)

    @app.get("/api/run/{run_id}", response_model=None)
    async def run(run_id: str) -> dict:
        _, found = await locate(run_id)
        return found

    @app.get("/api/run", response_model=None)
    async def latest_run() -> dict:
        found = await _read(store.read_latest_run)
        if found is None:
            raise _error(404, "No runs found")
        return found

    @app.patch("/api/run/{run_id}/tags", response_model=None)
    async def update_tags(run_id: str, request: Request) -> dict:
        """Replace a run's tags. Non-array ``tags`` is a 400, checked before the lookup,
        since writing one to disk breaks the whole dashboard."""
        body = await _body(request)
        tags = body.get("tags")
        if not isinstance(tags, list):
            raise _error(400, "Tags must be an array")
        project, _ = await locate(run_id)
        if not await _read(store.update_run_tags, project, run_id, tags):
            raise _error(500, "Failed to update tags")
        return {"message": "Tags updated", "tags": tags}

    @app.patch("/api/run/{run_id}/description", response_model=None)
    async def update_description(run_id: str, request: Request) -> dict:
        body = await _body(request)
        project, _ = await locate(run_id)
        description = _string_field(body, "description")
        if not await _read(store.update_run_description, project, run_id, description):
            raise _error(500, "Failed to update description")
        return {"message": "Description updated", "description": description}


def _register_ui(app: FastAPI, bundle: Path) -> None:
    """Mount the built frontend if present, else a JSON note at ``/``.

    Registered last so API routes match before the catch-all.
    """
    if bundle.is_dir() and (bundle / "index.html").is_file():
        app.mount("/", _SPAStaticFiles(directory=bundle, html=True), name="ui")
        return

    @app.get("/", response_model=None)
    async def ui_missing() -> dict:
        return {
            "status": "ok",
            "message": (
                "The API is running but the web UI has not been built into this "
                "installation. Build the frontend into "
                "src/mlexperimenttracker/server/static, or run the Vite dev server and "
                "use its address."
            ),
            "api": "/api/health",
        }


# --------------------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------------------


async def _body(request: Request) -> dict:
    """Parse a JSON object body; anything else is ``{}``. Handlers validate fields."""
    try:
        value = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _string_field(body: dict, key: str) -> str:
    """Return a string field, ``""`` if absent. Non-strings are a 400, not coerced."""
    value = body.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _error(400, f"{key.capitalize()} must be a string")
    return value


def _chunks(text: str) -> Iterator[bytes]:
    """Yield the CSV in chunks. The whole document is still built in memory first."""
    data = text.encode("utf-8")
    for start in range(0, len(data), _CSV_CHUNK):
        yield data[start : start + _CSV_CHUNK]


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> _JSONResponse:
        detail = exc.detail
        body = detail if isinstance(detail, dict) else {"message": str(detail)}
        return _JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> _JSONResponse:
        """Replace FastAPI's field-error list with the usual ``{"message": ...}`` 400."""
        return _JSONResponse(status_code=400, content={"message": "Invalid request"})

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> _JSONResponse:
        """Log the error; the client gets a generic 500 so no server paths leak."""
        logger.exception("unhandled error serving %s %s", request.method, request.url.path)
        return _JSONResponse(status_code=500, content={"message": "Internal server error"})


# --------------------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------------------


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    storage: Storage | None = None,
    log_level: str = "info",
) -> None:
    """Run the app with uvicorn (imported lazily). Blocks."""
    import uvicorn

    uvicorn.run(create_app(storage), host=host, port=port, log_level=log_level)
