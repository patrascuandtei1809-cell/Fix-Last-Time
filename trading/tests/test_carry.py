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


# ── maker-fill FRAGILITY sweep (Task #29) ────────────────────────────────────
# Re-price the carry across a grid of fill-cost assumptions and verify the
# break-even four-leg fee + ACCEPT/REJECT-by-cost surface. All offline: flat
# price (basis ≡ 0) so each symbol's gross carry equals its funding harvest and
# the break-even four-leg fee is exactly that gross.
_GRID_COMBOS = (len(R.CARRY_FEE_SWEEP_SPOT) * len(R.CARRY_FEE_SWEEP_PERP)
                * len(R.CARRY_FEE_SWEEP_SLIP))


def _flat_8h(n):
    """n+1 flat 8h bars (price 100) → basis ≡ 0, gross carry = funding sum."""
    return _candles([100.0] * (n + 1), step_ms=_8H)


def _const_fund_8h(n, rate):
    """n funding settlements of `rate` each, inside an (n+1)-bar 8h hold."""
    return _funding([((i + 1) * _8H, rate) for i in range(n)])


def _frames(target_grosses):
    """{symbol: (spot, perp, funding)} with flat price so each symbol's gross
    carry (in %) equals its target — funding_sum = n*rate over n=10 settlements."""
    out = {}
    for sym, gross_pct in target_grosses.items():
        n = 10
        px = _flat_8h(n)
        out[sym] = (px, px, _const_fund_8h(n, (gross_pct / 100.0) / n))
    return out


def test_four_leg_pct_matches_carry_pnl_fees():
    assert R._four_leg_pct(0.10, 0.05, 0.02) == approx(0.38)   # taker
    assert R._four_leg_pct(0.02, 0.02, 0.0) == approx(0.08)    # maker


def test_breakeven_equals_gross_carry():
    """Break-even four-leg fee per symbol = its fee-independent gross carry."""
    be = R._carry_breakeven(_frames({"BTCUSDT": 0.50, "ETHUSDT": 0.40,
                                     "SOLUSDT": -0.20}))
    assert be["BTCUSDT"]["gross_pct"] == approx(0.50)
    assert be["ETHUSDT"]["gross_pct"] == approx(0.40)
    assert be["SOLUSDT"]["gross_pct"] == approx(-0.20)


def test_fee_grid_has_all_combos_sorted_with_keys():
    grid = R.carry_fee_sensitivity(_frames({"BTCUSDT": 1.0, "ETHUSDT": 1.0,
                                            "SOLUSDT": -1.0}))
    assert len(grid) == _GRID_COMBOS
    fees = [g["four_leg_pct"] for g in grid]
    assert fees == sorted(fees)                       # cheapest → dearest
    for g in grid:
        assert {"spot_fee", "perp_fee", "slip", "four_leg_pct", "mean_net_pct",
                "n_winners", "n_held", "verdict"} <= set(g)


def test_mean_net_decreases_as_fees_rise():
    grid = R.carry_fee_sensitivity(_frames({"BTCUSDT": 1.0, "ETHUSDT": 1.0,
                                            "SOLUSDT": -1.0}))
    assert grid[-1]["four_leg_pct"] > grid[0]["four_leg_pct"]
    assert grid[-1]["mean_net_pct"] < grid[0]["mean_net_pct"]


def test_robust_accept_when_gross_exceeds_dearest_fee():
    """Both winners' gross (1.0%) > the dearest grid fee → ACCEPT at EVERY combo."""
    grid = R.carry_fee_sensitivity(_frames({"BTCUSDT": 1.0, "ETHUSDT": 1.0,
                                            "SOLUSDT": -0.20}))
    v, reasons, stats = R._fee_sweep_verdict(grid)
    assert v == "ACCEPT"
    assert stats["n_accept"] == stats["n_total"] == _GRID_COMBOS
    assert "robust" in reasons[0]


def test_fragile_reject_when_verdict_flips_inside_grid():
    """Gross 0.20% sits between the cheapest and dearest grid fee → the verdict
    flips ACCEPT→REJECT inside the grid → fragile → REJECT (load-bearing)."""
    grid = R.carry_fee_sensitivity(_frames({"BTCUSDT": 0.20, "ETHUSDT": 0.20,
                                            "SOLUSDT": -0.05}))
    v, reasons, stats = R._fee_sweep_verdict(grid)
    assert v == "REJECT"
    assert 0 < stats["n_accept"] < stats["n_total"]
    assert "LOAD-BEARING" in reasons[0]
    assert grid[0]["verdict"] == "ACCEPT"             # cheapest corner clears
    assert grid[-1]["verdict"] == "REJECT"            # dearest corner does not


def test_dead_reject_when_no_combo_clears():
    """All gross below the cheapest grid fee → never ACCEPT (dead, not fragile)."""
    grid = R.carry_fee_sensitivity(_frames({"BTCUSDT": 0.02, "ETHUSDT": 0.01,
                                            "SOLUSDT": -0.10}))
    v, reasons, stats = R._fee_sweep_verdict(grid)
    assert v == "REJECT"
    assert stats["n_accept"] == 0
    assert "ANY fill assumption" in reasons[0]


def test_breakeven_reason_reports_min_symbols_th_highest_gross():
    """The verdict break-even = the MIN_SYMBOLS-th highest symbol gross (when
    funding-only and gross agree, the binding break-even equals the gross)."""
    be = {"BTCUSDT": {"gross_pct": 0.40, "funding_sum_pct": 0.40},
          "ETHUSDT": {"gross_pct": 0.70, "funding_sum_pct": 0.70},
          "SOLUSDT": {"gross_pct": -0.02, "funding_sum_pct": -0.02}}
    be_fee, reason = R._breakeven_reason(be)
    assert be_fee == approx(0.40)                     # 2nd-highest (MIN_SYMBOLS=2)
    assert "break-even four-leg fee" in reason
    assert "ETHUSDT" in reason and "BTCUSDT" in reason
    # both break-evens are surfaced, and when they agree there's no false warning
    assert "gross" in reason and "funding-only" in reason
    assert "WARNING" not in reason


def test_breakeven_reason_when_breadth_unreachable():
    """Fewer than MIN_SYMBOLS positive symbols → no fee clears the breadth rule."""
    be = {"BTCUSDT": {"gross_pct": 0.40, "funding_sum_pct": 0.40},
          "ETHUSDT": {"gross_pct": -0.10, "funding_sum_pct": -0.10},
          "SOLUSDT": {"gross_pct": -0.20, "funding_sum_pct": -0.20}}
    be_fee, reason = R._breakeven_reason(be)
    assert be_fee is not None and be_fee <= 0
    assert "no four-leg fee clears" in reason


def test_breakeven_reason_warns_when_funding_only_is_binding():
    """A carry with a healthy GROSS break-even that clears the grid, but whose
    FUNDING income alone breaks even much lower, must be flagged: funding-only is
    the binding limit and the gross figure is propped up by a one-off basis move."""
    # Gross break-even (2nd-highest gross) = 0.60% would look like it clears the
    # whole grid, but funding alone only breaks even at 0.10% (2nd-highest funding).
    be = {"BTCUSDT": {"gross_pct": 0.80, "funding_sum_pct": 0.10},
          "ETHUSDT": {"gross_pct": 0.60, "funding_sum_pct": 0.10},
          "SOLUSDT": {"gross_pct": 0.05, "funding_sum_pct": 0.02}}
    be_fee, reason = R._breakeven_reason(be)
    # binding break-even = 2nd-highest of min(gross, funding) = 0.10, NOT 0.60
    assert be_fee == approx(0.10)
    assert "WARNING" in reason
    assert "funding-only is" in reason and "BINDING" in reason
    # both the misleading gross figure and the true funding-only figure are named
    assert "0.600%" in reason and "0.100%" in reason


def test_breakeven_reason_surfaces_funding_only_per_symbol():
    """Every symbol's funding-only break-even is named alongside its gross."""
    be = {"BTCUSDT": {"gross_pct": 0.40, "funding_sum_pct": 0.15},
          "ETHUSDT": {"gross_pct": 0.70, "funding_sum_pct": 0.30}}
    _, reason = R._breakeven_reason(be)
    assert "funding-only +0.150%" in reason
    assert "funding-only +0.300%" in reason
    assert "gross +0.400%" in reason and "gross +0.700%" in reason


def test_fee_sweep_cell_shape_and_excluded_from_allowlist():
    frames = _frames({"BTCUSDT": 1.0, "ETHUSDT": 1.0, "SOLUSDT": -0.20})
    grid = R.carry_fee_sensitivity(frames)
    cell = R.build_carry_fee_sweep_cell(
        grid, R._carry_breakeven(frames),
        strategy_key="carry_okx_fee_sensitivity",
        strategy="Carry Fill-Cost Sweep (OKX ~92d)",
        signal_name="Carry Fragility (OKX)", label="OKX ~92d")
    assert cell["kind"] == "carry"
    assert cell["qualify_mode"] == "carry_fee_sweep"
    assert "grid" in cell["fee_sweep"]
    assert cell["fee_sweep"]["verdict_breakeven_four_leg_pct"] is not None
    assert cell["subcells"]["BTCUSDT"]["breakeven_four_leg_pct"] == approx(1.0)
    # a carry fee-sweep ACCEPT must never reach the live directional allowlist
    out = R.run_research(specs=[], persist=False, extra_cells=[cell])
    promoted = {c["strategy_key"] for c in out.get("cells", [])
                if c["verdict"] == "ACCEPT" and c.get("kind") != "carry"}
    assert "carry_okx_fee_sensitivity" not in promoted


def test_run_carry_maker_sensitivity_emits_two_excluded_cells(monkeypatch):
    fr_ok = _frames({"BTCUSDT": 1.0, "ETHUSDT": 1.0, "SOLUSDT": -0.20})
    fr_bn = _frames({"BTCUSDT": 30.0, "ETHUSDT": 30.0, "SOLUSDT": -10.0})
    monkeypatch.setattr(R, "_carry_okx_frames", lambda sym, **k: fr_ok[sym])
    monkeypatch.setattr(R, "_carry_binance_frames", lambda sym, **k: fr_bn[sym])
    out = R.run_carry_maker_sensitivity(use_cache=False, persist=False)
    keys = {c["strategy_key"] for c in out["cells"]}
    assert keys == {"carry_okx_fee_sensitivity", "carry_binance_fee_sensitivity"}
    assert all(c["kind"] == "carry" for c in out["cells"])
    assert all(c["verdict"] == "ACCEPT" for c in out["cells"])   # gross >> fees
    assert all(c["fee_sweep"]["n_total"] == _GRID_COMBOS for c in out["cells"])


# ── funding-only-net is the BINDING constraint (Task #35) ─────────────────────
# The verdict requires BOTH net carry AND funding-only-net positive, so an ACCEPT
# can't be propped up by a one-off basis move. The Task #29 fragility sweep only
# uses FLAT frames (basis ≡ 0), where funding_only_net == net_carry and the two
# constraints are indistinguishable — there is no regression test for the case
# where funding-only-net is the TIGHTER constraint. These frames add a positive
# basis move so net carry stays positive while the funding harvest ALONE fails to
# clear fees, proving the verdict / break-even logic binds on funding_only_net.
def _basis_frames(specs):
    """{symbol: (spot, perp, funding)} where each symbol harvests `funding_pct`
    of funding AND realizes `basis_pct` of (fee-independent) basis P&L.

    The perp leg is flat; the spot leg rises by `basis_pct` over the hold, so
    basis_pnl == basis_pct and gross == funding_pct + basis_pct. With basis_pct>0,
    funding_only_net (= funding − fees) sits strictly BELOW net carry, so it
    crosses zero at a LOWER fee — making funding-only the tighter constraint.
    """
    out = {}
    for sym, (funding_pct, basis_pct) in specs.items():
        n = 10
        perp = _flat_8h(n)                                   # short leg flat
        spot = _candles([100.0] + [100.0 * (1 + basis_pct / 100.0)] * n,
                        step_ms=_8H)                         # long leg rises
        out[sym] = (spot, perp, _const_fund_8h(n, (funding_pct / 100.0) / n))
    return out


# The dearest grid corner — used to price the "net positive, funding negative" case.
_DEAR = (R.CARRY_FEE_SWEEP_SPOT[-1], R.CARRY_FEE_SWEEP_PERP[-1],
         R.CARRY_FEE_SWEEP_SLIP[-1])


def test_basis_frames_make_funding_only_the_binding_constraint():
    """Precondition: at the dearest grid fee net carry is still POSITIVE (propped
    up by the basis move) yet the funding harvest ALONE is negative — exactly the
    case the FLAT-frame sweep can never produce (there basis ≡ 0)."""
    spot, perp, fund = _basis_frames({"BTCUSDT": (0.20, 0.50)})["BTCUSDT"]
    r = B.carry_pnl(spot, perp, fund, spot_fee_pct=_DEAR[0],
                    perp_fee_pct=_DEAR[1], slip_pct=_DEAR[2])
    assert r["basis_pnl_pct"] == approx(0.50)               # fee-independent prop
    assert r["funding_sum_pct"] == approx(0.20)
    assert r["net_carry_pct"] > 0                           # net survives on basis
    assert r["funding_only_net_pct"] < 0                    # funding alone loses
    # funding_only_net is below net by exactly the basis term, so it binds first.
    assert r["funding_only_net_pct"] == approx(
        r["net_carry_pct"] - r["basis_pnl_pct"])


def test_verdict_binds_on_funding_only_not_net_carry():
    """At a dear four-leg fee the frame-priced carry has POSITIVE mean net but
    NEGATIVE funding-only-net on every symbol → `_carry_verdict` must REJECT on
    the funding-only rule, even though a net-carry-only gate would ACCEPT."""
    frames = _basis_frames({"BTCUSDT": (0.20, 0.50), "ETHUSDT": (0.20, 0.50)})
    results = []
    for sym, (s, p, f) in frames.items():
        r = B.carry_pnl(s, p, f, spot_fee_pct=0.10, perp_fee_pct=0.02,
                        slip_pct=0.02)
        r["symbol"] = sym
        results.append(r)
    # A net-carry-only gate would PASS (mean net positive, every symbol net>0)…
    assert sum(r["net_carry_pct"] for r in results) / len(results) > 0
    assert all(r["net_carry_pct"] > 0 for r in results)
    # …but the funding harvest alone is negative on every symbol → REJECT.
    assert all(r["funding_only_net_pct"] < 0 for r in results)
    v, reasons = R._carry_verdict(results)
    assert v == "REJECT"
    assert "beats costs on only 0 symbol" in reasons[0]


def test_fee_sweep_flips_on_funding_only_while_net_stays_positive():
    """The fragility sweep flips ACCEPT→REJECT inside the grid driven SOLELY by
    the funding-only constraint: net carry stays positive at EVERY grid fee (the
    basis prop never erodes), but once the funding harvest stops beating the fee
    the winners collapse and the verdict becomes LOAD-BEARING-fragile."""
    frames = _basis_frames({"BTCUSDT": (0.20, 0.50), "ETHUSDT": (0.20, 0.50),
                            "SOLUSDT": (-0.10, 0.50)})
    grid = R.carry_fee_sensitivity(frames)
    assert grid[0]["verdict"] == "ACCEPT"        # cheap corner: funding beats fee
    assert grid[-1]["verdict"] == "REJECT"       # dear corner: funding-only fails
    # net carry on a harvest winner is positive even at the dearest, rejecting fee.
    sB, pB, fB = frames["BTCUSDT"]
    dear_net = B.carry_pnl(sB, pB, fB, spot_fee_pct=_DEAR[0],
                           perp_fee_pct=_DEAR[1], slip_pct=_DEAR[2])
    assert dear_net["net_carry_pct"] > 0 and dear_net["funding_only_net_pct"] < 0
    v, reasons, stats = R._fee_sweep_verdict(grid)
    assert v == "REJECT"
    assert 0 < stats["n_accept"] < stats["n_total"]
    assert "LOAD-BEARING" in reasons[0]


def test_breakeven_distinguishes_funding_only_from_gross():
    """Break-even data exposes BOTH the gross break-even (net=0 ⟺ fee<gross) AND
    the funding-only break-even (funding=fees). With a positive basis the gross
    break-even (0.70%) clears the WHOLE grid — a gross/net-only read would call it
    robust — but the funding-only break-even (0.20%) sits INSIDE the grid, and
    that is the fee where the verdict actually flips."""
    frames = _basis_frames({"BTCUSDT": (0.20, 0.50), "ETHUSDT": (0.20, 0.50)})
    be = R._carry_breakeven(frames)
    assert be["BTCUSDT"]["gross_pct"] == approx(0.70)
    assert be["BTCUSDT"]["funding_sum_pct"] == approx(0.20)
    cheapest = R._four_leg_pct(R.CARRY_FEE_SWEEP_SPOT[0],
                               R.CARRY_FEE_SWEEP_PERP[0], R.CARRY_FEE_SWEEP_SLIP[0])
    dearest = R._four_leg_pct(*_DEAR)
    # gross break-even clears the entire grid (would look "robust")…
    assert be["BTCUSDT"]["gross_pct"] > dearest
    # …but the funding-only break-even is the binding one, INSIDE the grid.
    assert cheapest < be["BTCUSDT"]["funding_sum_pct"] < dearest
