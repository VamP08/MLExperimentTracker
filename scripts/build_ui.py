"""Build the React frontend and copy it into src/mlexperimenttracker/server/static.

Run before building a wheel: python scripts/build_ui.py (needs Node and npm).
The static dir is build output and gitignored. Each run replaces the whole bundle.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = REPO_ROOT / "ml_frontend"
DIST_DIR = FRONTEND_DIR / "dist"
STATIC_DIR = REPO_ROOT / "src" / "mlexperimenttracker" / "server" / "static"

# Copy here first, then rename into place, so a failed copy leaves the old bundle intact.
STAGING_DIR = STATIC_DIR.with_name(STATIC_DIR.name + ".incoming")

NODE_HINT = (
    "npm was not found on PATH.\n"
    "The dashboard is a Vite build, so building it needs Node.js 18 or newer:\n"
    "  https://nodejs.org/en/download\n"
    "The SDK half of this package does not need Node at all — only the bundled UI does."
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    if not FRONTEND_DIR.is_dir():
        return _fail(f"no frontend at {FRONTEND_DIR}")

    if not args.skip_build:
        npm = _find_npm()
        if npm is None:
            return _fail(NODE_HINT)
        if not args.skip_install:
            code = _install(npm)
            if code != 0:
                return code
        code = _run([npm, "run", "build"], "building the frontend")
        if code != 0:
            return _fail("`npm run build` failed; the errors above are from the frontend build")

    if not (DIST_DIR / "index.html").is_file():
        return _fail(
            f"{DIST_DIR / 'index.html'} does not exist. "
            "The build produced no bundle, so there is nothing to package."
        )

    _install_bundle()
    print(f"Bundle installed at {STATIC_DIR}")
    print(f"  {_count_files(STATIC_DIR)} files, {_total_bytes(STATIC_DIR) / 1024:.0f} KiB")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_ui.py",
        description="Build the React dashboard and copy it into the Python package.",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="do not run npm ci/install; assume node_modules is current",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="do not run npm at all; package the existing ml_frontend/dist",
    )
    return parser.parse_args(argv)


def _install(npm: str) -> int:
    """Try ``npm ci``, fall back to ``npm install``.

    ``ci`` fails when the lockfile is missing or out of sync with package.json.
    """
    if (FRONTEND_DIR / "package-lock.json").is_file():
        if _run([npm, "ci"], "installing frontend dependencies (npm ci)") == 0:
            return 0
        print("`npm ci` failed; falling back to `npm install`.", file=sys.stderr, flush=True)
    return _run([npm, "install"], "installing frontend dependencies (npm install)")


def _run(command: list[str], description: str) -> int:
    # Flush so the header prints before npm's output when stdout is piped.
    print(f"> {description}", flush=True)
    print(f"  {' '.join(command)}  (in {FRONTEND_DIR})", flush=True)
    try:
        completed = subprocess.run(command, cwd=FRONTEND_DIR, check=False)
    except OSError as exc:
        print(f"could not run {command[0]}: {exc}", file=sys.stderr)
        return 1
    return completed.returncode


def _find_npm() -> str | None:
    """On Windows npm is a ``.cmd`` shim, so try both names."""
    for name in ("npm", "npm.cmd"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _install_bundle() -> None:
    """Replace the packaged bundle with the new build.

    Staged and swapped so an interrupted copy can't leave a mix of two builds.
    """
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    shutil.copytree(DIST_DIR, STAGING_DIR)

    if STATIC_DIR.exists():
        shutil.rmtree(STATIC_DIR)
    STATIC_DIR.parent.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.replace(STATIC_DIR)


def _count_files(directory: Path) -> int:
    return sum(1 for path in directory.rglob("*") if path.is_file())


def _total_bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
