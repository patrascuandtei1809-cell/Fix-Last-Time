"""Foreground AlphaTrade worker entrypoint for systemd.

The worker owns the scanner and existing TradingBot loop.  It acquires the
cross-process execution lock before loading authenticated clients or building
the bot, and holds it until all threads have stopped.
"""
from __future__ import annotations

import signal
import threading
from typing import Any, Callable, Optional

from execution_lock import (
    acquire_process_execution_lock,
    get_process_role,
    release_process_execution_lock,
)


EXIT_OK = 0
EXIT_WRONG_ROLE = 64
EXIT_DUPLICATE = 73
EXIT_START_FAILED = 74


class HeadlessWorker:
    def __init__(
        self,
        *,
        bootstrap: Optional[Callable[[], Any]] = None,
        scanner_start: Optional[Callable[..., bool]] = None,
        scanner_stop: Optional[Callable[..., None]] = None,
        heartbeat_write: Optional[Callable[..., None]] = None,
        wait_interval: float = 5.0,
        role: Optional[str] = None,
    ):
        if bootstrap is None:
            from runtime_bootstrap import bootstrap_runtime

            bootstrap = bootstrap_runtime
        if scanner_start is None or scanner_stop is None:
            import scanner

            scanner_start = scanner_start or scanner.start_scanner_daemon
            scanner_stop = scanner_stop or scanner.stop_scanner_daemon
        if heartbeat_write is None:
            from heartbeats import write

            heartbeat_write = write

        self._bootstrap = bootstrap
        self._scanner_start = scanner_start
        self._scanner_stop = scanner_stop
        self._heartbeat_write = heartbeat_write
        self._wait_interval = max(0.05, float(wait_interval))
        self._role = get_process_role(role)
        self._stop_event = threading.Event()
        self._context = None
        self._scanner_started = False

    def request_stop(self, *_args) -> None:
        self._stop_event.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    def _heartbeat(self, state: str, **extra) -> None:
        payload = {"state": state, "role": self._role}
        payload.update(extra)
        try:
            self._heartbeat_write("worker", payload)
        except Exception as exc:
            print(f"[WORKER] heartbeat failed: {exc}", flush=True)

    def run(self) -> int:
        if self._role != "worker":
            print(
                f"[WORKER] refusing startup in process role={self._role!r}",
                flush=True,
            )
            return EXIT_WRONG_ROLE

        lease = acquire_process_execution_lock(
            owner="alphatrade-headless-worker",
            role=self._role,
        )
        if lease is None:
            print("[WORKER] duplicate execution owner detected; exiting", flush=True)
            return EXIT_DUPLICATE

        self._heartbeat("starting")
        bot_instance = None
        try:
            self._context = self._bootstrap()
            bot_instance = self._context.bot
            self._scanner_started = bool(
                self._scanner_start(
                    interval_sec=120,
                    on_log=self._context.log_activity,
                    stop_event=self._stop_event,
                )
            )
            started = bool(bot_instance.start())
            if not started and not bool(bot_instance.is_running()):
                self._heartbeat("start_failed")
                return EXIT_START_FAILED

            print("[WORKER] live trading loop started headlessly", flush=True)
            while not self._stop_event.wait(self._wait_interval):
                running = bool(bot_instance.is_running())
                self._heartbeat(
                    "running" if running else "halted",
                    bot_running=running,
                    scanner_running=self._scanner_started,
                )
            return EXIT_OK
        except Exception as exc:
            self._heartbeat("error", error=type(exc).__name__)
            print(f"[WORKER] startup/runtime error: {exc}", flush=True)
            return EXIT_START_FAILED
        finally:
            if bot_instance is not None:
                try:
                    bot_instance.stop()
                except Exception as exc:
                    print(f"[WORKER] bot stop failed: {exc}", flush=True)
                thread = getattr(bot_instance, "_thread", None)
                if thread is not None and thread.is_alive():
                    thread.join(timeout=30)
            if self._scanner_started:
                try:
                    self._scanner_stop(timeout=30)
                except Exception as exc:
                    print(f"[WORKER] scanner stop failed: {exc}", flush=True)
            self._heartbeat("stopped")
            release_process_execution_lock()


def main() -> int:
    worker = HeadlessWorker()
    worker.install_signal_handlers()
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
