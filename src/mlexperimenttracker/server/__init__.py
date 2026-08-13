"""The HTTP half of the package.

Deliberately empty of eager imports. ``mlexperimenttracker.server`` is reachable from a
training script that installed the package without the ``server`` extra, and importing
FastAPI from here would turn that into an :class:`ImportError` at import time rather than
at the moment someone actually asks for a server. :func:`create_app` is therefore
resolved lazily, so ``from mlexperimenttracker.server import create_app`` still works and
still fails with FastAPI's own message when the extra is missing.
"""

from __future__ import annotations

from typing import Any

__all__ = ["create_app", "serve"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import app as _app

        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
