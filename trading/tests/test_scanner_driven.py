"""Regression tests for the SCANNER-DRIVEN redesign (Task #39).

Locks in the operator-critical invariants of the cross-venue redesign:
  1. resolve_scanner_opportunities routes each pick to the venue it was
     discovered on, dedupes by symbol (highest-scored venue wins), and scopes
     correctly for binance / mexc / multi modes — never mixing wallets.
  2. A DRY-RUN MexcExchange with NO credentials returns a deterministic
     SIMULATED USDT wallet so scanner-routed MEXC picks can be paper-traded
     instead of being silently skipped at the balance gate.
  3. The global concurrency cap is 3 by default (risk.py + the dataclass).

All tests are OFFLINE — scanner output is injected via monkeypatch, no network
and no API keys required.
"""
import pytest

import bot
from exchanges.mexc import MexcExchange, SIM_DRY_RUN_USDT
from risk import GlobalRiskSettings


# Ranked (score desc) scanner output spanning BOTH venues, with one symbol
# (ETHUSDT) appearing on both — the higher-scored venue must win.
_FAKE_OPPS = [
    {"symbol": "ZECUSDT",  "exchange": "binance", "score": 95},
    {"symbol": "ETHUSDT",  "exchange": "mexc",    "score": 90},
    {"symbol": "WLDUSDT",  "exchange": "mexc",    "score": 85},
    {"symbol": "ETHUSDT",  "exchange": "binance", "score": 70},
    {"symbol": "NEARUSDT", "exchange": "binance", "score": 60},
]


@pytest.fixture
def fake_scanner(monkeypatch):
    import scanner
    monkeypatch.setattr(scanner, "load_opportunities", lambda exchange=None: list(_FAKE_OPPS))
    return scanner


def test_multi_mode_merges_both_venues_and_routes(fake_scanner):
    out = bot.resolve_scanner_opportunities("multi", top_n=10)
    venues = {o["symbol"]: o["exchange"] for o in out}
    # Both venues present → not collapsed to one exchange.
    assert set(venues.values()) == {"binance", "mexc"}
    # Each coin routed to the venue it was discovered on.
    assert venues["ZECUSDT"] == "binance"
    assert venues["WLDUSDT"] == "mexc"


def test_multi_mode_dedupes_by_symbol_highest_score_wins(fake_scanner):
    out = bot.resolve_scanner_opportunities("multi", top_n=10)
    syms = [o["symbol"] for o in out]
    # ETHUSDT appears once only — the higher-scored (mexc, 90) venue wins over
    # the lower binance (70) one.
    assert syms.count("ETHUSDT") == 1
    eth = next(o for o in out if o["symbol"] == "ETHUSDT")
    assert eth["exchange"] == "mexc"


def test_binance_mode_excludes_mexc(fake_scanner):
    out = bot.resolve_scanner_opportunities("binance", top_n=10)
    assert out, "expected at least one binance opportunity"
    assert all(o["exchange"] == "binance" for o in out)


def test_mexc_mode_excludes_binance(fake_scanner):
    out = bot.resolve_scanner_opportunities("mexc", top_n=10)
    assert out, "expected at least one mexc opportunity"
    assert all(o["exchange"] == "mexc" for o in out)


def test_top_n_caps_result(fake_scanner):
    out = bot.resolve_scanner_opportunities("multi", top_n=2)
    assert len(out) == 2


def test_no_scan_returns_empty(monkeypatch):
    import scanner
    monkeypatch.setattr(scanner, "load_opportunities", lambda exchange=None: [])
    assert bot.resolve_scanner_opportunities("multi", top_n=10) == []


def test_mexc_dry_run_simulated_usdt_without_creds():
    m = MexcExchange(client=None, live_orders=False)
    bal = m.get_balance("USDT")
    assert bal["free"] == SIM_DRY_RUN_USDT
    assert bal["total"] == SIM_DRY_RUN_USDT
    assert bal["locked"] == 0.0


def test_mexc_dry_run_non_usdt_is_zero():
    m = MexcExchange(client=None, live_orders=False)
    bal = m.get_balance("BTC")
    assert bal["free"] == 0.0 and bal["total"] == 0.0


def test_mexc_live_orders_without_creds_still_raises():
    # If the operator turns ON live orders but has no creds, balance must NOT be
    # faked — fail loudly instead of paper-trading a "live" config.
    m = MexcExchange(client=None, live_orders=True)
    with pytest.raises(RuntimeError):
        m.get_balance("USDT")


def test_active_symbols_cap_fits_full_split_plan():
    """Task #44 — the worker cap must fit 3 pinned Binance + 15 MEXC = 18, so the
    full split-routing plan is never truncated below the MEXC cap."""
    assert bot.MAX_ACTIVE_SYMBOLS >= 18


def test_per_venue_cap_defaults():
    """Task #44 — per-venue caps: Binance 3, MEXC 15, overall ceiling 18."""
    s = GlobalRiskSettings()
    assert s.max_open_trades_binance == 3
    assert s.max_open_trades_mexc == 15
    assert s.max_open_trades_total == 18
    assert s.cap_for_venue("binance") == 3
    assert s.cap_for_venue("mexc") == 15
    assert s.cap_for_venue("") == 3          # unknown ⇒ binance


def test_per_venue_cap_enforced_independently():
    """Binance fills at 3 while MEXC keeps accepting up to 15 — and vice versa."""
    from risk import GlobalRiskManager

    grm = GlobalRiskManager(GlobalRiskSettings())

    # 3 open Binance majors → a 4th Binance entry is blocked …
    bin_open = [{"invested": 10, "coin": s, "exchange": "binance"}
                for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
    ok, why = grm.check_global(bin_open, 10.0, "AVAXUSDT", new_venue="binance")
    assert ok is False and "BINANCE" in why

    # … but a MEXC entry with those same 3 Binance trades open still passes.
    ok, _ = grm.check_global(bin_open, 10.0, "WLDUSDT", new_venue="mexc")
    assert ok is True

    # 15 open MEXC alts → a 16th MEXC entry is blocked …
    mexc_open = [{"invested": 5, "coin": f"ALT{i}USDT", "exchange": "mexc"}
                 for i in range(15)]
    ok, why = grm.check_global(mexc_open, 5.0, "PEPEUSDT", new_venue="mexc")
    assert ok is False and "MEXC" in why

    # … but a Binance entry alongside 15 MEXC trades still passes.
    ok, _ = grm.check_global(mexc_open, 10.0, "BTCUSDT", new_venue="binance")
    assert ok is True
