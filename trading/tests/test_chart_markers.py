"""Tests for chart_markers.build_trade_markers — the BUY/SELL chart marker
builder. Covers the reported "trades recorded but no markers" bug, the
BTCUSDT verification scenario (BUY near 60720 / SELL near 60692), timezone
coercion, candle snapping, and the unmatched-trade accounting.

Pure / offline — no network, no Streamlit, no Binance.
"""
import pandas as pd
import pytest

from chart_markers import build_trade_markers


# ── helpers ────────────────────────────────────────────────────────────────
def _candles(n=180, start="2026-06-06 10:00:00", freq="1min"):
    """Naive Europe/London wall-clock candle axis, exactly like the real
    df_chart["open_time"] (Binance UTC klines -> London -> tz-stripped)."""
    return pd.date_range(start=start, periods=n, freq=freq)


def _trade(**kw):
    base = dict(
        id="trade-1", coin="BTCUSDT", type="bot", side="BUY",
        entry_price=60720.0, exit_price=None,
        open_time=None, close_time=None, status="open",
        stop_loss=60480.0, take_profit=61024.0,
    )
    base.update(kw)
    return base


# ── core behaviour ──────────────────────────────────────────────────────────
def test_open_trade_draws_buy_marker_only():
    candles = _candles()
    # open_time stored UTC-aware (like _utcnow().isoformat()); 11:30 UTC == 12:30 London (BST)
    t = _trade(open_time="2026-06-06T11:30:00+00:00", status="open")
    res = build_trade_markers([t], "BTCUSDT", candles)

    assert res.trades_found == 1
    assert res.buy_drawn == 1
    assert res.sell_drawn == 0
    assert res.unmatched_count == 0
    # UTC 11:30 -> London 12:30 wall-clock, snapped onto the 12:30 candle
    assert res.buy[0].x == pd.Timestamp("2026-06-06 12:30:00")
    assert res.buy[0].y == 60720.0


def test_coarse_candle_index_unit_does_not_raise():
    """Regression: real droplet candle axis can be seconds-resolution while a
    trade timestamp carries microseconds. pandas refuses the lossy unit
    conversion in searchsorted ('Cannot losslessly convert units'); the builder
    must normalize both sides to ns and still place the marker."""
    candles = _candles().as_unit("s")          # coarse (seconds) axis
    assert candles.dtype == "datetime64[s]"
    t = _trade(open_time="2026-06-06T11:30:00.500000+00:00", status="open")
    res = build_trade_markers([t], "BTCUSDT", candles)
    assert res.buy_drawn == 1
    assert res.unmatched_count == 0
    assert res.buy[0].x == pd.Timestamp("2026-06-06 12:30:00")


def test_closed_trade_draws_buy_and_sell():
    candles = _candles()
    t = _trade(
        open_time="2026-06-06T11:30:00+00:00",
        close_time="2026-06-06T11:42:00+00:00",
        exit_price=60692.0, status="closed",
    )
    res = build_trade_markers([t], "BTCUSDT", candles)

    assert res.buy_drawn == 1 and res.sell_drawn == 1
    assert res.unmatched_count == 0
    assert res.buy[0].y == 60720.0
    assert res.sell[0].y == 60692.0
    assert res.sell[0].x == pd.Timestamp("2026-06-06 12:42:00")


def test_btcusdt_verification_scenario():
    """Spec verification: BUY icon near 60720, SELL icon near 60692."""
    candles = _candles()
    trades = [
        _trade(id="t-buy", open_time="2026-06-06T11:30:00+00:00",
               close_time="2026-06-06T11:55:00+00:00",
               entry_price=60720.0, exit_price=60692.0, status="closed"),
    ]
    res = build_trade_markers(trades, "BTCUSDT", candles)
    assert res.trades_found == 1
    assert res.buy_drawn == 1 and res.sell_drawn == 1
    assert res.unmatched_count == 0
    assert res.buy[0].y == pytest.approx(60720.0)
    assert res.sell[0].y == pytest.approx(60692.0)


def test_naive_close_time_is_handled():
    """close_time is written naive (datetime.now().isoformat()). With the test
    env clock at UTC it must still land on the right London candle, not be
    dropped."""
    candles = _candles()
    t = _trade(
        open_time="2026-06-06T11:30:00+00:00",
        close_time="2026-06-06T11:45:00",  # naive
        exit_price=60700.0, status="closed",
    )
    res = build_trade_markers([t], "BTCUSDT", candles)
    assert res.sell_drawn == 1
    assert res.unmatched_count == 0


# ── matching / unmatched accounting ─────────────────────────────────────────
def test_open_time_before_history_is_unmatched():
    candles = _candles(start="2026-06-06 10:00:00")  # London 10:00 onward
    # 03:00 UTC == 04:00 London — well before the first candle
    t = _trade(open_time="2026-06-06T03:00:00+00:00", status="open")
    res = build_trade_markers([t], "BTCUSDT", candles)
    assert res.buy_drawn == 0
    assert res.unmatched_count == 1
    assert res.unmatched[0].kind == "BUY"
    assert "outside chart history" in res.unmatched[0].reason


def test_close_time_after_history_is_unmatched_sell_only():
    candles = _candles(n=60, start="2026-06-06 10:00:00")  # ends 10:59 London
    t = _trade(
        open_time="2026-06-06T09:30:00+00:00",   # 10:30 London — in range
        close_time="2026-06-06T20:00:00+00:00",  # 21:00 London — far past end
        exit_price=60800.0, status="closed",
    )
    res = build_trade_markers([t], "BTCUSDT", candles)
    assert res.buy_drawn == 1
    assert res.sell_drawn == 0
    assert res.unmatched_count == 1
    assert res.unmatched[0].kind == "SELL"


def test_missing_entry_price_is_unmatched():
    candles = _candles()
    t = _trade(open_time="2026-06-06T11:30:00+00:00", entry_price=0, status="open")
    res = build_trade_markers([t], "BTCUSDT", candles)
    assert res.buy_drawn == 0
    assert res.unmatched_count == 1
    assert "entry_price" in res.unmatched[0].reason


def test_wrong_symbol_is_filtered_out():
    candles = _candles()
    trades = [
        _trade(id="eth", coin="ETHUSDT", open_time="2026-06-06T11:30:00+00:00"),
        _trade(id="btc", coin="BTCUSDT", open_time="2026-06-06T11:30:00+00:00"),
    ]
    res = build_trade_markers(trades, "BTCUSDT", candles)
    assert res.trades_found == 1
    assert res.buy_drawn == 1
    assert res.buy[0].trade_id == "btc"


def test_snaps_to_nearest_candle():
    candles = _candles(freq="1min")
    # 11:30:40 UTC -> 12:30:40 London — nearest 1-min candle is 12:31
    t = _trade(open_time="2026-06-06T11:30:40+00:00", status="open")
    res = build_trade_markers([t], "BTCUSDT", candles)
    assert res.buy_drawn == 1
    assert res.buy[0].x == pd.Timestamp("2026-06-06 12:31:00")


def test_just_opened_trade_past_last_candle_within_tolerance():
    candles = _candles(n=60, start="2026-06-06 10:00:00")  # ends 10:59 London
    # 10:00:30 UTC -> 11:00:30 London — ~90s past last candle but within 1 candle tol? no.
    # use 09:59:30 UTC -> 10:59:30 London, 30s past last candle (within 1min tol)
    t = _trade(open_time="2026-06-06T09:59:30+00:00", status="open")
    res = build_trade_markers([t], "BTCUSDT", candles)
    assert res.buy_drawn == 1
    assert res.buy[0].x == pd.Timestamp("2026-06-06 10:59:00")


def test_empty_candles_marks_open_trade_unmatched():
    t = _trade(open_time="2026-06-06T11:30:00+00:00", status="open")
    res = build_trade_markers([t], "BTCUSDT", [])
    assert res.trades_found == 1
    assert res.buy_drawn == 0
    assert res.unmatched_count == 1
    assert res.unmatched[0].kind == "BUY"
    assert "no candle data" in res.unmatched[0].reason


def test_empty_candles_closed_trade_marks_both_buy_and_sell_unmatched():
    """Marker-level accounting: a closed trade owes BOTH a BUY and a SELL
    marker, so with no candle data it must contribute two unmatched entries."""
    t = _trade(
        open_time="2026-06-06T11:30:00+00:00",
        close_time="2026-06-06T11:45:00+00:00",
        exit_price=60692.0, status="closed",
    )
    res = build_trade_markers([t], "BTCUSDT", [])
    assert res.trades_found == 1
    assert res.unmatched_count == 2
    assert {u.kind for u in res.unmatched} == {"BUY", "SELL"}


def test_empty_candles_closed_status_missing_exit_still_counts_sell():
    """status='closed' but exit payload missing: still owes a SELL marker (it
    just can't be drawn), so empty-candles must count both BUY and SELL."""
    t = _trade(open_time="2026-06-06T11:30:00+00:00",
               status="closed", exit_price=None, close_time=None)
    res = build_trade_markers([t], "BTCUSDT", [])
    assert res.unmatched_count == 2
    assert {u.kind for u in res.unmatched} == {"BUY", "SELL"}


def test_no_trades_is_empty_result():
    candles = _candles()
    res = build_trade_markers([], "BTCUSDT", candles)
    assert res.trades_found == 0
    assert res.buy_drawn == 0 and res.sell_drawn == 0
    assert res.unmatched_count == 0


# ── out-of-range (benign) vs error (genuine) separation ─────────────────────
def test_out_of_range_is_flagged_not_error():
    """A trade older than the fetched candles must be flagged out_of_range
    (benign) — it goes in `out_of_range`, NOT `errors`, so the dashboard does
    not raise a scary warning for it."""
    candles = _candles(start="2026-06-06 10:00:00")
    t = _trade(open_time="2026-06-06T03:00:00+00:00", status="open")  # 04:00 London — before
    res = build_trade_markers([t], "BTCUSDT", candles)
    assert res.unmatched_count == 1
    assert res.out_of_range_count == 1
    assert res.error_count == 0
    assert res.unmatched[0].out_of_range is True
    assert res.out_of_range[0].kind == "BUY"


def test_missing_price_is_error_not_out_of_range():
    """Missing/zero entry_price is a genuine data problem -> error (warned),
    NOT out_of_range."""
    candles = _candles()
    t = _trade(open_time="2026-06-06T11:30:00+00:00", entry_price=0, status="open")
    res = build_trade_markers([t], "BTCUSDT", candles)
    assert res.error_count == 1
    assert res.out_of_range_count == 0
    assert res.errors[0].out_of_range is False


def test_no_candle_data_is_out_of_range_not_error():
    """No candles yet (chart loading) is benign -> out_of_range, not error."""
    t = _trade(open_time="2026-06-06T11:30:00+00:00", status="open")
    res = build_trade_markers([t], "BTCUSDT", [])
    assert res.out_of_range_count == 1
    assert res.error_count == 0
    assert res.out_of_range[0].out_of_range is True


def test_naive_and_aware_match_same_candle():
    """The same instant expressed naive-UTC vs aware-UTC must land on the SAME
    candle — proves the offset-naive/offset-aware path is unified (test env
    clock is UTC, so naive == UTC here)."""
    candles = _candles()
    aware = _trade(id="a", open_time="2026-06-06T11:30:00+00:00", status="open")
    naive = _trade(id="n", open_time="2026-06-06T11:30:00", status="open")
    ra = build_trade_markers([aware], "BTCUSDT", candles)
    rn = build_trade_markers([naive], "BTCUSDT", candles)
    assert ra.buy_drawn == 1 and rn.buy_drawn == 1
    assert ra.buy[0].x == rn.buy[0].x == pd.Timestamp("2026-06-06 12:30:00")
