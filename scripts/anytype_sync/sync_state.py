from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone


def load_state(path: str) -> dict[str, str]:
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save_state(path: str, state: dict[str, str]) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_last_sync(state: dict[str, str], space_id: str) -> str | None:
    return state.get(space_id)


def set_last_sync(state: dict[str, str], space_id: str, ts: str) -> None:
    state[space_id] = ts


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
