"""Optional disk persistence for sessions (crash recovery, local use)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Optional

STORE_DIR = Path(tempfile.gettempdir()) / "tarteeb_abu_alyaa_sessions"
STORE_DIR.mkdir(parents=True, exist_ok=True)


def _path(session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64]
    return STORE_DIR / f"{safe}.json"


def save_session_snapshot(session_id: str, payload: dict) -> None:
    """Save a JSON-serializable snapshot (results/summary/targets only)."""
    try:
        p = _path(session_id)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_session_snapshot(session_id: str) -> Optional[dict]:
    p = _path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_snapshots(limit: int = 20) -> list[dict[str, Any]]:
    items = []
    for f in sorted(STORE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[
        :limit
    ]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items.append(
                {
                    "session_id": f.stem,
                    "phase": data.get("phase"),
                    "summary": data.get("summary"),
                    "mtime": f.stat().st_mtime,
                }
            )
        except Exception:
            continue
    return items
