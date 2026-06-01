---
name: AlphaTrade P&L — GROSS headline + fee-aware NET (real fees now captured)
description: profit_loss stays GROSS forever; net_pnl/total_fees are real ONLY when fees_complete, else estimate.
---

# Stored `profit_loss` is GROSS; fee-aware NET is additive and conditional

In `bot.close_trade()` the per-trade `profit_loss` / `profit_loss_pct` are
**always** the raw price move `(exit-entry)/entry * invested`. This is a
permanent back-compat invariant — never make `profit_loss` net. Fee-aware
figures are stored as **additive** fields: `entry_fee`, `exit_fee`,
`total_fees`, `net_pnl`, `net_pnl_pct`, plus a completeness flag `fees_complete`.

**Real fees ARE now captured** (no longer "fees are never recorded"):
- Bot entries stamp `entry_fee` at open; bot closes pass a real `exit_fee` from
  the exchange response.
- All three manual OPEN paths (ForceTestBuy, BuyNow90, Manual BUY/SELL) and the
  manual CLOSE path use the shared `_order_fee_usdt(order, coin, fill_price)`
  helper in `dashboard.py` to convert raw Binance commissions to USDT
  (stablecoins direct; base asset × fill_price; else × `public_price(assetUSDT)`;
  fails open to 0.0).

**`fees_complete` is the trust gate** — `had_entry = ("entry_fee" in t) and
entry_fee>0`; `fees_complete = had_entry and exit_fee>0`. It is False for legacy
trades (predate fee capture) AND for any leg whose fee conversion failed to 0.

**How to apply:** the dashboard "Real P&L" panel and per-trade table use real
`total_fees`/`net_pnl` ONLY when `fees_complete` is True; otherwise they fall
back to the configurable per-side rate ESTIMATE (`(invested+exit_notional)*fr`),
and the table prefixes estimated values with `≈`. This was a deliberate
honesty fix: a partial capture (e.g. manual entry fee missing) was being shown
as exact and OVERSTATED net P&L. Never count a partial/legacy row as "real".

**Why it matters:** for a small-TP scalper paying ~0.1%/side (~0.2% round trip),
net reality can be far worse than gross — fees can exceed the entire gross
profit. The breakeven win rate `(avg_gross_loss + avg_fee)/(avg_gross_win +
avg_gross_loss)` is the honest framing for "why am I not profitable".
