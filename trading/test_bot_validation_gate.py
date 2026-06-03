"""
test_bot_validation_gate.py — lock in the ORCHESTRATOR's auto-disable WIRING
============================================================================

`test_research.py` already pins the approval *rules* (`research._verdict`) and
the gate function (`research.is_strategy_validated`) in isolation. What those
tests do NOT prove is that the live orchestrator actually CALLS the gate before
placing an auto order — the wiring in `bot.TradingBot._inner_loop` (the
"AUTO-DISABLE GATE" block: import `is_strategy_validated`, check
`require_validation`, null the winning worker on a miss so `execute_entry()` is
never reached).

These tests drive ONE real orchestrator cycle (`_inner_loop`) with a stub
worker + stub exchange (no network, no Binance, no real order) and assert:

  * empty / non-matching allowlist  → winner is gated, `execute_entry` is NEVER
    called, and the block reason reflects the gate (AUTO-DISABLED log + [GATE]).
  * a matching allowlisted (strategy, interval) → the winner passes the gate and
    `execute_entry` IS called exactly once.
  * `require_validation=False` (ALPHATRADE_ALLOW_UNVALIDATED=1 override) → the
    gate is skipped and the trade is allowed through even with an empty allowlist.
  * the gate FAILS CLOSED — if `is_strategy_validated` raises, the winner is
    still blocked (the `except → _allowed=False` path).

IMPORTANT CAVEAT (verified, see commit message + follow-up): the LIVE default is
`TradingBot.dip_mode = True`, which runs the DipLiveEngine and — by its own
design — bypasses every legacy gate including this allowlist. These tests
exercise the legacy path (`dip_mode=False`) so the gate wiring stays regression-
locked, but they are NOT proof that the *current* live (dip) path refuses an
unapproved strategy.

Run:  cd trading && python -m unittest test_bot_validation_gate -v
(also discoverable by pytest if it is installed).
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import research
import bot
from risk import GlobalRiskManager, GlobalRiskSettings


# ── stubs (no network, no real exchange) ─────────────────────────────────────
class _DummyExchange:
    """Minimal exchange: only `.name` (for the worker key) and a truthy
    `.client` (so nothing thinks we're disconnected)."""
    name = "TEST"
    client = object()


class _StubWorker:
    """Publishes one strong BUY candidate on tick and records execute_entry
    calls. Stands in for a real SymbolWorker through the orchestrator cycle."""

    def __init__(self, symbol="BTCUSDT", strategy="Donchian Breakout",
                 interval="1h"):
        self.symbol     = symbol
        self.strategy   = strategy
        self.interval   = interval
        self.exchange   = _DummyExchange()
        self._session_trades   = 0
        self._last_block_reason = ""
        self._last_eval         = {}
        self.manage_manual_trades = False
        self._on_candidate      = None      # wired by TradingBot.__init__
        self.executed: list     = []

    def tick(self, all_open_trades=None, global_gate_fn=None):
        # A clear winner: score well above threshold, directional signal.
        cand = {
            "symbol":     self.symbol,
            "signal":     "BUY",
            "score":      80,
            "confidence": 70,
            "price":      100.0,
            "exchange":   self.exchange.name,
            "regime":     "TREND",
            "breakdown":  {},
        }
        if self._on_candidate:
            self._on_candidate(cand)

    def execute_entry(self, winner, all_open, global_gate):
        self.executed.append(winner)
        self._session_trades += 1


def _matching_cell(strategy, interval):
    """A research-ACCEPTED cell shaped for research.save_validated()."""
    return {
        "signal_name": strategy,          # matches worker.strategy
        "strategy":    f"{strategy} (display)",
        "interval":    interval,
        "exit_policy": {"use_atr": True, "sl_pct": 0.4, "tp_pct": 0.8},
        "aggregate":   {"expectancy_pct": 0.30, "profit_factor": 1.6,
                        "trades": 40},
    }


class OrchestratorGateWiringTests(unittest.TestCase):
    """Drive one real `_inner_loop` cycle and assert the gate wiring."""

    def setUp(self):
        # Redirect the allowlist to a temp dir (same pattern as test_research).
        self.tmp = tempfile.mkdtemp()
        self._orig_dir  = research.RESEARCH_DIR
        self._orig_path = research.VALIDATED_PATH
        research.RESEARCH_DIR  = self.tmp
        research.VALIDATED_PATH = os.path.join(self.tmp, "validated_strategies.json")

        self.worker = _StubWorker()
        gr = GlobalRiskManager(GlobalRiskSettings())
        self.bot = bot.TradingBot(
            workers={"TEST:BTCUSDT": self.worker},
            global_risk=gr,
            check_every=1,
            initial_balance=1000.0,
        )
        # Exercise the LEGACY orchestrator path that contains the gate. The live
        # default (dip_mode=True) bypasses it entirely — see module docstring.
        self.bot.dip_mode = False
        self.bot.require_validation = True
        self.logs: list = []

    def tearDown(self):
        research.RESEARCH_DIR  = self._orig_dir
        research.VALIDATED_PATH = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_one_cycle(self):
        """Run exactly one orchestrator cycle, fully offline."""
        self.bot._running = True

        def _stop_after_first_sleep(*_a, **_k):
            # The cycle ends with an interruptible sleep loop; the first sleep
            # call flips _running off so the `while self._running` loop exits.
            self.bot._running = False

        def _capture_log(level, message):
            self.logs.append((level, message))

        with mock.patch("bot.time.sleep", side_effect=_stop_after_first_sleep), \
             mock.patch("bot.get_open_trades", return_value=[]), \
             mock.patch("bot.load_trades", return_value=[]), \
             mock.patch("bot.log_activity", side_effect=_capture_log), \
             mock.patch("bot.diagnostics.reconcile_ghost_trades",
                        return_value={"closed": []}), \
             mock.patch("bot.diagnostics.record_cycle", return_value=None), \
             mock.patch("gpt_advisor.get_advisor", return_value=None):
            self.bot._inner_loop()

    # ── the blocked case ─────────────────────────────────────────────────────
    def test_empty_allowlist_blocks_auto_entry(self):
        """Empty allowlist → winner gated, execute_entry NEVER called."""
        research.save_validated([], fee=0.1, slip=0.02)
        self._run_one_cycle()

        self.assertEqual(self.worker.executed, [],
                         "execute_entry must NOT run for an unvalidated strategy")
        self.assertTrue(
            any("AUTO-DISABLED" in m for _lvl, m in self.logs),
            f"expected an AUTO-DISABLED block reason, got logs={self.logs}")

    def test_missing_allowlist_blocks_auto_entry(self):
        """No allowlist file at all → default-safe block, no order attempted."""
        self.assertFalse(os.path.exists(research.VALIDATED_PATH))
        self._run_one_cycle()
        self.assertEqual(self.worker.executed, [],
                         "missing allowlist must block auto-entry")

    def test_non_matching_allowlist_blocks_auto_entry(self):
        """Allowlist has a DIFFERENT (strategy, interval) → still blocked."""
        research.save_validated([_matching_cell("Some Other Strategy", "4h")],
                                fee=0.1, slip=0.02)
        self._run_one_cycle()
        self.assertEqual(self.worker.executed, [],
                         "a non-matching allowlist entry must not allow this pair")

    # ── the allowed case ─────────────────────────────────────────────────────
    def test_matching_allowlist_permits_auto_entry(self):
        """Allowlisted (strategy, interval) → winner passes the gate, trades."""
        research.save_validated(
            [_matching_cell(self.worker.strategy, self.worker.interval)],
            fee=0.1, slip=0.02)
        self._run_one_cycle()

        self.assertEqual(len(self.worker.executed), 1,
                         "execute_entry must run exactly once for a validated pair")
        self.assertEqual(self.worker.executed[0]["symbol"], "BTCUSDT")

    # ── the override + fail-closed cases ─────────────────────────────────────
    def test_require_validation_off_skips_gate(self):
        """ALPHATRADE_ALLOW_UNVALIDATED override → gate skipped, trade allowed."""
        research.save_validated([], fee=0.1, slip=0.02)   # empty allowlist
        self.bot.require_validation = False                # operator override
        self._run_one_cycle()
        self.assertEqual(len(self.worker.executed), 1,
                         "with validation off the empty allowlist must NOT block")

    def test_gate_fails_closed_on_exception(self):
        """If is_strategy_validated raises, the winner is still blocked."""
        research.save_validated(
            [_matching_cell(self.worker.strategy, self.worker.interval)],
            fee=0.1, slip=0.02)
        with mock.patch.object(bot, "_is_strategy_validated",
                               side_effect=RuntimeError("boom")):
            self._run_one_cycle()
        self.assertEqual(self.worker.executed, [],
                         "a raising gate must fail CLOSED (no order)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
