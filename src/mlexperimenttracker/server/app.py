"""The read/write HTTP API, and the host for the built React bundle.

This is a port of the Express server the React frontend already talks to, so the route
paths and the response bodies are fixed by an existing client rather than chosen here.
Where a body is odd — one endpoint wrapping its payload in ``{success, message, data}``
while every other returns a bare value, one endpoint changing shape depending on a query
parameter — it is odd on purpose: the frontend reads it that way today, and a tidier
shape is a frontend change wearing a backend disguise.

Three things are deliberately *not* ports.

*Containment.* Every path parameter is resolved through :meth:`Storage.resolve_within`,
so a name that cannot address a file inside the storage root is a 404 and never an
exception. The handlers below never touch a path; they hold strings and hand them to
Storage.

*Blocking.* Every read here is an unbounded synchronous directory walk — the latest-run
endpoint reads every file of every run of every project. Running that on the event loop
is what makes one slow request stall all the others, so each one is offloaded to a worker
thread. Nothing in this module opens a file itself, which is what makes that offload a
single, checkable rule rather than a habit.

*Leaks.* Handlers raise, and the exception handlers at the bottom decide what a client is
told. A stack trace and an absolute server path are diagnostics for the operator's log,
not for the browser.
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

#: Loopback, not ``0.0.0.0``. There is no authentication anywhere in this server and that
#: is a decision, not an omission: the trust boundary is the loopback interface. Binding
#: to an interface anybody else can reach removes the only control there is.
DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 5000

#: Origins allowed to call the API cross-origin. Vite's dev server proxies ``/api``, so
#: during normal frontend development this list is never consulted; it exists for the case
#: where the browser talks to the API directly, which is what a developer does the moment
#: something looks wrong. The served bundle is same-origin and needs no entry at all.
DEV_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

#: Populated at release by building the React app into the package. Absent in a source
#: checkout, which is the case this module has to survive without pretending otherwise.
STATIC_DIR: Path = Path(__file__).resolve().parent / "static"

_CSV_CHUNK = 64 * 1024
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


# --------------------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------------------


class _JSONResponse(JSONResponse):
    """JSON that a browser can actually parse.

    Python's :func:`json.loads` accepts the non-standard ``NaN`` and ``Infinity`` tokens,
    so a metrics line containing either survives the read and would be re-emitted by the
    default encoder as a document ``JSON.parse`` rejects — the whole response lost to one
    value. Non-finite floats become ``null`` instead. The sanitising walk only runs after
    a strict dump has failed, so the common case pays one extra encode attempt and no
    traversal.
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
    """Every error body in this API is ``{"message": …}``, so every raise builds one."""
    return HTTPException(status_code=status, detail={"message": message})


# --------------------------------------------------------------------------------------
# Small ports of JavaScript semantics
# --------------------------------------------------------------------------------------


def _js_or(value: Any, fallback: Any) -> Any:
    """``value || fallback`` as JavaScript evaluates it.

    Needed because the handlers being ported use ``||`` on values that are empty rather
    than absent, and Python disagrees with JavaScript about exactly those: ``[]`` and
    ``{}`` are falsy here and truthy there. An empty system-metrics array must stay an
    empty array, not become ``{}``.
    """
    if value is None or value is False or value == "":
        return fallback
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
        return fallback
    return value


def _attachment_filename(stem: str, extension: str) -> str:
    """Reduce a run ID to characters that cannot break out of a header.

    The ID has already been proved addressable by Storage, which rejects separators and
    NUL — but not a quote or a newline, and this value is interpolated into
    ``Content-Disposition``. The recommended charset for a run ID (§2.1 of the data
    contract) is exactly what survives here, so a well-named run is unchanged.
    """
    return f"{_UNSAFE_FILENAME.sub('_', stem)}.{extension}"


def _package_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("mlexperimenttracker")
    except PackageNotFoundError:
        # Running from a source tree that was never installed — the normal case for the
        # test suite, and not worth failing a health check over.
        return "0.0.0+source"


# --------------------------------------------------------------------------------------
# Storage access, always off the event loop
# --------------------------------------------------------------------------------------


async def _read(func: Callable[..., Any], *args: Any) -> Any:
    return await run_in_threadpool(func, *args)


def _locate_run(storage: Storage, run_id: str) -> tuple[str, dict] | None:
    """Resolve a bare run ID to its project and its full run object, in one thread hop.

    Run IDs are unique only by convention, so this reproduces the reader's rule: the first
    project containing a directory of that name wins, and if that directory has no
    readable ``metadata.json`` the run is *not found* rather than searched for elsewhere.
    A run shadowed by a namesake in an earlier project is unreachable — which is why the
    SDK embeds the project in the ID it generates.
    """
    located = storage.find_run(run_id)
    if located is None:
        return None
    project, _ = located
    run = storage.read_run(project, run_id)
    if run is None:
        return None
    return project, run


def _verify_report(storage: Storage, project: str, run_id: str) -> Any:
    """Re-check a run's recorded world against the one that exists now.

    The verifier is imported here rather than at the top of the module, and that is a
    deliberate coupling choice rather than an import-time optimisation: it re-hashes
    datasets and shells out to git, so an installation that never calls this route should
    not load it, and an installation that does not ship it at all still serves the other
    twenty.
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
    """Static files with a single-page-app fallback.

    The React app owns client-side routes such as ``/run/abc`` that have no file behind
    them, so an unmatched GET has to return ``index.html`` and let the router sort it out.
    The exception is ``/api``: an unmatched API path is a 404 in JSON, because answering
    a mistyped endpoint with a page of HTML is how a client ends up reporting "the server
    returned HTML" instead of the actual mistake.

    That exception is taken *before* the method check, and for a reason worth stating: a
    URL-encoded separator survives routing as a real one, so ``/api/experiment/%2fetc%2f
    passwd`` arrives here as a three-segment path that matches no route. Letting it reach
    the file server answers a nonexistent endpoint with 405 Method Not Allowed, which
    tells a caller the path exists and the verb is wrong — the opposite of the truth, and
    a difference in status code visible only when a bundle happens to be built. The
    ``/api`` namespace belongs to the router under every method.
    """

    #: Suffixes that mean "this was meant to be a file, not a client-side route". A React
    #: route is a path segment; an asset has an extension. Kept as a check on the last
    #: segment rather than a fixed allowlist so a new asset type cannot silently regress.
    _ASSET_PATH = re.compile(r"\.[A-Za-z0-9]{1,8}$")

    async def get_response(self, path: str, scope: Scope) -> Any:
        request_path = scope.get("path", "")
        if request_path.startswith("/api"):
            raise HTTPException(status_code=404, detail={"message": "Not Found"})
        # A missing asset must 404, not fall through to index.html. Serving HTML with a
        # 200 for a missing .js or .png hides the failure twice over: the browser reports
        # a MIME-type error instead of a missing file, and anything scripted against the
        # server sees success. Only extensionless paths are client-side routes.
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
    """Build the application around a :class:`Storage`.

    The storage instance is a parameter rather than a module-level singleton so a test can
    hand in a root under ``tmp_path``; without that the suite would have to mutate the
    environment and every test would share one tree. ``static_dir`` is the same argument
    for the bundle: it defaults to the directory a release populates, and overriding it
    lets a checkout serve a bundle built anywhere — including the one the tests build.
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
    # Declaration order is load-bearing twice over: `/api/experiment/all` must precede
    # `/api/experiment/{id}`, and `/api/run/{id}/metrics/timeseries` must precede
    # `/api/run/{id}/metrics`, or the parameterised route matches first and swallows them.
    _register_experiments(app, store)
    _register_runs(app, store)
    _register_ui(app, bundle)
    return app


def _register_health(app: FastAPI, store: Storage) -> None:
    @app.get("/api/health", response_model=None)
    async def health() -> dict:
        """Answers the two questions a support conversation opens with: is it running, and
        is it looking where you think it is looking. The Express server printed the
        resolved storage path once at startup, into a terminal nobody still has open."""
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
        """Never 404s. An unknown — or unaddressable — project is an empty comparison
        table, which is what the frontend renders while a project is still empty."""
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
        """Writes ``project_metadata.json``, and the experiment read paths open it.

        Until GAPS M3 was fixed nothing did, so this edit appeared to save and reverted on
        the next load. A stored description now outranks the one derived from the first
        run's notes; clearing it restores the derivation.
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
        """Two shapes from one path: with ``metric``, one row per step carrying that key;
        without it, the raw wide rows verbatim. The frontend relies on both."""
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
        """The reproducibility manifest, verbatim.

        Absence is a 404 rather than an empty object because it is a fact about the run
        and not a fact about this request: every run written before format 1.1, and every
        run whose capture failed, has no manifest, and a client has to be able to tell
        "nothing was recorded" from "everything matched".
        """
        project, _ = await locate(run_id)
        manifest = await _read(store.read_provenance, project, run_id)
        if manifest is None:
            raise _error(404, "No provenance recorded")
        return manifest

    @app.get("/api/run/{run_id}/patch", response_model=None)
    async def patch(run_id: str) -> Response:
        """The captured diff, as the bytes on disk.

        Served as an attachment and never decoded. ``git diff --binary`` output is a
        patch only for as long as nobody re-encodes it, and the one thing a user does with
        this file is feed it back to ``git apply``. A zero-length file reads as absent:
        the writer never produces one, so an empty patch is a patch somebody emptied.
        """
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
        """Drift between the world the manifest recorded and the world as it is now.

        The manifest is checked for first so that a run with nothing recorded answers the
        same 404 as the provenance route, with the same message. Verification re-hashes
        datasets and runs git, so it is offloaded like every other disk read here — it is
        the slowest thing this server does, and the only one whose cost is unbounded by
        the size of the run directory.
        """
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
        """The array check precedes the lookup, so a malformed body is a 400 whether or
        not the run exists — a non-array ``tags`` written to disk returns 500 for the
        entire dashboard, so this is the one piece of validation the format cannot do
        without."""
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
    """Serve the built bundle if it is there, and say so plainly if it is not.

    Mounted last, so every API route is matched before the catch-all — and registered at
    all only when there is something to serve, because a mount that resolves to an empty
    directory answers every page request with a 404 that looks like a routing bug.
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
    """Parse a JSON object body, tolerating everything else as empty.

    The handlers validate the one field they care about themselves, and this keeps a
    missing content type or a malformed body producing the same ``{"message": …}`` shape
    as every other error rather than a validation document in a different schema.
    """
    try:
        value = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _string_field(body: dict, key: str) -> str:
    """A description is written into a field the UI renders as text — and, for a run,
    into the field that doubles as its display name. A number or an object there produces
    a run whose name is ``[object Object]``, so the type is checked rather than coerced;
    an absent key is an empty description, which is what clearing the box sends."""
    value = body.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _error(400, f"{key.capitalize()} must be a string")
    return value


def _chunks(text: str) -> Iterator[bytes]:
    """Hand the CSV to the client in pieces.

    Storage builds the whole document in memory first, so this bounds the size of each
    write rather than the peak memory of the request; a genuinely streaming export needs a
    row iterator on the storage side, and that is worth doing when a metrics file is large
    enough to matter.
    """
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
        """FastAPI's default is a list of error objects naming the offending fields. This
        API answers in one shape, and the detail is of no use to the only client there
        is."""
        return _JSONResponse(status_code=400, content={"message": "Invalid request"})

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> _JSONResponse:
        """The reader this replaces returned ``error.message`` to the client, which for a
        filesystem failure is an absolute server path. The message goes to the log, where
        the operator is; the client is told that something failed and nothing else."""
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
    """Block, serving the app. Imported lazily so the SDK half never pulls in uvicorn."""
    import uvicorn

    uvicorn.run(create_app(storage), host=host, port=port, log_level=log_level)
