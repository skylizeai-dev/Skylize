"""OD6: the TS `LifecycleStatus` mirror (website/src/lib/skylize/types.ts)
must carry the exact same members as the Python `LifecycleStatus` Literal
(src/skylize/contracts/base.py). A regex that fails to find the TS union
must FAIL this test, not vacuously pass on an empty set.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from skylize.contracts.base import LifecycleStatus as PyLifecycleStatus

_TS_TYPES_PATH = (
    Path(__file__).resolve().parents[2]
    / "website"
    / "src"
    / "lib"
    / "skylize"
    / "types.ts"
)

_TS_UNION_RE = re.compile(r"export type LifecycleStatus\s*=\s*([^;]+);")


def _ts_lifecycle_status_members() -> set[str]:
    text = _TS_TYPES_PATH.read_text(encoding="utf-8")
    match = _TS_UNION_RE.search(text)
    assert match is not None, (
        f"could not find 'export type LifecycleStatus = ...;' in {_TS_TYPES_PATH}"
    )
    raw = match.group(1)
    members = {
        part.strip().strip('"').strip("'")
        for part in raw.split("|")
        if part.strip()
    }
    assert members, f"parsed an empty member set from: {raw!r}"
    return members


def test_ts_lifecycle_status_mirror_matches_python() -> None:
    py_members = set(get_args(PyLifecycleStatus))
    assert py_members, "typing.get_args(PyLifecycleStatus) returned an empty set"

    ts_members = _ts_lifecycle_status_members()

    assert py_members == ts_members, (
        f"LifecycleStatus drift: python={sorted(py_members)} "
        f"ts={sorted(ts_members)}"
    )
