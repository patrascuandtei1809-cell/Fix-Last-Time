"""
test_order_sizing.py — lock in compute_order_amount sizing rules
================================================================

Focus: the "Use 100% of free USDT" (SIZE_ALL) mode must deploy the ENTIRE free
balance so a small account can still meet the Binance $10 min-notional. The
regression that motivated this: free=$10.95, a 75% reserve capped the order at
$8.21 < $10 and the trade was wrongly blocked as "insufficient balance".

SIZE_ALL bypasses the 25% reserve AND the max-position-% cap, but STILL honours:
  * the Binance min-notional floor,
  * the operator's explicit hard-$ cap and spending limit (opt-in budgets).

Run:  cd trading && python -m pytest tests/test_order_sizing.py -v
"""

import unittest

import live_engine
from live_settings import (
    LiveSettings, SIZE_ALL, SIZE_PERCENT, SIZE_FIXED,
)


class TestComputeOrderAmount(unittest.TestCase):
    # ── the reported bug ─────────────────────────────────────────────────────
    def test_use_all_small_balance_can_meet_min_notional(self):
        """free=$10.95, SIZE_ALL → CAN TRADE (full balance ≥ $10), not blocked."""
        s = LiveSettings(size_mode=SIZE_ALL)
        amount, ok, reason = live_engine.compute_order_amount(s, 10.95, 0.0)
        self.assertTrue(ok, f"should be tradeable, got blocked: {reason}")
        self.assertAlmostEqual(amount, 10.95, places=2)
        self.assertEqual(reason, "")

    def test_percent_75_small_balance_is_blocked(self):
        """Same balance under the old 75% behaviour (%-mode) is genuinely too
        small once the reserve applies — proves the fix is scoped to SIZE_ALL."""
        s = LiveSettings(size_mode=SIZE_PERCENT, portfolio_percent=75.0,
                         max_position_pct=0.0)
        amount, ok, reason = live_engine.compute_order_amount(s, 10.95, 0.0)
        self.assertFalse(ok)
        self.assertEqual(amount, 0.0)
        self.assertIn("Insufficient balance", reason)

    # ── SIZE_ALL bypasses reserve + max-position-% ───────────────────────────
    def test_use_all_deploys_full_balance(self):
        s = LiveSettings(size_mode=SIZE_ALL, max_position_pct=50.0)
        amount, ok, _ = live_engine.compute_order_amount(s, 200.0, 0.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(amount, 200.0, places=2)   # NOT 150 (75%) or 100 (50%)

    # ── SIZE_ALL still honours genuine "too small" ───────────────────────────
    def test_use_all_below_min_notional_is_blocked(self):
        s = LiveSettings(size_mode=SIZE_ALL)
        amount, ok, reason = live_engine.compute_order_amount(s, 9.50, 0.0)
        self.assertFalse(ok)
        self.assertEqual(amount, 0.0)
        self.assertIn("Insufficient balance", reason)

    # ── SIZE_ALL still honours explicit opt-in caps ──────────────────────────
    def test_use_all_respects_hard_dollar_cap(self):
        s = LiveSettings(size_mode=SIZE_ALL, max_position_size_usdt=50.0)
        amount, ok, _ = live_engine.compute_order_amount(s, 200.0, 0.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(amount, 50.0, places=2)

    def test_use_all_respects_spending_limit(self):
        s = LiveSettings(size_mode=SIZE_ALL, bot_spending_limit_usdt=100.0)
        # $80 already deployed ⇒ only $20 of budget remains.
        amount, ok, _ = live_engine.compute_order_amount(s, 200.0, 80.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(amount, 20.0, places=2)

    # ── floor-up must NEVER overrun an explicit cap below the min-notional ────
    def test_use_all_hard_cap_below_min_blocks_no_overrun(self):
        """Hard-$ cap $5 < $10 min ⇒ CANNOT TRADE (must not floor up to $10)."""
        s = LiveSettings(size_mode=SIZE_ALL, max_position_size_usdt=5.0)
        amount, ok, reason = live_engine.compute_order_amount(s, 100.0, 0.0)
        self.assertFalse(ok)
        self.assertEqual(amount, 0.0)
        self.assertIn("Insufficient balance", reason)

    def test_use_all_spending_remaining_below_min_blocks_no_overrun(self):
        """Spending budget leaves only $8 (< $10 min) ⇒ CANNOT TRADE, no overrun."""
        s = LiveSettings(size_mode=SIZE_ALL, bot_spending_limit_usdt=100.0)
        amount, ok, reason = live_engine.compute_order_amount(s, 200.0, 92.0)
        self.assertFalse(ok)
        self.assertEqual(amount, 0.0)
        self.assertIn("Insufficient balance", reason)

    # ── non-ALL modes keep the 75% reserve (unchanged behaviour) ─────────────
    def test_percent_mode_keeps_reserve_ceiling(self):
        s = LiveSettings(size_mode=SIZE_PERCENT, portfolio_percent=100.0,
                         max_position_pct=0.0)
        amount, ok, _ = live_engine.compute_order_amount(s, 200.0, 0.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(amount, 150.0, places=2)   # 75% reserve still applies

    def test_fixed_mode_unchanged(self):
        s = LiveSettings(size_mode=SIZE_FIXED, fixed_usdt_amount=25.0)
        amount, ok, _ = live_engine.compute_order_amount(s, 200.0, 0.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(amount, 25.0, places=2)


if __name__ == "__main__":
    unittest.main()
