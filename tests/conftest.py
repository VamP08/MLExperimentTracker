"""Put ``src/`` on the path so the suite runs against the working tree.

Deliberately not dependent on an editable install: the tests are the thing that proves
the package is importable at all, so they must not require it to be installed first.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
