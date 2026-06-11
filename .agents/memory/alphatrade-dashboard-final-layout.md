---
name: AlphaTrade dashboard FINAL DASHBOARD RULE layout
description: The operator-decreed fixed layout for trading/dashboard.py — what may appear and what must never be re-added.
---

# FINAL DASHBOARD RULE (operator-locked layout)

`trading/dashboard.py` must render ONLY this, in this order:

1. **GLOBAL RULES** bar — once, at the very top (`_render_global_rules_bar`), applies to both venues.
2. **BINANCE DASHBOARD** — Wallet Overview → Core Markets (BTC/ETH/SOL only) → Binance Chart (entry/exit BUY/SELL markers + SL/TP "stop" lines) → Active Trades → Legacy Holdings → AI Decisions.
3. **MEXC DASHBOARD** — Wallet Overview → Live Scanner → Scanner Coin Charts → Active Trades (cap 15) → AI Decisions → Rotation Engine.

**Removed and must NOT be re-added** (was the bottom `st.tabs()` block): Trade History tab, Activity Log tab (incl. the duplicated per-symbol "scanner cards" and dip diagnostics), Stats tab (after-fee P&L / breakeven / carry-breakeven research). JSON persistence under `data/` is untouched — the data still exists, it just has no UI.

**Why:** the operator (emphatically, twice) demanded "keep ONLY this structure, remove everything old or duplicated… real data only." Re-introducing history/stats/activity tabs or duplicated scanner cards is a regression against an explicit instruction, not an improvement.

**How to apply:** when asked to add a panel, fit it into one of the three sections above (or confirm with the operator). Treat the Binance wallet cards + spending meter + equity sparkline as the single "Wallet Overview". Legacy Binance coins must stay closeable to USDT (real sell, min-notional guard, confirm checkbox) with the convert panel open and nothing hidden — don't collapse or cap that list.
