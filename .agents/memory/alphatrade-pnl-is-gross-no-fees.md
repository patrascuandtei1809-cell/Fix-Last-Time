---
name: AlphaTrade stored P&L is GROSS — fees are never recorded
description: Why the dashboard's headline profit overstates reality and any profitability analysis must estimate fees.
---

# Stored `profit_loss` is a raw price difference — fees are NOT in the data

In `bot.close_trade()` the per-trade `profit_loss` / `profit_loss_pct` are
computed purely as `(exit-entry)/entry * invested`. Binance commissions are
**never stored** on a trade record (no `fee`/`commission` field; the exchange
`fills[]` are used only to derive an average fill price, then discarded).

**Implication:** every P&L number on the dashboard (Realized/Total/Daily P&L,
Win Rate, per-trade table) is **GROSS**. For a high-frequency scalper paying a
fee on BOTH entry and exit (~0.1%/side = ~0.2% round trip), net reality can be
dramatically worse — fees can exceed the entire gross profit.

**Why it matters:** the operator was losing money while the dashboard looked
roughly break-even/positive. The structural cause of a small-TP scalper losing
is usually fees + needing a very high win rate just to break even, not a bug.

**How to apply:** any "why am I not profitable / show me real P&L" request must
account for fees. Since they aren't recorded, you can only ESTIMATE them at a
configurable per-side rate and must label the result as an estimate. The
data-driven breakeven win rate is `(avg_gross_loss + avg_fee_per_trade) /
(avg_gross_win + avg_gross_loss)`. If you ever want EXACT net P&L, you'd have to
start persisting `commission`/`commissionAsset` from the order `fills[]` at
open and close time. Do NOT silently change existing gross displays — add net as
a clearly-labelled separate view.
