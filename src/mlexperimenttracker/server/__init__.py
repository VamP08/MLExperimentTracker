"""HTTP server package.

Exports are resolved lazily so importing this without the ``server`` extra doesn't fail
until someone actually asks for the app.
"""

from __future__ import annotations

from typing import Any

__all__ = ["create_app", "serve"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import app as _app

        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
