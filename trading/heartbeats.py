"""Process heartbeats — lightweight JSON files for 24/7 health monitoring.

Written by bot, scanner daemon, and dashboard. Never stores secrets.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_DIR = Path(__file__).resolve().parent / "data" / "heartbeats"
_DIR.mkdir(parents=True, exist_ok=True)


def _path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower())
    return _DIR / f"{safe}.json"


def write(name: str, extra: Optional[Dict[str, Any]] = None) -> None:
    payload = {
        "name": name,
        "at": datetime.now(timezone.utc).isoformat(),
        "ts": time.time(),
    }
    if extra:
        payload.update(extra)
    p = _path(name)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    tmp.replace(p)


def read(name: str, max_age_sec: float = 300.0) -> Optional[Dict[str, Any]]:
    p = _path(name)
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    ts = float(data.get("ts") or 0)
    if ts and max_age_sec > 0 and (time.time() - ts) > max_age_sec:
        data["stale"] = True
    else:
        data["stale"] = False
    return data
