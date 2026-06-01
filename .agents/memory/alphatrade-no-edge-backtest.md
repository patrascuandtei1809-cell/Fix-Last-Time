---
name: AlphaTrade strategy has no backtested edge
description: The current Reversal/weighted scalper loses money after costs in backtest; the structural reason future strategy work must fix.
---

# The scalper has NO edge after costs (backtest finding, May 2026)

A faithful replay (`trading/backtest.py`) of the live engine
(`strategy.weighted_decision` with AI excluded + `classify_regime` +
`get_signal`, qualified like `bot.py`, exits = SL/TP/breakeven/2-red-candle from
`symbol_worker`) over real Binance candles, with 0.1%/side fee + 0.02%/side
slippage (≈0.24% round-trip), shows a **catastrophic negative edge**, stable
across the FULL suite — 30/90/180d, all 3 symbols (BTC/ETH/SOL), every
walk-forward fold (each fold −0.23…−0.25%/trade):
- win rate ≈ 3–9%, expectancy ≈ **−0.24%/trade** (dead flat regardless of symbol
  or period), profit factor ≈ 0.02–0.10, ≈48 trades/day/symbol, avg hold ≈ 5–6 bars.
- 180d net per symbol ≈ −2070% to −2110% (sum of per-trade returns; ~8600 trades),
  with FEES (≈1700–1740%) dwarfing already-negative GROSS (≈−345% to −373%).

**Structural cause (the durable lesson):** avg LOSS ≈ −0.26% ≈ the round-trip
cost itself. The **2-red-candle exit fires within ~5 bars almost every time**,
closing trades near entry-minus-fees before the +0.8% TP can be reached, while
the bot **over-trades** (~50 entries/day/symbol). It is a fee-bleeding machine:
constant entries + premature exits + 0.24% cost = guaranteed slow loss.
Raising `score_threshold` barely cuts trade count because the qualify rule's
**OR-path (`rule_conf >= conf_floor=30`) floods entries regardless of score** —
so "just be more selective via threshold" does NOT help as currently wired.

**Why it matters:** bigger position size or faster execution CANNOT fix a
negative expectancy — they lose faster. Any real fix must change the EDGE:
fewer/higher-quality entries, an exit that lets winners reach a TP that clears
0.24% costs with margin, and a qualify rule where selectivity actually bites.

**How to apply:** before claiming any strategy change is an improvement, re-run
`backtest.py` and require CONSISTENTLY positive expectancy across walk-forward
folds. Caveat: GPT advisory layer is not replayed (non-deterministic), and spot
is long-only (SELL entries can't short) — so the backtest is the rule-engine's
edge, slightly more permissive than live.
