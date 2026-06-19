"""Cross-process safety tests for the AlphaTrade live execution lease."""
import multiprocessing
import os

import pytest

import execution_lock as el


def _hold_lock(path, ready, release):
    lock = el.ExecutionLock(path)
    ready.put(lock.acquire(owner="child-worker", role="worker"))
    release.wait(10)
    lock.release()


@pytest.fixture(autouse=True)
def _release_process_lease():
    el.release_process_execution_lock()
    yield
    el.release_process_execution_lock()


def test_execution_lock_is_exclusive(tmp_path):
    path = tmp_path / "execution.lock"
    first = el.ExecutionLock(path)
    second = el.ExecutionLock(path)

    assert first.acquire(owner="first", role="worker") is True
    assert second.acquire(owner="second", role="worker") is False

    first.release()
    assert second.acquire(owner="second", role="worker") is True
    second.release()


def test_execution_lock_blocks_a_second_process(tmp_path):
    path = str(tmp_path / "execution.lock")
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    release = ctx.Event()
    process = ctx.Process(target=_hold_lock, args=(path, ready, release))
    process.start()
    try:
        assert ready.get(timeout=10) is True
        contender = el.ExecutionLock(path)
        assert contender.acquire(owner="parent", role="worker") is False
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


def test_dashboard_role_cannot_acquire_execution(tmp_path, monkeypatch):
    monkeypatch.setenv(el.ROLE_ENV, "dashboard")
    monkeypatch.setenv(el.LOCK_PATH_ENV, str(tmp_path / "execution.lock"))

    assert el.role_allows_local_execution() is False
    assert el.acquire_process_execution_lock(owner="dashboard") is None
    assert not (tmp_path / "execution.lock").exists()


def test_process_lease_is_reused_only_by_its_owner_process(tmp_path, monkeypatch):
    monkeypatch.setenv(el.ROLE_ENV, "worker")
    monkeypatch.setenv(el.LOCK_PATH_ENV, str(tmp_path / "execution.lock"))

    first = el.acquire_process_execution_lock(owner="worker")
    second = el.acquire_process_execution_lock(owner="worker")

    assert first is not None
    assert second is first
    assert el.process_has_execution_lock() is True
    metadata = el.read_lock_metadata()
    assert metadata["pid"] == os.getpid()
    assert metadata["role"] == "worker"
