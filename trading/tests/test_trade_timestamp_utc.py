"""Tests that freshly recorded trade timestamps are tz-aware UTC.

Trades used to be written with the server's local wall-clock time
(``datetime.now().isoformat()``) with NO timezone attached. If the trading
server's timezone ever changed (droplet move, DST transition) historical
times became ambiguous and chart markers could land on the wrong candle.
Writing UTC tz-aware ISO 8601 (``...+00:00``) at the source makes every
recorded time unambiguous forever.

This locks in:
  * a bot OPEN records ``open_time`` as tz-aware UTC,
  * a CLOSE records ``close_time`` as tz-aware UTC,
  * the chart marker reader still tolerates BOTH old (naive-local) and new
    (UTC-aware) records.

Pure / offline — no network, no Streamlit, no Binance.
"""
from datetime import datetime, timezone

import pandas as pd

import bot as bot_module
from risk import RiskManager, SymbolRiskSettings
from symbol_worker import SymbolWorker
from chart_markers import build_trade_markers


class _FakeExchange:
    """Minimal Exchange stand-in: a successful LIVE buy + ample balance."""
    name = "binance"
    client = object()

    def get_balance(self, asset):
        return {"free": 1000.0, "total": 1000.0}

    def round_quantity(self, symbol, qty):
        return round(qty, 6)

    def place_buy_order(self, symbol, invested):
        return {"ok": True, "price": 60000.0, "qty": invested / 60000.0, "fee": 0.0}

    def place_sell_order(self, symbol, qty):
        return {"ok": True, "price": 60000.0, "qty": qty, "fee": 0.0}


def _is_utc_aware_iso(s: str) -> bool:
    """A recorded timestamp must parse to a tz-aware datetime at UTC offset."""
    dt = datetime.fromisoformat(s)
    return dt.tzinfo is not None and dt.utcoffset() == timezone.utc.utcoffset(None)


def test_bot_open_time_is_tz_aware_utc():
    captured = {}
    worker = SymbolWorker(
        exchange=_FakeExchange(),
        symbol="BTCUSDT",
        strategy="Active Scalper",
        risk_manager=RiskManager(SymbolRiskSettings()),
        on_open_trade=lambda t: captured.update(t) or t,
    )
    ev = {
        "signal": "BUY", "reason": "test", "confidence": 80,
        "price": 60000.0, "score": 80, "regime": "TREND", "my_open": [],
    }
    placed = worker.execute_entry(ev, [], lambda amt, sym: (True, ""))

    assert placed is not False
    assert captured.get("open_time"), "open_time must be recorded"
    assert _is_utc_aware_iso(captured["open_time"]), \
        f"open_time not tz-aware UTC: {captured['open_time']!r}"
    assert captured["open_time"].endswith("+00:00")


def test_close_time_is_tz_aware_utc(tmp_path, monkeypatch):
    """bot.close_trade stamps close_time as tz-aware UTC."""
    monkeypatch.setattr(bot_module, "TRADES_DIR", str(tmp_path))
    # Isolate the unrelated activity-log side effect: close_trade calls
    # log_activity while holding _file_lock, so exercising the real logger here
    # would couple this timestamp test to that dependency. No-op keeps it
    # hermetic and focused on the close_time write.
    monkeypatch.setattr(bot_module, "log_activity", lambda *a, **k: None)
    trade = {
        "id": "t1", "coin": "BTCUSDT", "exchange": "binance", "type": "bot",
        "side": "BUY", "entry_price": 60000.0, "quantity": 0.001,
        "invested": 60.0, "entry_fee": 0.0, "open_time": "2026-06-11T10:00:00+00:00",
        "close_time": None, "status": "open",
    }
    fp = bot_module._trade_file_for("binance", "BTCUSDT")
    bot_module._save_trade_file(fp, [trade])

    closed = bot_module.close_trade("t1", exit_price=60300.0, reason="test close")

    assert closed is not None
    assert closed.get("close_time"), "close_time must be recorded"
    assert _is_utc_aware_iso(closed["close_time"]), \
        f"close_time not tz-aware UTC: {closed['close_time']!r}"
    assert closed["close_time"].endswith("+00:00")


def test_marker_reader_tolerates_old_and_new_timestamps():
    """Old naive-local AND new UTC-aware open_time both resolve to a marker."""
    # Wide window (09:00–15:00 London) so both timestamp forms land in range
    # regardless of the host's UTC offset when reading the naive value.
    candles = pd.date_range("2026-06-06 09:00:00", periods=360, freq="1min")
    base = dict(
        coin="BTCUSDT", type="bot", side="BUY", entry_price=60720.0,
        exit_price=None, close_time=None, status="open",
        stop_loss=60480.0, take_profit=61024.0,
    )
    new_utc = dict(base, id="new", open_time="2026-06-06T11:30:00+00:00")
    old_naive = dict(base, id="old", open_time="2026-06-06T11:30:00")

    res = build_trade_markers([new_utc, old_naive], "BTCUSDT", candles)
    assert res.buy_drawn == 2, f"both timestamp forms should match: {res.unmatched}"
