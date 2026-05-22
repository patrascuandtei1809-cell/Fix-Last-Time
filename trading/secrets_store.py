"""Server-side persistence for Binance API credentials.

LIVE-only. Credentials are stored at `data/.binance_creds.json` with file
mode 0o600 so only the process owner can read them. Never logged in full —
callers must mask to first 6 chars before printing.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Optional, Tuple

_CREDS_PATH = Path(__file__).parent / "data" / ".binance_creds.json"


def _ensure_dir() -> None:
    _CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)


def save_credentials(api_key: str, api_secret: str) -> None:
    """Persist LIVE Binance credentials with 0600 perms (owner read/write only)."""
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret are required")
    _ensure_dir()
    payload = {"api_key": api_key, "api_secret": api_secret}
    # Write atomically with restrictive perms from the start.
    tmp = _CREDS_PATH.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
    except Exception:
        try: tmp.unlink()
        except FileNotFoundError: pass
        raise
    os.replace(tmp, _CREDS_PATH)
    try:
        os.chmod(_CREDS_PATH, 0o600)
    except OSError:
        pass
    print(f"[CREDS] Saved LIVE Binance credentials (key={api_key[:6]}…) to "
          f"{_CREDS_PATH.name} mode=600", flush=True)


def load_credentials() -> Optional[Tuple[str, str]]:
    """Return (api_key, api_secret) if a saved file exists, else None."""
    if not _CREDS_PATH.exists():
        return None
    try:
        # Refuse to load if file perms are too open (group/other readable).
        st_mode = _CREDS_PATH.stat().st_mode
        if st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"[CREDS][WARN] {_CREDS_PATH.name} has loose perms "
                  f"({oct(st_mode & 0o777)}); tightening to 600.", flush=True)
            try: os.chmod(_CREDS_PATH, 0o600)
            except OSError: pass
        with _CREDS_PATH.open("r") as f:
            data = json.load(f)
        key    = (data.get("api_key") or "").strip()
        secret = (data.get("api_secret") or "").strip()
        if not key or not secret:
            return None
        return key, secret
    except Exception as e:
        print(f"[CREDS][ERROR] Failed to load credentials: {e}", flush=True)
        return None


def clear_credentials() -> bool:
    """Delete the saved credentials file. Returns True if a file was removed."""
    try:
        _CREDS_PATH.unlink()
        print(f"[CREDS] Cleared saved Binance credentials ({_CREDS_PATH.name})",
              flush=True)
        return True
    except FileNotFoundError:
        return False


def has_saved_credentials() -> bool:
    return _CREDS_PATH.exists()
