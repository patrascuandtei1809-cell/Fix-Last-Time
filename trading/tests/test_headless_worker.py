"""Safety tests for the non-Streamlit AlphaTrade worker."""
from types import SimpleNamespace
import signal
import threading

import pytest

import bot as bot_module
import execution_lock as el
import scanner as scanner_module
from headless_worker import (
    EXIT_DUPLICATE,
    EXIT_OK,
    EXIT_WRONG_ROLE,
    HeadlessWorker,
)
from runtime_bootstrap import RuntimeBootstrapError, load_runtime_config


class _FakeBot:
    def __init__(self, order_calls=None):
        self.running = False
        self.start_calls = 0
        self.stop_calls = 0
        self.order_calls = order_calls if order_calls is not None else []
        self._thread = None

    def start(self):
        self.start_calls += 1
        self.running = True
        self.order_calls.append("worker-loop-started")
        return True

    def stop(self):
        self.stop_calls += 1
        self.running = False

    def is_running(self):
        return self.running


class _AggressiveStub:
    @staticmethod
    def get_mode():
        return "Persisted Mode"

    @staticmethod
    def get_profile(_mode):
        return {"check_every": 7}

    @staticmethod
    def apply_profile_to_risk(_settings, _mode):
        return None


class _LiveSettingsStub:
    @staticmethod
    def get_settings():
        return SimpleNamespace(
            min_volume_multiple=0.30,
            volume_filter_on=True,
            trend_filter_on=True,
        )


class _Client:
    def __init__(self, _key, _secret):
        pass

    def test_connection(self):
        return True, "ok"


@pytest.fixture(autouse=True)
def _clean_execution_state(monkeypatch):
    el.release_process_execution_lock()
    monkeypatch.setattr(bot_module, "_active_loop_owner", None)
    monkeypatch.setattr(bot_module, "log_activity", lambda *_a, **_k: None)
    monkeypatch.setattr(bot_module, "_tg_dispatch", lambda *_a, **_k: None)
    yield
    owner = getattr(bot_module, "_active_loop_owner", None)
    if owner is not None:
        stop = getattr(owner, "_test_stop_event", None)
        if stop is not None:
            stop.set()
        thread = getattr(owner, "_thread", None)
        if thread is not None:
            thread.join(timeout=5)
    bot_module._active_loop_owner = None
    el.release_process_execution_lock()


def _minimal_trading_bot(order_calls):
    instance = bot_module.TradingBot.__new__(bot_module.TradingBot)
    instance._thread = None
    instance._running = False
    instance.workers = {}
    instance.check_every = 1
    instance.global_risk = SimpleNamespace(
        settings=SimpleNamespace(max_total_exposure_usdt=1000.0)
    )
    stop_event = threading.Event()
    started_event = threading.Event()
    instance._test_stop_event = stop_event
    instance._test_started_event = started_event

    def _loop():
        order_calls.append("local-loop-started")
        started_event.set()
        stop_event.wait(5)

    instance._loop = _loop
    return instance


def _worker_with_fakes(tmp_path, monkeypatch, order_calls=None):
    monkeypatch.setenv(el.ROLE_ENV, "worker")
    monkeypatch.setenv(el.LOCK_PATH_ENV, str(tmp_path / "execution.lock"))
    fake_bot = _FakeBot(order_calls)
    context = SimpleNamespace(bot=fake_bot, log_activity=lambda *_a: None)
    scanner = {"started": 0, "stopped": 0}

    def scanner_start(**_kwargs):
        scanner["started"] += 1
        return True

    def scanner_stop(**_kwargs):
        scanner["stopped"] += 1

    worker = HeadlessWorker(
        bootstrap=lambda: context,
        scanner_start=scanner_start,
        scanner_stop=scanner_stop,
        heartbeat_write=lambda *_a, **_k: None,
        wait_interval=0.02,
        role="worker",
    )
    return worker, fake_bot, scanner


def test_headless_worker_starts_once_and_sigterm_stops_cleanly(
    tmp_path, monkeypatch
):
    worker, fake_bot, scanner = _worker_with_fakes(tmp_path, monkeypatch)
    result = []
    thread = threading.Thread(target=lambda: result.append(worker.run()))
    thread.start()

    for _ in range(100):
        if fake_bot.running:
            break
        threading.Event().wait(0.01)
    assert fake_bot.running is True

    worker.request_stop(signal.SIGTERM, None)
    thread.join(timeout=5)

    assert result == [EXIT_OK]
    assert fake_bot.start_calls == 1
    assert fake_bot.stop_calls == 1
    assert scanner == {"started": 1, "stopped": 1}
    assert el.process_has_execution_lock() is False


def test_second_worker_is_blocked_before_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv(el.ROLE_ENV, "worker")
    path = tmp_path / "execution.lock"
    monkeypatch.setenv(el.LOCK_PATH_ENV, str(path))
    existing = el.ExecutionLock(path)
    assert existing.acquire(owner="existing-worker", role="worker")
    bootstrap_calls = []
    worker = HeadlessWorker(
        bootstrap=lambda: bootstrap_calls.append(True),
        scanner_start=lambda **_k: True,
        scanner_stop=lambda **_k: None,
        heartbeat_write=lambda *_a, **_k: None,
        role="worker",
    )
    try:
        assert worker.run() == EXIT_DUPLICATE
        assert bootstrap_calls == []
    finally:
        existing.release()


def test_dashboard_role_cannot_start_a_live_bot_loop(tmp_path, monkeypatch):
    monkeypatch.setenv(el.ROLE_ENV, "dashboard")
    monkeypatch.setenv(el.LOCK_PATH_ENV, str(tmp_path / "execution.lock"))
    order_calls = []
    dashboard_bot = _minimal_trading_bot(order_calls)

    assert dashboard_bot.start() is False
    assert order_calls == []
    assert dashboard_bot._thread is None


def test_worker_dashboard_overlap_cannot_duplicate_orders(tmp_path, monkeypatch):
    order_calls = []
    worker, fake_bot, _scanner = _worker_with_fakes(
        tmp_path, monkeypatch, order_calls
    )
    result = []
    worker_thread = threading.Thread(target=lambda: result.append(worker.run()))
    worker_thread.start()
    for _ in range(100):
        if fake_bot.running:
            break
        threading.Event().wait(0.01)
    assert order_calls == ["worker-loop-started"]

    monkeypatch.setenv(el.ROLE_ENV, "dashboard")
    dashboard_bot = _minimal_trading_bot(order_calls)
    assert dashboard_bot.start() is False
    assert order_calls == ["worker-loop-started"]

    worker.request_stop()
    worker_thread.join(timeout=5)
    assert result == [EXIT_OK]


def test_two_local_dashboard_tabs_cannot_run_two_loops(tmp_path, monkeypatch):
    monkeypatch.setenv(el.ROLE_ENV, "legacy")
    monkeypatch.setenv(el.LOCK_PATH_ENV, str(tmp_path / "execution.lock"))
    order_calls = []
    first = _minimal_trading_bot(order_calls)
    second = _minimal_trading_bot(order_calls)

    assert first.start() is True
    assert first._test_started_event.wait(2)
    assert second.start() is False
    assert order_calls == ["local-loop-started"]

    first._test_stop_event.set()
    first._thread.join(timeout=5)


def test_worker_entrypoint_refuses_non_worker_role():
    worker = HeadlessWorker(
        bootstrap=lambda: None,
        scanner_start=lambda **_k: True,
        scanner_stop=lambda **_k: None,
        heartbeat_write=lambda *_a, **_k: None,
        role="dashboard",
    )
    assert worker.run() == EXIT_WRONG_ROLE


def test_scanner_daemon_refuses_dashboard_role(monkeypatch):
    monkeypatch.setenv(el.ROLE_ENV, "dashboard")
    assert scanner_module.start_scanner_daemon(interval_sec=15) is False


def test_scanner_daemon_can_stop_cleanly(monkeypatch):
    monkeypatch.setenv(el.ROLE_ENV, "worker")
    scanned = threading.Event()
    monkeypatch.setattr(
        scanner_module,
        "scan",
        lambda **_kwargs: (
            scanned.set()
            or {"opportunities": [], "count_scored": 0}
        ),
    )

    assert scanner_module.start_scanner_daemon(interval_sec=15) is True
    assert scanned.wait(2)
    scanner_module.stop_scanner_daemon(timeout=2)
    assert scanner_module.is_daemon_running() is False


def test_runtime_bootstrap_preserves_persisted_risk_values():
    config = load_runtime_config(
        persisted={
            "risk": {
                "stop_loss_pct": 9.1,
                "take_profit_pct": 8.2,
                "cooldown_seconds": 77,
                "dynamic_size_pct": 12.5,
            },
            "global_risk": {
                "max_daily_loss_pct": 3.3,
                "max_total_exposure_usdt": 432.1,
            },
        },
        credentials_loader=lambda: ("binance-key", "binance-secret"),
        mexc_credentials_loader=lambda: ("mexc-key", "mexc-secret"),
        binance_client_factory=_Client,
        aggressive_module=_AggressiveStub,
        live_settings_module=_LiveSettingsStub,
    )

    assert config.risk_settings.stop_loss_pct == 9.1
    assert config.risk_settings.take_profit_pct == 8.2
    assert config.risk_settings.cooldown_seconds == 77
    assert config.risk_settings.dynamic_size_pct == 12.5
    assert config.global_risk_settings.max_daily_loss_pct == 3.3
    assert config.global_risk_settings.max_total_exposure_usdt == 432.1
    assert config.min_volume_multiple == 0.30


def test_runtime_bootstrap_fails_closed_if_volume_threshold_drifts():
    bad_live = SimpleNamespace(
        get_settings=lambda: SimpleNamespace(
            min_volume_multiple=1.0,
            volume_filter_on=True,
            trend_filter_on=True,
        )
    )
    with pytest.raises(RuntimeBootstrapError, match="min_volume_multiple"):
        load_runtime_config(
            persisted={},
            credentials_loader=lambda: ("binance-key", "binance-secret"),
            mexc_credentials_loader=lambda: ("mexc-key", "mexc-secret"),
            binance_client_factory=_Client,
            aggressive_module=_AggressiveStub,
            live_settings_module=bad_live,
        )
