---
name: AlphaTrade min-notional sizing must floor up, not block
description: Why the scalper "never trades" on small accounts, and the floor-vs-block rule for Binance $10 min notional.
---

# Position sizing must FLOOR UP to the min notional, never block

In `symbol_worker.execute_entry()` the score-tiered sizing computes
`invested = free_usdt * pct/100` (default/standard tier = 10%). Binance Spot
has a **$10 minimum notional**. The bug: the code BLOCKED any order whose
computed notional was `< $10`. On a small account this silently halts ALL
trading — e.g. $66 free × 10% = $6.60 < $10 → blocked every single tick, so the
operator sees "the bot never trades" with no obvious cause.

**Rule:** floor the order UP to $10 when affordable, only block if genuinely too
small:
- `_ceiling = free_usdt * 0.75` (keep 25% reserve)
- `invested = min(tier_size, _ceiling)`
- if `invested < 10`: set `invested = 10` **iff** `_ceiling >= 10`; else block
  (account truly too small for even one min-notional order while keeping reserve).

**Why:** `replit.md` (ACTIVE SCALPER) already specifies size is "floored at $10
(Binance min notional)" — the code contradicted its own documented intent.
Flooring is safe: $10 only triggers when free ≥ ~$13.33, so it never breaches
the 25% reserve or `free_usdt`, and a $10 notional at 0.4% SL risks ~4¢.

**How to apply:** any time a sizing/tier change is made, re-check that small
accounts still reach the min notional. The decision engine (strategy /
weighted_decision / ai_decide / classify_regime) is pure Python and **can be
unit-tested on geo-blocked Replit with synthetic OHLCV** — only order placement
and balance fetch need Binance. Use a synthetic harness to prove signals are
directional with score>0 before suspecting the engine; the usual culprit for
"never trades" on a connected account is this sizing gate, not the edge engine.
