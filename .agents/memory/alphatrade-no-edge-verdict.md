---
name: AlphaTrade after-fee edge verdict
description: Result of the Strategy Research Framework sweep — no strategy×timeframe clears the fee hurdle; live bot stays auto-disabled.
---

# Honest verdict: NO after-fee edge found

The Strategy Research Framework (`trading/research.py`, engine `trading/backtest.py`)
sweeps strategy × timeframe × symbol × period and ranks by **net expectancy/trade
after fees**. Cost model: 0.1%/side fee + 0.02%/side slippage ≈ **0.24% round-trip**.

**Result: every candidate REJECTS.** No strategy×timeframe produced a robust
positive after-fee expectancy under the strict ACCEPT rule (net exp > 0 AND PF ≥ 1
on EVERY cell with ≥5 trades, ≥20 total trades, majority walk-forward folds positive).

- Closest: Trend Pullback @ 4h aggregate net **+0.051%/trade** — but that is NOT an
  edge: it is carried entirely by SOL (+1.23% on only 15 trades) while BTC (−0.20%)
  and ETH (−0.71%) are negative. One lucky symbol on a tiny sample → correctly REJECTED.
- EMA/MACD/RSI/Vol V2 @ 4h: −0.122%; Trend Pullback @ 1h: −0.214%; 1m reversal
  baseline: −0.250% (fees alone ≈ −0.24%, confirming the 1m scalper is structurally dead).
- Donchian breakout was the worst (−0.42% to −0.49%) — breakouts on crypto majors
  mostly fail/whipsaw at these timeframes.

**Why this matters / how to apply:**
- The pattern across ALL cells: gross edge (if any) is smaller than the ~0.24%
  round-trip cost. Win rates sit ~27–37% with PF < 1. Lowering thresholds to trade
  MORE only multiplies the fee drag — the historical failure mode of this project.
- The live bot now has an **auto-disable gate** (`is_strategy_validated` in
  research.py, enforced in `bot.py` before `execute_entry`). The allowlist
  (`data/research/validated_strategies.json`) is EMPTY, so the bot will not place
  auto orders. This is the correct, honest default. Manual trades bypass the gate.
- Override only for deliberate experiments: `ALPHATRADE_ALLOW_UNVALIDATED=1`.
- To change the verdict you need a genuinely different edge source (e.g. funding/basis,
  cross-exchange, on-chain/news — Phase 3 paid feeds), NOT more threshold tuning.
- Runtime note: the weighted-gate 1m baseline is very slow per-candle (recomputes
  indicators on a sliced df each bar). Keep 1m baseline tiny (BTC, a few days); HTF
  (1h/4h) cells are fast. Background/detached jobs do NOT persist across tool calls
  in the Replit sandbox — run sweeps synchronously within the command timeout.

## Hardened ACCEPT rule (breadth guards)
`_verdict()` requires ALL of: aggregate net exp > 0 AND PF ≥ 1; edge on ≥2
symbols (`MIN_SYMBOLS`, not one lucky coin); ≥60% of attempted subcells reach
`MIN_TRADES` (`MIN_TRADED_FRAC`, stops sparse cells hiding losers behind the
"every traded cell passes" test); every traded cell net exp > 0 AND PF ≥ 1;
≥20 total trades; majority walk-forward folds positive.
**Why:** the original rule only checked positivity on cells with ≥5 trades, so a
single strong subcell could pass while others were sparse/negative and excluded.
**How to apply:** when adding strategies/symbols, an ACCEPT must clear all guards
— do not relax them to get a green light.
