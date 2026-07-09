"""Make scripts/anytype_sync importable during pytest runs."""
from __future__ import annotations

import pathlib
import sys

_SCRIPTS = str(pathlib.Path(__file__).parent.parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
