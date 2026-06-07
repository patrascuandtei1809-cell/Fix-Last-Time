"""Lock-in tests for the MEXC exchange adapter (Step 2).

Guarantees the operator cares about most:
  1. MEXC defaults to DRY-RUN — no order ever leaves the process without an
     explicit live_orders switch.
  2. Order-level safety: a BUY is capped to max 2 USDT; balance/volatility
     gates block when they should.
  3. The adapter is a complete Exchange (all abstract methods implemented) so
     the bot can route to it exactly like Binance.

All tests are OFFLINE — network calls (get_price / balance / klines) are
monkeypatched, and no API keys are required.
"""
import pytest

from exchanges.base import Exchange
from exchanges.mexc import MexcExchange


@pytest.fixture
def ex(monkeypatch):
    """A keyless MexcExchange with price + filters stubbed (no network)."""
    e = MexcExchange()  # client=None, live_orders defaults False
    monkeypatch.setattr(e, "get_price", lambda s: 100.0)
    monkeypatch.setattr(e, "get_symbol_filters",
                        lambda s: {"step_size": 0.001, "min_notional": 1.0,
                                   "min_qty": 0.0})
    # Default: safety passes unless a test overrides it.
    monkeypatch.setattr(e, "_buy_safety_block", lambda s, q: None)
    return e


def test_is_a_complete_exchange():
    # Instantiation would raise if any abstract method were unimplemented.
    e = MexcExchange()
    assert isinstance(e, Exchange)
    assert e.name == "mexc"


def test_defaults_to_dry_run():
    assert MexcExchange().live_orders is False


def test_buy_is_capped_to_two_usdt(ex):
    # Ask to spend 50 USDT — must be capped to 2 USDT → qty = 2/100 = 0.02.
    res = ex.place_buy_order("FOOUSDT", 50)
    assert res["ok"] is True
    assert res["dry_run"] is True
    assert res["side"] == "BUY"
    assert res["qty"] == pytest.approx(0.02, abs=1e-9)


def test_buy_under_cap_is_unchanged(ex):
    res = ex.place_buy_order("FOOUSDT", 1.5)  # below 2 USDT cap
    assert res["ok"] is True
    assert res["qty"] == pytest.approx(0.015, abs=1e-9)


def test_sell_dry_run_simulates(ex):
    res = ex.place_sell_order("FOOUSDT", 0.5)
    assert res["ok"] is True
    assert res["dry_run"] is True
    assert res["side"] == "SELL"
    assert res["qty"] == pytest.approx(0.5, abs=1e-9)


def test_safety_block_prevents_order(ex, monkeypatch):
    monkeypatch.setattr(ex, "_buy_safety_block",
                        lambda s, q: "MEXC USDT balance < 5")
    res = ex.place_buy_order("FOOUSDT", 2)
    assert res["ok"] is False
    msg = (res.get("error") or res.get("reason") or "").lower()
    assert "balance" in msg


def test_dry_run_never_calls_client(monkeypatch):
    # Even with a (fake) client present, DRY-RUN must not call it.
    sentinel = {"called": False}

    class _FakeClient:
        def place_market_buy_quote(self, *a, **k):
            sentinel["called"] = True
            raise AssertionError("client must NOT be called in dry-run")

    e = MexcExchange(client=_FakeClient(), live_orders=False)
    monkeypatch.setattr(e, "get_price", lambda s: 100.0)
    monkeypatch.setattr(e, "get_symbol_filters",
                        lambda s: {"step_size": 0.001, "min_notional": 1.0,
                                   "min_qty": 0.0})
    monkeypatch.setattr(e, "_buy_safety_block", lambda s, q: None)
    res = e.place_buy_order("FOOUSDT", 2)
    assert res["dry_run"] is True
    assert sentinel["called"] is False
