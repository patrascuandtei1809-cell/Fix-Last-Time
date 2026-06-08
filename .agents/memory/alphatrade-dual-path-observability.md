---
name: AlphaTrade dual-path observability + portfolio separation
description: The two live bot paths (V2 strategy_mode vs Market Low dip_mode) must mirror each other's logging/UI; Binance and MEXC wallets must never be merged.
---

# Dual live-bot paths must mirror observability

`bot.create_bot` picks ONE live path from the persisted strategy name:
`strategy=="EMA_MACD_RSI_VOLUME_V2"` → `strategy_mode` (V2 @ settings interval, e.g. 4h,
research-gated via StrategyLiveEngine); anything else → `dip_mode` ("Market Low" 1m
DipLiveEngine). The running bot exposes `.strategy_mode/.dip_mode/.live_strategy_name/
.live_strategy_interval` — these are the authoritative source for the active-strategy
banner. Never hardcode the strategy label in the dashboard.

**Why:** The dip cycle logged `[SCAN]` to activity.json + diagnostics every tick, but the
V2 `_run_strategy_cycle` historically logged NEITHER — so on a V2 account the activity
feed and per-symbol diagnostics looked dead even though the bot was scanning. Any new
behavior added to one path's cycle (logging, diagnostics counters, UI panels) must be
mirrored on the other or one mode silently loses observability.

**How to apply:** When touching either cycle in `bot.py`, check the sibling cycle.
StrategyLiveEngine AND DipLiveEngine both `_publish` ActivityRecord
(symbol/at/decision/reason) → `live_engine.get_all_activity()`, so a single dashboard
panel can read both paths; build per-symbol UI off that store and keep path-specific
detail panels guarded by the active path.

# Binance and MEXC ("MCD") wallets are SEPARATE, never mixed

Account value / holdings are Binance-only. MEXC must render in its own clearly-labeled
section (DRY-RUN by default; `mexc_live_orders` flag). Pull MEXC via
`exchanges.mexc.load_mexc_credentials()` → `MexcExchange(MexcClient(*creds)).get_balance("USDT")`
({total,free,locked}). Never sum MEXC into the Binance equity/holdings math.
**Why:** operator explicitly needs to trust each exchange's number independently; a merged
total hides where real money actually is.
