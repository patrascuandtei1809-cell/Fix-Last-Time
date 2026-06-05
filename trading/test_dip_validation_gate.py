"""
test_dip_validation_gate.py — lock in the LIVE (dip) auto-disable WIRING
========================================================================

`test_bot_validation_gate.py` proves the AUTO-DISABLE allowlist gate is called
on the LEGACY orchestrator path (`TradingBot.dip_mode = False`). But the LIVE
default is `dip_mode = True`, which runs `DipLiveEngine` — and that path used to
bypass the allowlist entirely, so the "no proven after-fee edge → no
auto-trading" guarantee was NOT enforced on the path that actually trades.

These tests drive ONE real `DipLiveEngine.evaluate()` cycle with a stub
exchange (no network, no Binance, no real order) on a clear BUY (dip) signal and
assert:

  * empty / missing / non-matching allowlist → entry is gated, NO BUY order is
    placed, and the decision reflects the AUTO-DISABLE gate.
  * a matching allowlisted ('20-Minute Dip', '1m') → the entry passes the gate
    and a LIVE BUY order IS placed exactly once.
  * `require_validation=False` (ALPHATRADE_ALLOW_UNVALIDATED=1 override) → the
    gate is skipped and the trade is allowed even with an empty allowlist.
  * the gate FAILS CLOSED — if the validate fn raises, the entry is still
    blocked (no order).
  * the gate is ENTRY-ONLY — an already-open bot position still gets its
    STOP-LOSS exit even with an empty allowlist (exits must never be stranded).

Run:  cd trading && python -m unittest test_dip_validation_gate -v
(also discoverable by pytest).
"""

import os
import shutil
import tempfile
import unittest

import research
import live_engine
from live_engine import DipLiveEngine, DIP_STRATEGY_NAME, DIP_INTERVAL
from live_settings import LiveSettings


# ── stub cooldown (no DB, no leaking production cooldown state) ───────────────
class _StubCooldown:
    """In-memory cooldown that never blocks — keeps the test fully offline and
    independent of any real `trading_dip_cooldown` table."""

    def get(self, symbol):
        return {}

    def record_buy(self, symbol):
        pass

    def record_sell(self, symbol, profitable=False):
        pass

    def record_stop_loss(self, symbol):
        pass


# ── stubs (no network, no real exchange) ─────────────────────────────────────
class _StubExchange:
    """Minimal exchange that always reports a dip and records BUY orders."""
    name = "TEST"
    client = object()                      # truthy ⇒ "connected"

    def __init__(self):
        self.buys: list = []

    def get_price(self, symbol):
        return 99.0

    def get_klines(self, symbol, interval, limit=0):
        # 20m change well below the −0.10% buy threshold: ref=100 → now=99 (−1%).
        # Need ≥ lookback+1 closes; default lookback = 20.
        return [100.0] * 25 + [99.0]

    def get_balance(self, asset):
        return {"free": 1000.0, "total": 1000.0}

    def place_buy_order(self, symbol, amount):
        self.buys.append((symbol, amount))
        return {"ok": True, "price": 99.0, "qty": amount / 99.0, "fee": 0.0}


def _allow_gate(_invest, _sym):
    """Global risk gate stub — always allows (we test the validation gate)."""
    return True, ""


def _matching_cell(strategy, interval):
    """A research-ACCEPTED cell shaped for research.save_validated()."""
    return {
        "signal_name": strategy,           # matches DIP_STRATEGY_NAME
        "strategy":    f"{strategy} (display)",
        "interval":    interval,
        "exit_policy": {"use_atr": True, "sl_pct": 0.4, "tp_pct": 0.8},
        "aggregate":   {"expectancy_pct": 0.30, "profit_factor": 1.6,
                        "trades": 40},
    }


class DipEngineGateTests(unittest.TestCase):
    """Drive one real DipLiveEngine.evaluate() cycle and assert the gate."""

    SYMBOL = "BTCUSDT"

    def setUp(self):
        # Redirect the allowlist to a temp dir (same pattern as test_research).
        self.tmp = tempfile.mkdtemp()
        self._orig_dir  = research.RESEARCH_DIR
        self._orig_path = research.VALIDATED_PATH
        research.RESEARCH_DIR  = self.tmp
        research.VALIDATED_PATH = os.path.join(self.tmp, "validated_strategies.json")

        self.exchange = _StubExchange()
        self.opened: list = []      # trades the engine asked to record
        self.closed: list = []      # (trade, price, reason) the engine exited

    def tearDown(self):
        research.RESEARCH_DIR  = self._orig_dir
        research.VALIDATED_PATH = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _engine(self, require_validation=True, validate_fn=None):
        return DipLiveEngine(
            exchange      = self.exchange,
            on_open_trade = self.opened.append,
            close_fn      = lambda t, p, r: self.closed.append((t, p, r)),
            cooldown      = _StubCooldown(),
            require_validation = require_validation,
            # Bind explicitly to the (path-redirected) gate so the test is
            # robust regardless of how the module-level default was captured.
            validate_fn   = validate_fn or research.is_strategy_validated,
        )

    def _evaluate(self, engine, open_trades=None):
        return engine.evaluate(
            symbol           = self.SYMBOL,
            settings         = LiveSettings(),
            open_trades      = open_trades or [],
            current_exposure = 0.0,
            global_gate_fn   = _allow_gate,
        )

    # ── blocked cases ────────────────────────────────────────────────────────
    def test_empty_allowlist_blocks_entry(self):
        """Empty allowlist → NO BUY order, decision is the AUTO-DISABLE skip."""
        research.save_validated([], fee=0.1, slip=0.02)
        rec = self._evaluate(self._engine())
        self.assertEqual(self.exchange.buys, [],
                         "no BUY order may be placed for an unvalidated dip")
        self.assertFalse(rec.traded)
        self.assertIn("AUTO-DISABLED", rec.reason)

    def test_missing_allowlist_blocks_entry(self):
        """No allowlist file at all → default-safe block, no order."""
        self.assertFalse(os.path.exists(research.VALIDATED_PATH))
        rec = self._evaluate(self._engine())
        self.assertEqual(self.exchange.buys, [])
        self.assertFalse(rec.traded)

    def test_non_matching_allowlist_blocks_entry(self):
        """Allowlist has a DIFFERENT (strategy, interval) → still blocked."""
        research.save_validated([_matching_cell("Some Other Strategy", "4h")],
                                fee=0.1, slip=0.02)
        rec = self._evaluate(self._engine())
        self.assertEqual(self.exchange.buys, [])
        self.assertFalse(rec.traded)

    def test_interval_mismatch_blocks_entry(self):
        """Right strategy name but wrong interval → blocked."""
        research.save_validated([_matching_cell(DIP_STRATEGY_NAME, "1h")],
                                fee=0.1, slip=0.02)
        rec = self._evaluate(self._engine())
        self.assertEqual(self.exchange.buys, [])
        self.assertFalse(rec.traded)

    # ── allowed case ─────────────────────────────────────────────────────────
    def test_matching_allowlist_permits_entry(self):
        """Allowlisted ('20-Minute Dip', '1m') → entry passes, BUY placed once."""
        research.save_validated([_matching_cell(DIP_STRATEGY_NAME, DIP_INTERVAL)],
                                fee=0.1, slip=0.02)
        rec = self._evaluate(self._engine())
        self.assertEqual(len(self.exchange.buys), 1,
                         "a validated dip must place exactly one BUY order")
        self.assertEqual(self.exchange.buys[0][0], self.SYMBOL)
        self.assertTrue(rec.traded)
        self.assertEqual(rec.decision, "BUY")
        self.assertEqual(len(self.opened), 1)

    # ── override + fail-closed cases ─────────────────────────────────────────
    def test_require_validation_off_skips_gate(self):
        """ALPHATRADE_ALLOW_UNVALIDATED override → gate skipped, trade allowed."""
        research.save_validated([], fee=0.1, slip=0.02)      # empty allowlist
        rec = self._evaluate(self._engine(require_validation=False))
        self.assertEqual(len(self.exchange.buys), 1,
                         "with validation off the empty allowlist must NOT block")
        self.assertTrue(rec.traded)

    def test_gate_fails_closed_on_exception(self):
        """If the validate fn raises, the entry is still blocked (no order)."""
        research.save_validated([_matching_cell(DIP_STRATEGY_NAME, DIP_INTERVAL)],
                                fee=0.1, slip=0.02)

        def _boom(_s, _i):
            raise RuntimeError("boom")

        rec = self._evaluate(self._engine(validate_fn=_boom))
        self.assertEqual(self.exchange.buys, [],
                         "a raising gate must fail CLOSED (no order)")
        self.assertFalse(rec.traded)

    def test_gate_fails_closed_when_validate_fn_missing(self):
        """No importable gate (validate_fn None) → fail closed, no order."""
        eng = self._engine()
        eng._validate_fn = None             # simulate research unimportable
        rec = self._evaluate(eng)
        self.assertEqual(self.exchange.buys, [])
        self.assertFalse(rec.traded)

    # ── the gate is ENTRY-ONLY — exits must never be stranded ─────────────────
    def test_gate_does_not_block_stop_loss_exit(self):
        """An open bot position still hits STOP-LOSS even with empty allowlist."""
        research.save_validated([], fee=0.1, slip=0.02)      # empty ⇒ entries off
        open_trade = {
            "id": "t1", "coin": self.SYMBOL, "type": "bot", "manual": False,
            "status": "open", "side": "BUY",
            "entry_price": 200.0,            # price 99 ⇒ ~-50% ⇒ STOP-LOSS
        }
        rec = self._evaluate(self._engine(), open_trades=[open_trade])
        self.assertEqual(self.exchange.buys, [], "no new BUY while blocked")
        self.assertEqual(len(self.closed), 1,
                         "open position must still be stop-lossed (exit-first)")
        self.assertEqual(rec.decision, "STOP_LOSS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
