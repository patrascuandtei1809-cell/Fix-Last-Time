"""Regression tests for SCANNER ROTATION (Task #41) + the SPLIT-ROUTING plan.

Operator spec (June 2026):
  - Binance auto-trades ONLY BTC/ETH/SOL (Market-Low, pinned to Binance, NEVER
    rotated).
  - The MEXC scanner auto-trades the top volatile alts (EXCLUDING the 3 majors)
    and rotates out coins that go quiet, but never drops a symbol that has an
    OPEN position.

All tests are OFFLINE — scanner output and open trades are injected via
monkeypatch, no network and no API keys.
"""
import types

import pytest

import bot
from risk import GlobalRiskManager, GlobalRiskSettings


# ── resolve_live_plan: majors pinned to binance + mexc alts excluding majors ──
def test_resolve_live_plan_pins_majors_to_binance(monkeypatch):
    monkeypatch.setattr(
        bot, "resolve_scanner_opportunities",
        lambda mode, top_n=3: [
            {"symbol": "DOGEUSDT", "exchange": "mexc"},
            {"symbol": "WLDUSDT",  "exchange": "mexc"},
            {"symbol": "PEPEUSDT", "exchange": "mexc"},
        ],
    )
    plan = bot.resolve_live_plan(top_n_mexc=3)
    majors = [p for p in plan if p["exchange"] == "binance"]
    assert [p["symbol"] for p in majors] == bot.BINANCE_MAJORS
    mexc = [p for p in plan if p["exchange"] == "mexc"]
    assert len(mexc) == 3
    assert all(p["symbol"] not in bot.BINANCE_MAJORS for p in mexc)


def test_resolve_live_plan_excludes_majors_from_mexc(monkeypatch):
    # Scanner surfaces a major on MEXC — it must NOT be added (already pinned to
    # binance; the same coin is never traded on both venues).
    monkeypatch.setattr(
        bot, "resolve_scanner_opportunities",
        lambda mode, top_n=3: [
            {"symbol": "ETHUSDT",  "exchange": "mexc"},   # major → skip
            {"symbol": "DOGEUSDT", "exchange": "mexc"},
            {"symbol": "WLDUSDT",  "exchange": "mexc"},
        ],
    )
    plan = bot.resolve_live_plan(top_n_mexc=3)
    syms = [p["symbol"] for p in plan]
    assert syms.count("ETHUSDT") == 1                 # only the pinned binance one
    eth = next(p for p in plan if p["symbol"] == "ETHUSDT")
    assert eth["exchange"] == "binance"
    mexc = [p["symbol"] for p in plan if p["exchange"] == "mexc"]
    assert "ETHUSDT" not in mexc
    assert set(mexc) == {"DOGEUSDT", "WLDUSDT"}


def test_resolve_live_plan_returns_majors_when_scanner_empty(monkeypatch):
    monkeypatch.setattr(bot, "resolve_scanner_opportunities",
                        lambda mode, top_n=3: [])
    plan = bot.resolve_live_plan(top_n_mexc=3)
    assert [p["symbol"] for p in plan] == bot.BINANCE_MAJORS
    assert all(p["exchange"] == "binance" for p in plan)


# ── Rotation orchestration ────────────────────────────────────────────────────
def _fake_worker(symbol: str, venue: str):
    ex = types.SimpleNamespace(name=venue)
    return types.SimpleNamespace(symbol=symbol, exchange=ex, _on_candidate=None)


def _make_bot(workers):
    b = bot.TradingBot(
        workers=workers,
        global_risk=GlobalRiskManager(GlobalRiskSettings()),
        check_every=2,
        initial_balance=1000.0,
    )
    b._scanner_rotation_on = True
    b._scanner_top_n = 3
    b._rotation_interval_sec = 0          # disable throttle for the test
    b._worker_factory = _fake_worker
    return b


def _rotate(monkeypatch, workers, scanner_out, open_trades):
    monkeypatch.setattr(
        bot, "resolve_scanner_opportunities",
        lambda mode, top_n=3: [{"symbol": s, "exchange": "mexc"} for s in scanner_out],
    )
    monkeypatch.setattr(bot, "get_open_trades", lambda: list(open_trades))
    b = _make_bot(workers)
    b._maybe_rotate_scanner_symbols()
    return b


def test_rotation_drops_idle_and_adds_new(monkeypatch):
    workers = {
        "binance:BTCUSDT": _fake_worker("BTCUSDT", "binance"),
        "mexc:DOGEUSDT":   _fake_worker("DOGEUSDT", "mexc"),
        "mexc:WLDUSDT":    _fake_worker("WLDUSDT", "mexc"),
    }
    # New top-3 drops DOGE/WLD, brings in three fresh alts. No open positions.
    b = _rotate(monkeypatch, workers,
                scanner_out=["PEPEUSDT", "SHIBUSDT", "FLOKIUSDT"],
                open_trades=[])
    syms = {w.symbol for w in b.workers.values()}
    assert "DOGEUSDT" not in syms and "WLDUSDT" not in syms     # idle → dropped
    assert {"PEPEUSDT", "SHIBUSDT", "FLOKIUSDT"} <= syms        # new → added
    assert "BTCUSDT" in syms                                    # pinned kept


def test_rotation_keeps_open_position_even_if_dropped(monkeypatch):
    workers = {
        "mexc:DOGEUSDT": _fake_worker("DOGEUSDT", "mexc"),
        "mexc:WLDUSDT":  _fake_worker("WLDUSDT", "mexc"),
    }
    # DOGE no longer in scanner top-N, BUT it has an OPEN position → must stay.
    b = _rotate(monkeypatch, workers,
                scanner_out=["PEPEUSDT", "SHIBUSDT", "FLOKIUSDT"],
                open_trades=[{"coin": "DOGEUSDT", "exchange": "mexc"}])
    syms = {w.symbol for w in b.workers.values()}
    assert "DOGEUSDT" in syms          # open → never dropped
    assert "WLDUSDT" not in syms       # idle + dropped-out → removed


def test_rotation_never_drops_pinned_major(monkeypatch):
    workers = {
        "binance:BTCUSDT": _fake_worker("BTCUSDT", "binance"),
        "binance:ETHUSDT": _fake_worker("ETHUSDT", "binance"),
        "binance:SOLUSDT": _fake_worker("SOLUSDT", "binance"),
        "mexc:DOGEUSDT":   _fake_worker("DOGEUSDT", "mexc"),
    }
    b = _rotate(monkeypatch, workers,
                scanner_out=["PEPEUSDT", "SHIBUSDT", "FLOKIUSDT"],
                open_trades=[])
    syms = {w.symbol for w in b.workers.values()}
    assert set(bot.BINANCE_MAJORS) <= syms     # all 3 majors kept


def test_rotation_noop_when_scanner_empty(monkeypatch):
    workers = {"mexc:DOGEUSDT": _fake_worker("DOGEUSDT", "mexc")}
    b = _rotate(monkeypatch, workers, scanner_out=[], open_trades=[])
    syms = {w.symbol for w in b.workers.values()}
    assert syms == {"DOGEUSDT"}          # empty ranking → no churn


def test_rotation_disabled_does_nothing(monkeypatch):
    workers = {"mexc:DOGEUSDT": _fake_worker("DOGEUSDT", "mexc")}
    monkeypatch.setattr(
        bot, "resolve_scanner_opportunities",
        lambda mode, top_n=3: [{"symbol": "PEPEUSDT", "exchange": "mexc"}])
    monkeypatch.setattr(bot, "get_open_trades", lambda: [])
    b = _make_bot(workers)
    b._scanner_rotation_on = False       # OFF
    b._maybe_rotate_scanner_symbols()
    assert set(b.workers) == {"mexc:DOGEUSDT"}


def test_rotation_throttle_prevents_rapid_rerun(monkeypatch):
    workers = {"mexc:DOGEUSDT": _fake_worker("DOGEUSDT", "mexc")}
    b = _rotate(monkeypatch, workers,
                scanner_out=["PEPEUSDT", "SHIBUSDT", "FLOKIUSDT"],
                open_trades=[])
    # First call rotated (interval=0). Now set a long interval and confirm a
    # second immediate call is a no-op (throttled).
    after_first = set(b.workers)
    b._rotation_interval_sec = 9999
    b._maybe_rotate_scanner_symbols()
    assert set(b.workers) == after_first
