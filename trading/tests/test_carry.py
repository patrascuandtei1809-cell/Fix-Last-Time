"""Offline tests for the delta-neutral CARRY study (Task #13).

Two pure, network-free surfaces:
  • `backtest.carry_pnl` — the cash-flow accumulator (long spot + short perp,
    harvest 8h funding, 4 taker legs). Driven by tiny synthetic frames.
  • `research._carry_verdict` — the honest ACCEPT/REJECT gate (breadth + funding
    must beat costs), driven by synthetic per-symbol result dicts.

Carry is a cash-flow test, NOT a directional signal, and must never reach the
live allowlist — that exclusion is asserted here too.
"""
import numpy as np
import pandas as pd
from pytest import approx

import backtest as B
import research as R


# 8h in milliseconds — funding settles every 8h on OKX/Binance perps.
_8H = 8 * 3600 * 1000


def _candles(prices, *, start=0, step_ms=3600 * 1000):
    """Build an OKX-style [open_time, okx_close] frame from a price list."""
    return pd.DataFrame({
        "open_time": [start + i * step_ms for i in range(len(prices))],
        "okx_close": [float(p) for p in prices],
    })


def _funding(times_rates):
    return pd.DataFrame({
        "funding_time": [t for t, _ in times_rates],
        "funding_rate": [r for _, r in times_rates],
    })


# ── carry_pnl: structure / guards ────────────────────────────────────────────
def test_carry_insufficient_candles_not_held():
    res = B.carry_pnl(_candles([100]), _candles([100]), _funding([]),
                      spot_fee_pct=0.1, perp_fee_pct=0.05, slip_pct=0.02)
    assert res["held"] is False


def test_carry_no_overlap_not_held():
    spot = _candles([100, 101], start=0)
    perp = _candles([100, 101], start=10 * _8H)   # disjoint timestamps
    res = B.carry_pnl(spot, perp, _funding([]),
                      spot_fee_pct=0.1, perp_fee_pct=0.05, slip_pct=0.02)
    assert res["held"] is False


def test_carry_nonfinite_price_not_held():
    spot = _candles([100, float("nan")])
    perp = _candles([100, 101])
    res = B.carry_pnl(spot, perp, _funding([]),
                      spot_fee_pct=0.1, perp_fee_pct=0.05, slip_pct=0.02)
    assert res["held"] is False


# ── carry_pnl: economics ─────────────────────────────────────────────────────
def test_fees_are_four_taker_legs():
    """Flat prices, zero funding → P&L is exactly minus the four-leg cost."""
    flat = _candles([100, 100, 100])
    res = B.carry_pnl(flat, flat, _funding([]),
                      spot_fee_pct=0.10, perp_fee_pct=0.05, slip_pct=0.02)
    # 2*(0.10+0.02) + 2*(0.05+0.02) = 0.24 + 0.14 = 0.38%
    assert res["fees_pct"] == approx(0.38)
    assert res["funding_sum_pct"] == 0.0
    assert res["net_carry_pct"] == approx(-0.38)


def test_delta_neutral_cancels_price_move():
    """Spot and perp move together → basis P&L ~0 (price exposure cancels)."""
    spot = _candles([100, 110, 120])
    perp = _candles([100, 110, 120])
    res = B.carry_pnl(spot, perp, _funding([]),
                      spot_fee_pct=0.0, perp_fee_pct=0.0, slip_pct=0.0)
    assert res["basis_pnl_pct"] == approx(0.0)
    assert res["net_carry_pct"] == approx(0.0)


def test_positive_funding_is_received_by_short():
    """Two +0.01% settlements inside the hold → short receives +0.02% funding."""
    spot = _candles([100, 100, 100, 100], step_ms=_8H)
    perp = _candles([100, 100, 100, 100], step_ms=_8H)
    fund = _funding([(_8H, 0.0001), (2 * _8H, 0.0001)])  # settle at t1,t2 (in hold)
    res = B.carry_pnl(spot, perp, fund,
                      spot_fee_pct=0.0, perp_fee_pct=0.0, slip_pct=0.0)
    assert res["n_funding"] == 2
    assert res["funding_sum_pct"] == approx(0.02)
    assert res["net_carry_pct"] == approx(0.02)


def test_negative_funding_is_paid_by_short():
    spot = _candles([100, 100, 100], step_ms=_8H)
    perp = _candles([100, 100, 100], step_ms=_8H)
    fund = _funding([(_8H, -0.0003)])
    res = B.carry_pnl(spot, perp, fund,
                      spot_fee_pct=0.0, perp_fee_pct=0.0, slip_pct=0.0)
    assert res["n_funding_neg"] == 1
    assert res["funding_sum_pct"] == approx(-0.03)


def test_funding_outside_hold_excluded():
    """Settlements at/<= t0 or > tN must not count (no look-ahead / no leakage)."""
    spot = _candles([100, 100, 100])         # t0=0, tN=2h
    perp = _candles([100, 100, 100])
    fund = _funding([(0, 0.01), (10 * _8H, 0.01)])  # at t0 and far after tN
    res = B.carry_pnl(spot, perp, fund,
                      spot_fee_pct=0.0, perp_fee_pct=0.0, slip_pct=0.0)
    assert res["n_funding"] == 0
    assert res["funding_sum_pct"] == 0.0


def test_funding_only_net_ignores_basis():
    """funding_only_net = funding − fees, independent of the basis term."""
    spot = _candles([100, 130])              # big spot move…
    perp = _candles([100, 125])              # …perp moves differently (basis P&L≠0)
    fund = _funding([(_8H // 2, 0.001)])
    res = B.carry_pnl(spot, perp, fund,
                      spot_fee_pct=0.10, perp_fee_pct=0.05, slip_pct=0.02)
    assert res["basis_pnl_pct"] != approx(0.0)
    assert res["funding_only_net_pct"] == approx(
        res["funding_sum_pct"] - res["fees_pct"])


# ── _carry_verdict: honest accept/reject ─────────────────────────────────────
def _held(symbol, net, funding_only_net):
    return {"symbol": symbol, "held": True, "net_carry_pct": net,
            "funding_only_net_pct": funding_only_net}


def test_verdict_accepts_robust_carry():
    res = [_held("BTCUSDT", 0.30, 0.20), _held("ETHUSDT", 0.25, 0.15)]
    v, _ = R._carry_verdict(res)
    assert v == "ACCEPT"


def test_verdict_rejects_too_few_held():
    res = [_held("BTCUSDT", 0.5, 0.4),
           {"symbol": "ETHUSDT", "held": False, "error": "x"}]
    v, _ = R._carry_verdict(res)
    assert v == "REJECT"


def test_verdict_rejects_negative_mean():
    res = [_held("BTCUSDT", 0.20, 0.10), _held("ETHUSDT", -0.30, -0.40),
           _held("SOLUSDT", -0.50, -0.60)]
    v, _ = R._carry_verdict(res)
    assert v == "REJECT"


def test_verdict_rejects_thin_breadth():
    """Mean could be positive but only ONE symbol actually clears costs."""
    res = [_held("BTCUSDT", 0.90, 0.80), _held("ETHUSDT", -0.10, -0.20),
           _held("SOLUSDT", -0.05, -0.15)]
    v, _ = R._carry_verdict(res)
    assert v == "REJECT"


def test_verdict_requires_funding_to_beat_fees():
    """Net positive purely from basis luck (funding alone loses) → REJECT."""
    res = [_held("BTCUSDT", 0.30, -0.10), _held("ETHUSDT", 0.25, -0.05)]
    v, _ = R._carry_verdict(res)
    assert v == "REJECT"


# ── allowlist exclusion: carry never goes live ───────────────────────────────
def test_carry_cell_excluded_from_allowlist():
    res = [_held("BTCUSDT", 0.30, 0.20), _held("ETHUSDT", 0.25, 0.15)]
    cell = R.build_carry_cell(res, interval="1h",
                              spot_fee=0.1, perp_fee=0.05, slip=0.02)
    assert cell["kind"] == "carry"
    assert cell["verdict"] == "ACCEPT"          # even when it ACCEPTs…
    # …a carry ACCEPT must NOT be promoted to the live directional allowlist.
    out = R.run_research(specs=[], persist=False, extra_cells=[cell])
    promoted = {c["strategy_key"] for c in out.get("cells", [])
               if c["verdict"] == "ACCEPT" and c.get("kind") != "carry"}
    assert "carry_okx_delta_neutral" not in promoted
