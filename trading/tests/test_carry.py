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


# ── MULTI-YEAR maker-vs-taker carry economics (Task #28) ─────────────────────
# Fee profiles read straight from research so the tests track the code constants.
_TAKER = R._CARRY_PROFILES["taker"]
_MAKER = R._CARRY_PROFILES["maker"]


def _carry(spot, perp, fund, prof):
    return B.carry_pnl(spot, perp, fund, spot_fee_pct=prof["spot_fee"],
                       perp_fee_pct=prof["perp_fee"], slip_pct=prof["slip"])


def test_maker_one_time_fee_is_cheaper_than_taker():
    """Same hold, zero funding → maker pays a strictly smaller one-time 4-leg fee."""
    flat = _candles([100, 100, 100])
    t = _carry(flat, flat, _funding([]), _TAKER)
    m = _carry(flat, flat, _funding([]), _MAKER)
    assert m["fees_pct"] < t["fees_pct"]
    assert m["net_carry_pct"] > t["net_carry_pct"]   # less cost = better net


def test_spot_proxies_perp_so_basis_is_zero():
    """Multi-year model uses spot as the perp leg → basis term cancels exactly."""
    leg = _candles([100, 130, 95, 140])              # arbitrary price path
    res = _carry(leg, leg, _funding([]), _MAKER)
    assert res["basis_pnl_pct"] == approx(0.0)


def test_multiyear_funding_dwarfs_the_one_time_fee():
    """Long +funding stream → both profiles clear costs; the maker/taker gap is
    just the tiny one-time fee difference, not the multi-year harvest."""
    n = 200                                          # 200 × 8h settlements
    px = _candles([100] * (n + 1), step_ms=_8H)
    fund = _funding([((i + 1) * _8H, 0.0001) for i in range(n)])  # +0.01% each
    t = _carry(px, px, fund, _TAKER)
    m = _carry(px, px, fund, _MAKER)
    assert t["funding_sum_pct"] == approx(2.0)       # 200 × 0.01%
    assert t["net_carry_pct"] > 0 and m["net_carry_pct"] > 0
    assert m["net_carry_pct"] > t["net_carry_pct"]
    # the entire difference is the one-time fee gap, dwarfed by the harvest
    assert (m["net_carry_pct"] - t["net_carry_pct"]) == approx(
        t["fees_pct"] - m["fees_pct"])


def test_short_window_loser_flips_positive_under_maker():
    """A thin funding harvest that loses to the taker fee can flip positive under
    the cheaper maker fee — the SOL-style 'lone loser flips' check."""
    px = _candles([100, 100, 100, 100], step_ms=_8H)
    fund = _funding([(_8H, 0.001), (2 * _8H, 0.001)])  # +0.20% total
    t = _carry(px, px, fund, _TAKER)
    m = _carry(px, px, fund, _MAKER)
    assert t["net_carry_pct"] < 0                    # 0.20% < taker 4-leg cost
    assert m["net_carry_pct"] > 0                    # but clears the maker cost


def test_multiyear_maker_cell_is_conditional_and_taker_is_not():
    res = [_held("BTCUSDT", 0.30, 0.20), _held("ETHUSDT", 0.25, 0.15)]
    maker = R.build_carry_cell(
        res, interval="1d", spot_fee=_MAKER["spot_fee"],
        perp_fee=_MAKER["perp_fee"], slip=_MAKER["slip"],
        strategy_key="carry_binance_multiyear_maker",
        conditional_reason="resting-maker fills")
    taker = R.build_carry_cell(
        res, interval="1d", spot_fee=_TAKER["spot_fee"],
        perp_fee=_TAKER["perp_fee"], slip=_TAKER["slip"],
        strategy_key="carry_binance_multiyear_taker")
    assert maker["verdict"] == "ACCEPT" and taker["verdict"] == "ACCEPT"
    assert maker["verdict_reasons"][0].startswith("CONDITIONAL ACCEPT")
    assert not taker["verdict_reasons"][0].startswith("CONDITIONAL")


def test_multiyear_carry_cells_excluded_from_allowlist():
    """Both distinct multi-year carry cells stay out of the live allowlist."""
    res = [_held("BTCUSDT", 0.40, 0.30), _held("ETHUSDT", 0.35, 0.25),
           _held("SOLUSDT", 0.20, 0.10)]
    cells = [
        R.build_carry_cell(res, interval="1d", spot_fee=_TAKER["spot_fee"],
                           perp_fee=_TAKER["perp_fee"], slip=_TAKER["slip"],
                           strategy_key="carry_binance_multiyear_taker"),
        R.build_carry_cell(res, interval="1d", spot_fee=_MAKER["spot_fee"],
                           perp_fee=_MAKER["perp_fee"], slip=_MAKER["slip"],
                           strategy_key="carry_binance_multiyear_maker",
                           conditional_reason="resting-maker fills"),
    ]
    out = R.run_research(specs=[], persist=False, extra_cells=cells)
    promoted = {c["strategy_key"] for c in out.get("cells", [])
                if c["verdict"] == "ACCEPT" and c.get("kind") != "carry"}
    assert "carry_binance_multiyear_taker" not in promoted
    assert "carry_binance_multiyear_maker" not in promoted


# ── strict single-venue source: no silent OKX fallback (Task #28 honesty) ────
def test_binance_vision_strict_source_raises_without_okx_fallback(monkeypatch):
    """source='binance-vision' must NOT fall back to OKX — a cell labeled Binance
    can never silently harvest OKX funding. If Vision is empty it must raise."""
    calls = {"okx": 0}

    def _no_vision(symbol, days):
        return []                                    # Vision returns nothing

    def _spy_okx(symbol, days, cutoff_ms):
        calls["okx"] += 1
        return [(0, 0.0001)]

    monkeypatch.setattr(B, "_fetch_funding_binance_vision", _no_vision)
    monkeypatch.setattr(B, "_fetch_funding_okx", _spy_okx)
    try:
        B.fetch_funding_rates("BTCUSDT", 1825, use_cache=False,
                              source="binance-vision")
        assert False, "expected RuntimeError on empty strict Vision fetch"
    except RuntimeError:
        pass
    assert calls["okx"] == 0                          # OKX never consulted


# ── ROLLING-WINDOW carry robustness (Task #24) ───────────────────────────────
# A single buy-and-hold is endpoint-sensitive; the rolling study enters a fixed-
# length hold at many stepped starts and judges the carry by the BREADTH of
# positive windows, not one endpoint. All offline (synthetic frames).
_DAY = 86400000


def _daily_flat(n_days, *, price=100.0, start=0):
    """Flat daily [open_time, okx_close] frame — basis ≡ 0 spot-proxy."""
    return pd.DataFrame({
        "open_time": [start + i * _DAY for i in range(n_days)],
        "okx_close": [float(price)] * n_days,
    })


def _const_funding(n_days, rate, *, start=0):
    """Constant `rate` per 8h settlement across `n_days` (3 settlements/day)."""
    return _funding([(start + (i + 1) * _8H, rate) for i in range(n_days * 3)])


def test_rolling_insufficient_candles_not_held():
    res = B.rolling_carry(_daily_flat(1), _daily_flat(1), _funding([]),
                          window_days=90, step_days=30,
                          spot_fee_pct=0.1, perp_fee_pct=0.05, slip_pct=0.02)
    assert res["held"] is False


def test_rolling_too_few_windows_not_held():
    """100d of data fits only ONE 90d/30d hold → below min_windows → not held."""
    spot = _daily_flat(100)
    fund = _const_funding(100, 0.0001)
    res = B.rolling_carry(spot, spot, fund, window_days=90, step_days=30,
                          spot_fee_pct=0.1, perp_fee_pct=0.05, slip_pct=0.02,
                          min_windows=6)
    assert res["held"] is False
    assert res["n_windows"] < 6


def test_rolling_all_windows_positive_with_strong_funding():
    """Large constant +funding → every overlapping window clears the 4-leg fee."""
    spot = _daily_flat(301)
    fund = _const_funding(301, 0.0005)          # +0.05%/8h
    res = B.rolling_carry(spot, spot, fund, window_days=90, step_days=30,
                          spot_fee_pct=0.10, perp_fee_pct=0.05, slip_pct=0.02,
                          min_windows=4)
    assert res["held"] and res["n_windows"] >= 4
    assert res["pct_windows_positive"] == approx(100.0)
    assert res["n_windows_positive"] == res["n_windows"]
    assert res["min_net_carry_pct"] > 0 and res["median_net_carry_pct"] > 0
    # worst/best windows are drawn from the realized net distribution
    assert res["worst_window"]["net_carry_pct"] == approx(res["min_net_carry_pct"])
    assert res["best_window"]["net_carry_pct"] == approx(res["max_net_carry_pct"])


def test_rolling_worst_window_captures_negative_funding_stretch():
    """A deep negative-funding blast makes the windows spanning it the losers, and
    `worst_window` must surface the minimum of the net distribution."""
    spot = _daily_flat(301)
    rows = []
    for i in range(301 * 3):
        t = (i + 1) * _8H
        day = t // _DAY
        rate = -0.01 if 100 <= day <= 110 else 0.00005   # 11-day −1%/8h blast
        rows.append((t, rate))
    res = B.rolling_carry(spot, spot, _funding(rows), window_days=90, step_days=30,
                          spot_fee_pct=0.0, perp_fee_pct=0.0, slip_pct=0.0,
                          min_windows=4)
    assert res["held"]
    assert res["min_net_carry_pct"] < 0                  # the blast drags some <0
    assert res["pct_windows_positive"] < 100.0
    assert res["worst_window"]["net_carry_pct"] == approx(res["min_net_carry_pct"])


# ── _carry_rolling_verdict: breadth of positive windows ──────────────────────
def _dist(symbol, pct_pos, median_net):
    return {"symbol": symbol, "held": True,
            "n_windows": 50, "n_windows_positive": round(pct_pos / 2),
            "pct_windows_positive": pct_pos,
            "mean_net_carry_pct": median_net, "median_net_carry_pct": median_net,
            "std_net_carry_pct": 1.0, "min_net_carry_pct": -1.0,
            "max_net_carry_pct": 5.0, "sum_pos_net_pct": 10.0,
            "sum_neg_net_pct": 2.0, "mean_apr_pct": 5.0, "median_apr_pct": 5.0,
            "worst_window": {"net_carry_pct": -1.0},
            "best_window": {"net_carry_pct": 5.0}}


def test_rolling_verdict_accepts_broad_positive_windows():
    res = [_dist("BTCUSDT", 97, 1.1), _dist("ETHUSDT", 86, 1.0)]
    v, _ = R._carry_rolling_verdict(res)
    assert v == "ACCEPT"


def test_rolling_verdict_rejects_too_few_held():
    res = [_dist("BTCUSDT", 97, 1.1),
           {"symbol": "ETHUSDT", "held": False, "error": "x"}]
    v, _ = R._carry_rolling_verdict(res)
    assert v == "REJECT"


def test_rolling_verdict_rejects_thin_breadth():
    """Only ONE symbol is positive in ≥70% of windows → not endpoint-robust."""
    res = [_dist("BTCUSDT", 97, 1.1), _dist("ETHUSDT", 55, 0.5),
           _dist("SOLUSDT", 59, 0.3)]
    v, _ = R._carry_rolling_verdict(res)
    assert v == "REJECT"


def test_rolling_verdict_rejects_positive_median_but_low_window_breadth():
    """A positive median is NOT enough — the carry must clear in ≥70% of windows
    (the SOL-style 'positive median, fragile breadth' case)."""
    res = [_dist("BTCUSDT", 97, 1.1), _dist("ETHUSDT", 60, 0.2)]
    v, _ = R._carry_rolling_verdict(res)
    assert v == "REJECT"


# ── rolling cell shape + allowlist exclusion ─────────────────────────────────
def test_rolling_cell_excluded_from_allowlist():
    res = [_dist("BTCUSDT", 97, 1.1), _dist("ETHUSDT", 86, 1.0)]
    cell = R.build_carry_rolling_cell(
        res, window_days=180, step_days=30, spot_fee=0.10, perp_fee=0.05,
        slip=0.02, strategy_key="carry_binance_rolling_180d")
    assert cell["kind"] == "carry"
    assert cell["verdict"] == "ACCEPT"
    assert cell["aggregate"]["win_rate"] > 0
    assert cell["subcells"]["BTCUSDT"]["pct_windows_positive"] == 97
    out = R.run_research(specs=[], persist=False, extra_cells=[cell])
    promoted = {c["strategy_key"] for c in out.get("cells", [])
                if c["verdict"] == "ACCEPT" and c.get("kind") != "carry"}
    assert "carry_binance_rolling_180d" not in promoted


def test_run_carry_rolling_uses_strict_vision_and_two_window_cells(monkeypatch):
    """End-to-end (network stubbed): both window lengths pull from the STRICT
    Binance Vision source and emit two distinct rolling carry cells."""
    seen_sources = []
    fund = _const_funding(400, 0.0002)          # +0.02%/8h over ~400d

    def _fake_funding(symbol, days, use_cache=True, source="auto"):
        seen_sources.append(source)
        return fund.copy()

    monkeypatch.setattr(B, "fetch_funding_rates", _fake_funding)
    out = R.run_carry_rolling(symbols=["BTCUSDT", "ETHUSDT"], persist=False)
    assert seen_sources and all(s == "binance-vision" for s in seen_sources)
    cells = {c["strategy_key"]: c for c in out["cells"]}
    assert set(cells) == {"carry_binance_rolling_90d", "carry_binance_rolling_180d"}
    assert all(c["kind"] == "carry" for c in cells.values())
    assert all(c["verdict"] == "ACCEPT" for c in cells.values())


def test_run_carry_multiyear_uses_strict_vision_and_two_conditional_cells(monkeypatch):
    """End-to-end (network stubbed): both profiles pull from the STRICT Binance
    Vision source, emit two distinct carry cells, and only the maker ACCEPT is
    flagged CONDITIONAL."""
    seen_sources = []
    # ~2y of +0.01%/8h funding → harvest easily clears both fee profiles.
    n = 600
    fund = _funding([((i + 1) * _8H, 0.0001) for i in range(n)])
    px_close = pd.DataFrame({
        "open_time": [i * _8H for i in range(n + 1)],
        "close": [100.0] * (n + 1),
    })

    def _fake_funding(symbol, days, use_cache=True, source="auto"):
        seen_sources.append(source)
        return fund.copy()

    def _fake_klines(symbol, interval, days, use_cache=True):
        return px_close.copy()

    monkeypatch.setattr(B, "fetch_funding_rates", _fake_funding)
    monkeypatch.setattr(B, "fetch_klines", _fake_klines)

    out = R.run_carry_multiyear(symbols=["BTCUSDT", "ETHUSDT"], persist=False)
    # every fetch went through the strict single-venue source
    assert seen_sources and all(s == "binance-vision" for s in seen_sources)

    cells = {c["strategy_key"]: c for c in out["cells"]}
    assert set(cells) == {"carry_binance_multiyear_taker",
                          "carry_binance_multiyear_maker"}
    assert all(c["kind"] == "carry" for c in cells.values())
    assert all(c["verdict"] == "ACCEPT" for c in cells.values())
    assert cells["carry_binance_multiyear_maker"][
        "verdict_reasons"][0].startswith("CONDITIONAL ACCEPT")
    assert not cells["carry_binance_multiyear_taker"][
        "verdict_reasons"][0].startswith("CONDITIONAL")
