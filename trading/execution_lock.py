"""Cross-process ownership guard for AlphaTrade live execution.

The existing ``bot.py`` singleton prevents duplicate loops only inside one
Python process.  Streamlit and the headless worker are separate processes, so
live execution also needs an operating-system lock held for the entire process
lifetime.

The lock contains metadata for diagnostics, but the metadata is never used as
the authority.  The kernel lock is authoritative and is released automatically
when the owning process exits.
"""
from __future__ import annotations

import atexit
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


ROLE_ENV = "ALPHATRADE_PROCESS_ROLE"
LOCK_PATH_ENV = "ALPHATRADE_EXECUTION_LOCK"


class DuplicateExecutionError(RuntimeError):
    """Raised when another process already owns live trading execution."""


def get_process_role(value: Optional[str] = None) -> str:
    """Return ``worker``, ``dashboard`` or the backward-compatible ``legacy``."""
    raw = value if value is not None else os.environ.get(ROLE_ENV, "legacy")
    role = str(raw or "legacy").strip().lower()
    return role if role in {"worker", "dashboard", "legacy"} else "legacy"


def role_allows_local_execution(role: Optional[str] = None) -> bool:
    """Dashboard-only processes are never allowed to start a live bot loop."""
    return get_process_role(role) in {"worker", "legacy"}


def default_lock_path() -> Path:
    configured = os.environ.get(LOCK_PATH_ENV)
    if configured:
        return Path(configured)
    try:
        uid = os.getuid()
    except AttributeError:  # Windows unit tests
        uid = os.getpid()
    return Path(tempfile.gettempdir()) / f"alphatrade-execution-{uid}.lock"


class ExecutionLock:
    """Non-blocking, cross-platform exclusive file lock."""

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self.path = Path(path) if path is not None else default_lock_path()
        self._file = None
        self._held = False
        self._owner_pid: Optional[int] = None

    @property
    def held(self) -> bool:
        return bool(self._held and self._owner_pid == os.getpid())

    def acquire(self, *, owner: str = "alphatrade", role: Optional[str] = None) -> bool:
        if self.held:
            return True
        if not role_allows_local_execution(role):
            return False

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b", buffering=0)
        try:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"\0")
            fh.seek(0)
            self._lock_file(fh)
        except (BlockingIOError, OSError):
            fh.close()
            return False

        self._file = fh
        self._held = True
        self._owner_pid = os.getpid()
        self._write_metadata(owner=owner, role=get_process_role(role))
        return True

    def _lock_file(self, fh) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _write_metadata(self, *, owner: str, role: str) -> None:
        if not self._file:
            return
        payload = {
            "pid": os.getpid(),
            "owner": owner,
            "role": role,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        self._file.seek(1)
        self._file.truncate()
        self._file.write(raw)
        self._file.flush()

    def release(self) -> None:
        fh = self._file
        if fh is None:
            self._held = False
            self._owner_pid = None
            return
        try:
            fh.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
            self._file = None
            self._held = False
            self._owner_pid = None

    def __enter__(self) -> "ExecutionLock":
        if not self.acquire():
            raise DuplicateExecutionError(
                f"AlphaTrade execution is already owned: {self.path}"
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


_process_guard = threading.RLock()
_process_lock: Optional[ExecutionLock] = None


def acquire_process_execution_lock(
    *,
    path: Optional[os.PathLike | str] = None,
    owner: str = "alphatrade",
    role: Optional[str] = None,
) -> Optional[ExecutionLock]:
    """Acquire or reuse the one execution lease held by this process."""
    global _process_lock
    if not role_allows_local_execution(role):
        return None
    with _process_guard:
        if _process_lock is not None and _process_lock.held:
            return _process_lock
        candidate = ExecutionLock(path)
        if not candidate.acquire(owner=owner, role=role):
            return None
        _process_lock = candidate
        return candidate


def process_has_execution_lock() -> bool:
    with _process_guard:
        return bool(_process_lock is not None and _process_lock.held)


def release_process_execution_lock() -> None:
    global _process_lock
    with _process_guard:
        current = _process_lock
        _process_lock = None
        if current is not None:
            current.release()


def read_lock_metadata(path: Optional[os.PathLike | str] = None) -> dict:
    """Best-effort diagnostic read; never substitutes for acquiring the lock."""
    target = Path(path) if path is not None else default_lock_path()
    try:
        with target.open("rb") as fh:
            fh.seek(1)
            raw = fh.read().decode("utf-8")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


atexit.register(release_process_execution_lock)
