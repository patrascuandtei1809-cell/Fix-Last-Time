"""
test_strategy_live_engine.py — lock in the GATED StrategyLiveEngine (Task #19)
=============================================================================

The research-validated strategy (EMA_MACD_RSI_VOLUME_V2 @ 4h) runs live via the
NEW `live_engine.StrategyLiveEngine`. Unlike the (deliberately ungated) dip
engine, this one MUST consult `research.is_strategy_validated(strategy, interval,
symbol)` fail-closed before any entry, so that only the research-approved cell —
ETH today — can place a LIVE order, while BTC/SOL are blocked.

These tests drive the engine's `evaluate()` directly with a fake exchange (no
network, no Binance, no real order) and the REAL `research.is_strategy_validated`
against a temp allowlist that mirrors the committed file (V2/4h scoped to ETH).

Asserted invariants:
  * ETHUSDT (on the allowlist)        → gate passes, a LIVE BUY is placed.
  * BTCUSDT / SOLUSDT (not on list)   → blocked HOLD, NO order, reason explains it.
  * require_validation=False override  → gate skipped, BTC may trade.
  * validator raises                  → fail CLOSED (no order).
  * validator missing (None)          → fail CLOSED (no order).
  * an open bot position exits FIRST  → stop-loss fires regardless of the gate.

Run:  cd trading && python -m pytest tests/test_strategy_live_engine.py -v
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import pandas as pd

import research
import strategy
import live_engine
from live_engine import StrategyLiveEngine
from live_settings import LiveSettings


# ── fakes (no network, no real exchange) ─────────────────────────────────────
class _FakeExchange:
    """Records buy orders; returns canned price/klines/balance."""
    name = "TEST"

    def __init__(self, price=2000.0, free=1000.0):
        self.client = object()           # truthy ⇒ "connected"
        self._price = price
        self._free = free
        self.buys = []

    def get_price(self, symbol):
        return self._price

    def get_klines(self, symbol, interval, limit=300):
        # Content is irrelevant — the signal is mocked in the entry tests.
        return pd.DataFrame({"close": [self._price] * 5})

    def get_balance(self, asset):
        return {"free": self._free, "locked": 0.0}

    def place_buy_order(self, symbol, amount):
        self.buys.append((symbol, amount))
        qty = amount / self._price
        return {"ok": True, "price": self._price, "qty": qty, "fee": 0.0}


def _settings(**kw):
    s = LiveSettings()
    s.safe_mode = False
    s.aggressive_on = True
    s.bot_spending_limit_usdt = 0.0          # no limit
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _allow_all_gate(*_a, **_kw):
    return True, "ok"


class StrategyLiveEngineGateTests(unittest.TestCase):
    def setUp(self):
        # Redirect the allowlist to a temp file shaped like the committed one:
        # V2 @ 4h scoped to ETHUSDT only.
        self.tmp = tempfile.mkdtemp()
        self._orig_path = research.VALIDATED_PATH
        research.VALIDATED_PATH = os.path.join(self.tmp, "validated.json")
        with open(research.VALIDATED_PATH, "w") as f:
            json.dump({
                "updated_at": "2026-06-03T00:00:00+00:00",
                "validated": [{
                    "strategy": "EMA_MACD_RSI_VOLUME_V2",
                    "interval": "4h",
                    "exit_policy": {"use_atr": True, "sl_pct": 0.4, "tp_pct": 0.8,
                                    "atr_sl_mult": 1.5, "atr_tp_mult": 3.0},
                    "symbols": ["ETHUSDT"],
                }],
            }, f)

    def tearDown(self):
        research.VALIDATED_PATH = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _engine(self, ex, *, require_validation=True, validate_fn="real"):
        if validate_fn == "real":
            validate_fn = research.is_strategy_validated
        return StrategyLiveEngine(
            exchange=ex,
            strategy_name="EMA_MACD_RSI_VOLUME_V2",
            interval="4h",
            validate_fn=validate_fn,
            require_validation=require_validation,
        )

    def _evaluate(self, eng, symbol):
        return eng.evaluate(
            symbol=symbol,
            settings=_settings(),
            open_trades=[],
            current_exposure=0.0,
            global_gate_fn=_allow_all_gate,
        )

    # ── allowed symbol ───────────────────────────────────────────────────────
    def test_validated_symbol_trades(self):
        ex = _FakeExchange()
        eng = self._engine(ex)
        with mock.patch.object(strategy, "get_signal",
                               return_value=("BUY", "v2 long", 70)):
            rec = self._evaluate(eng, "ETHUSDT")
        self.assertEqual(rec.decision, "BUY")
        self.assertTrue(rec.traded)
        self.assertEqual(len(ex.buys), 1)
        self.assertEqual(ex.buys[0][0], "ETHUSDT")

    # ── blocked symbols ──────────────────────────────────────────────────────
    def test_unvalidated_symbol_blocked(self):
        for sym in ("BTCUSDT", "SOLUSDT"):
            ex = _FakeExchange()
            eng = self._engine(ex)
            with mock.patch.object(strategy, "get_signal",
                                   return_value=("BUY", "v2 long", 70)):
                rec = self._evaluate(eng, sym)
            self.assertEqual(rec.decision, "HOLD", f"{sym} must be blocked")
            self.assertFalse(rec.traded)
            self.assertEqual(ex.buys, [], f"{sym} must NOT place an order")
            self.assertIn("not research-validated", rec.reason)

    # ── override + fail-closed ───────────────────────────────────────────────
    def test_require_validation_off_skips_gate(self):
        ex = _FakeExchange()
        eng = self._engine(ex, require_validation=False)
        with mock.patch.object(strategy, "get_signal",
                               return_value=("BUY", "v2 long", 70)):
            rec = self._evaluate(eng, "BTCUSDT")
        self.assertEqual(rec.decision, "BUY")
        self.assertEqual(len(ex.buys), 1)

    def test_gate_fails_closed_on_exception(self):
        ex = _FakeExchange()
        def _boom(*_a, **_kw):
            raise RuntimeError("boom")
        eng = self._engine(ex, validate_fn=_boom)
        with mock.patch.object(strategy, "get_signal",
                               return_value=("BUY", "v2 long", 70)):
            rec = self._evaluate(eng, "ETHUSDT")
        self.assertEqual(rec.decision, "HOLD")
        self.assertEqual(ex.buys, [])

    def test_gate_fails_closed_when_validator_missing(self):
        ex = _FakeExchange()
        eng = self._engine(ex, validate_fn=None)
        with mock.patch.object(strategy, "get_signal",
                               return_value=("BUY", "v2 long", 70)):
            rec = self._evaluate(eng, "ETHUSDT")
        self.assertEqual(rec.decision, "HOLD")
        self.assertEqual(ex.buys, [])

    # ── HOLD signal places no order even when validated ──────────────────────
    def test_validated_but_hold_signal_no_order(self):
        ex = _FakeExchange()
        eng = self._engine(ex)
        with mock.patch.object(strategy, "get_signal",
                               return_value=("HOLD", "no setup", 0)):
            rec = self._evaluate(eng, "ETHUSDT")
        self.assertEqual(rec.decision, "HOLD")
        self.assertEqual(ex.buys, [])

    # ── exit-first: a stop-loss must fire regardless of the gate ─────────────
    def test_open_position_stop_loss_exits_before_gate(self):
        ex = _FakeExchange(price=1900.0)     # below the stored stop
        closed = []
        eng = StrategyLiveEngine(
            exchange=ex,
            strategy_name="EMA_MACD_RSI_VOLUME_V2",
            interval="4h",
            validate_fn=research.is_strategy_validated,
            require_validation=True,
            close_fn=lambda t, p, why: closed.append((t, p, why)),
        )
        open_trade = {
            "id": "x1", "coin": "ETHUSDT", "type": "bot", "manual": False,
            "status": "open", "entry_price": 2000.0,
            "stop_loss": 1950.0, "take_profit": 2100.0, "invested": 100.0,
        }
        rec = eng.evaluate(
            symbol="ETHUSDT", settings=_settings(),
            open_trades=[open_trade], current_exposure=100.0,
            global_gate_fn=_allow_all_gate,
        )
        self.assertEqual(rec.decision, "STOP_LOSS")
        self.assertEqual(len(closed), 1)
        self.assertEqual(ex.buys, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
