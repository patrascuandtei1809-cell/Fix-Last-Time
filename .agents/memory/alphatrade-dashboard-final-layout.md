---
name: AlphaTrade dashboard FINAL DASHBOARD RULE layout
description: The operator-decreed fixed layout for trading/dashboard.py — what may appear, what must never be re-added, and the Legacy Holdings contract.
---

# FINAL DASHBOARD RULE (operator-locked layout)

`trading/dashboard.py` must render ONLY this, in this order:

1. **GLOBAL RULES** bar — once, at the very top (`_render_global_rules_bar`), applies to both venues.
2. **BINANCE DASHBOARD** — Wallet Overview → Core Markets (BTC/ETH/SOL only) → Binance Chart (entry/exit BUY/SELL markers + SL/TP "stop" lines) → Active Trades → Legacy Holdings → AI Decisions.
3. **MEXC DASHBOARD** — Wallet Overview → Live Scanner → Scanner Coin Charts → Active Trades (cap 15) → AI Decisions → Rotation Engine.

**Removed and must NOT be re-added** (was the bottom `st.tabs()` block): Trade History tab, Activity Log tab (incl. the duplicated per-symbol "scanner cards" and dip diagnostics), Stats tab (after-fee P&L / breakeven / carry-breakeven research). JSON persistence under `data/` is untouched — the data still exists, it just has no UI.

**Why:** the operator (emphatically, repeatedly) demanded "keep ONLY this structure, remove everything old or duplicated… real data only." Re-introducing history/stats/activity tabs or duplicated scanner cards is a regression against an explicit instruction, not an improvement.

**How to apply:** when asked to add a panel, fit it into one of the three sections above (or confirm with the operator). Treat the Binance wallet cards + spending meter + equity sparkline as the single "Wallet Overview".

# Legacy Holdings contract (`_render_binance_legacy`)

Legacy Holdings must show **every** Binance coin held that is NOT a core market (BTC/ETH/SOL) and NOT a stablecoin (`_FEE_STABLES`). It is a real `st.dataframe` with columns **Coin · Qty · Value · PnL · Status** — built from BOTH priced `account_holdings` AND no-price-feed `account_unpriced` (Value `—`, Status `No price feed`), so nothing is hidden. Status = Open position / Dust (<~$10) / Holding / No price feed. The bot NEVER auto-buys legacy coins; the only action is the manual "Convert a legacy coin to USDT" expander (real LIVE sell, per-coin min-notional guard, confirm checkbox), whose sellable list also filters via the same `_is_legacy` rule and requires a positive free balance.

**Why:** the operator holds many non-core coins (ZEC, NEAR, WLD, HEI, ATM, FIDA, OSMO, TRX…) and insisted they all appear, never be ignored, stay separate from Core Markets + AI Decisions, and be closeable to USDT.

**Note (Replit only):** under the Binance 451 geo-block, `public_price` fails for everything, so locally ALL coins fall into `account_unpriced` and show "No price feed". On the droplet they price normally. This is environment-expected, not a bug.

# Gotcha: trade["coin"] is the FULL symbol, not the bare asset

A persisted trade stores `"coin": self.symbol` → e.g. `"ZECUSDT"`, NOT `"ZEC"`. Any per-asset lookup against open trades (PnL, "has open position?") must key by `f"{ASSET}USDT"`, never the bare asset.

**Why:** a legacy-table PnL/Status lookup keyed by bare asset silently matched nothing — PnL rendered "—" and "Open position" never fired. `_cur_price_for(_ot.get("coin"))` works precisely because `coin` is already a full symbol that `public_price()` accepts.

**How to apply:** when joining holdings/balances (asset-keyed) to trades (symbol-keyed), bridge with `f"{asset}USDT"` or strip the quote suffix — don't assume `coin == asset`.
