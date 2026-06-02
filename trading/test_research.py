"""
test_research.py — lock in the strategy approval rules
======================================================

The auto-disable gate is the safety mechanism that stops the LIVE bot from
trading a money-losing strategy. Two pieces matter:

  1. `research._verdict()` — decides ACCEPT/REJECT for a (strategy × timeframe)
     cell. A regression that loosens this could silently let a non-robust
     strategy go live.
  2. `research.is_strategy_validated()` — the live gate. It must be DEFAULT-SAFE:
     a missing / empty / corrupt allowlist means NO auto-trading at all.

These tests drive `_verdict` with synthetic sub-cell / aggregate / walk-forward
metrics (no network, no backtest run) so the exact approval rules are pinned,
and exercise the gate against a temporary allowlist file.

Run:  cd trading && python -m unittest test_research -v
(also discoverable by pytest if it is installed).
"""

import os
import json
import shutil
import tempfile
import unittest

import research


# ── helpers ──────────────────────────────────────────────────────────────────
def _m(trades: int, exp: float, pf: float = 1.5) -> dict:
    """A minimal metrics dict shaped like backtest.metrics() output."""
    return {"trades": trades, "expectancy_pct": exp, "profit_factor": pf,
            "win_rate": 55.0}


def _good_wf() -> list:
    """Walk-forward folds with a majority positive (passes the WF gate)."""
    return [_m(10, 0.2), _m(10, 0.3), _m(10, -0.1), _m(10, 0.4)]


def _robust_subcells() -> dict:
    """6 tradable cells across 3 symbols, all positive after fees."""
    return {
        "BTCUSDT/90d":  _m(8, 0.30), "BTCUSDT/180d": _m(8, 0.28),
        "ETHUSDT/90d":  _m(8, 0.25), "ETHUSDT/180d": _m(8, 0.22),
        "SOLUSDT/90d":  _m(8, 0.20), "SOLUSDT/180d": _m(8, 0.18),
    }


class VerdictTests(unittest.TestCase):
    """Pin the exact ACCEPT/REJECT rules in research._verdict()."""

    def test_robust_edge_accepts(self):
        """Positive on every cell, ≥2 symbols, enough trades, WF holds → ACCEPT."""
        subcells = _robust_subcells()
        agg = _m(48, 0.24, pf=1.6)
        verdict, reasons = research._verdict(subcells, agg, _good_wf())
        self.assertEqual(verdict, "ACCEPT", reasons)

    def test_single_lucky_symbol_rejects(self):
        """One profitable coin while the rest are sparse → REJECT (breadth)."""
        subcells = {
            "BTCUSDT/90d":  _m(12, 0.40), "BTCUSDT/180d": _m(12, 0.35),
            "ETHUSDT/90d":  _m(2, 0.50),  "ETHUSDT/180d": _m(2, 0.50),
            "SOLUSDT/90d":  _m(2, 0.50),  "SOLUSDT/180d": _m(2, 0.50),
        }
        agg = _m(32, 0.30, pf=1.7)  # aggregate looks good, but it's one coin
        verdict, reasons = research._verdict(subcells, agg, _good_wf())
        self.assertEqual(verdict, "REJECT", reasons)
        self.assertTrue(any("symbol" in r for r in reasons), reasons)

    def test_aggregate_negative_rejects(self):
        """Blended result not profitable after fees → REJECT, no partial credit."""
        subcells = _robust_subcells()
        agg = _m(40, -0.05, pf=0.9)
        verdict, reasons = research._verdict(subcells, agg, _good_wf())
        self.assertEqual(verdict, "REJECT", reasons)
        self.assertTrue(any("aggregate" in r for r in reasons), reasons)

    def test_sparse_coverage_rejects(self):
        """Too few attempted cells reached MIN_TRADES → REJECT (breadth #2)."""
        subcells = {
            # 2 tradable across 2 symbols (passes the ≥2-symbols guard) ...
            "BTCUSDT/90d":  _m(9, 0.30), "ETHUSDT/90d": _m(9, 0.28),
            # ... but the other 4 attempted cells are sparse (< MIN_TRADES).
            "BTCUSDT/180d": _m(3, 0.30), "ETHUSDT/180d": _m(3, 0.30),
            "SOLUSDT/90d":  _m(3, 0.30), "SOLUSDT/180d": _m(3, 0.30),
        }
        # 2 / 6 traded = 0.33 < MIN_TRADED_FRAC (0.60)
        agg = _m(30, 0.29, pf=1.6)
        verdict, reasons = research._verdict(subcells, agg, _good_wf())
        self.assertEqual(verdict, "REJECT", reasons)
        self.assertTrue(any("sub-cell" in r for r in reasons), reasons)

    def test_not_enough_total_trades_rejects(self):
        """Below MIN_TOTAL_TRADES aggregate → REJECT (untrustworthy sample)."""
        subcells = {"BTCUSDT/90d": _m(6, 0.30), "ETHUSDT/90d": _m(6, 0.30)}
        agg = _m(12, 0.30, pf=1.6)  # < MIN_TOTAL_TRADES (20)
        verdict, reasons = research._verdict(subcells, agg, _good_wf())
        self.assertEqual(verdict, "REJECT", reasons)
        self.assertTrue(any("total trades" in r for r in reasons), reasons)

    def test_one_negative_cell_rejects(self):
        """Every traded cell must be positive — one loser kills it → REJECT."""
        subcells = _robust_subcells()
        subcells["SOLUSDT/180d"] = _m(8, -0.10, pf=0.8)  # a single loser
        agg = _m(48, 0.18, pf=1.3)  # aggregate still positive
        verdict, reasons = research._verdict(subcells, agg, _good_wf())
        self.assertEqual(verdict, "REJECT", reasons)
        self.assertTrue(any("negative after fees" in r for r in reasons), reasons)

    def test_weak_walk_forward_rejects(self):
        """Majority of out-of-sample folds negative → REJECT (no consistency)."""
        subcells = _robust_subcells()
        agg = _m(48, 0.24, pf=1.6)
        weak_wf = [_m(10, -0.1), _m(10, -0.2), _m(10, 0.1), _m(10, -0.3)]
        verdict, reasons = research._verdict(subcells, agg, weak_wf)
        self.assertEqual(verdict, "REJECT", reasons)
        self.assertTrue(any("walk-forward" in r for r in reasons), reasons)


class GateTests(unittest.TestCase):
    """Pin the DEFAULT-SAFE behavior of the live auto-disable gate."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_dir = research.RESEARCH_DIR
        self._orig_path = research.VALIDATED_PATH
        research.RESEARCH_DIR = self.tmp
        research.VALIDATED_PATH = os.path.join(self.tmp, "validated_strategies.json")

    def tearDown(self):
        research.RESEARCH_DIR = self._orig_dir
        research.VALIDATED_PATH = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_allowlist_blocks(self):
        """No file at all → gate blocks (False, None)."""
        self.assertFalse(os.path.exists(research.VALIDATED_PATH))
        allowed, entry = research.is_strategy_validated("Donchian Breakout", "1h")
        self.assertFalse(allowed)
        self.assertIsNone(entry)

    def test_empty_allowlist_blocks(self):
        """An empty validated[] (e.g. a run that found NO edge) → gate blocks."""
        research.save_validated([], fee=0.1, slip=0.02)
        self.assertEqual(research.load_validated().get("validated"), [])
        allowed, _ = research.is_strategy_validated("Donchian Breakout", "1h")
        self.assertFalse(allowed)

    def test_corrupt_allowlist_blocks(self):
        """Unparseable file → load returns safe default → gate blocks."""
        with open(research.VALIDATED_PATH, "w") as f:
            f.write("{ this is not valid json")
        self.assertEqual(research.load_validated(), {"validated": []})
        allowed, _ = research.is_strategy_validated("Donchian Breakout", "1h")
        self.assertFalse(allowed)

    def test_accepted_pair_is_allowed(self):
        """A saved ACCEPTED cell allows exactly its (strategy, interval)."""
        cell = {
            "signal_name": "Donchian Breakout",
            "strategy":    "Donchian Breakout (HTF)",
            "interval":    "1h",
            "exit_policy": {"use_atr": True, "sl_pct": 0.4, "tp_pct": 0.8},
            "aggregate":   {"expectancy_pct": 0.30, "profit_factor": 1.6,
                            "trades": 40},
        }
        research.save_validated([cell], fee=0.1, slip=0.02)

        allowed, entry = research.is_strategy_validated("Donchian Breakout", "1h")
        self.assertTrue(allowed)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["interval"], "1h")

        # Wrong timeframe and wrong strategy are NOT allowed.
        self.assertFalse(research.is_strategy_validated("Donchian Breakout", "4h")[0])
        self.assertFalse(research.is_strategy_validated("Trend Pullback", "1h")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
