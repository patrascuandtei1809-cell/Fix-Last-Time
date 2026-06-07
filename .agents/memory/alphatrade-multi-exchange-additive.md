---
name: AlphaTrade multi-exchange (MEXC) additive integration
description: How MEXC + scanner were added without rebuilding the Binance bot, and the safety/decoupling boundaries that must hold.
---

# MEXC + scanner — additive, not a rebuild

The Binance LIVE bot was extended with a second exchange (MEXC) and a
cross-venue symbol scanner WITHOUT touching the Binance execution path.

## Where safety actually lives
- **DRY-RUN is enforced inside `MexcExchange`, not the dashboard.** Default
  `live_orders=False` → orders are simulated regardless of caller. Real orders
  only when `live_orders=True` AND an authenticated `MexcClient` is present.
  BUY guards (cap 2 USDT/buy, block if free USDT < 5, volatility/24h-change
  refusal) live at the execution method, so a UI bypass can't skip them.
  **Why:** the dashboard is a thin, rerun-driven layer; safety must not depend
  on it.

## Decoupling boundary (known limitation, intentional)
- The dashboard gates **bot start on a Binance client** (`_cl()`), so MEXC-only
  execution still requires a Binance connection. `create_bot(exchange_mode=
  "mexc")` already ignores the Binance `client` and builds its own
  `MexcExchange` from saved creds — routing works — but the *start UI* is
  Binance-centric (equity, chart, balance all need it). Fully decoupling
  MEXC-only startup was deliberately left out: it would mean rebuilding the
  start flow, violating the "ADD, don't rebuild" constraint.

## Dynamic symbols
- Dynamic `active_symbols` are driven through the existing `create_bot()` rebuild
  path (Streamlit rerun), NOT by mutating the live orchestrator loop. The
  3-symbol cap was raised to `MAX_ACTIVE_SYMBOLS=15` for ALL modes; default
  `active_symbols` is still BTC+ETH+SOL, so default behavior is unchanged in
  practice. Scanner output is read-only public data; `_effective_bot_symbols()`
  falls back to configured symbols whenever the scan is empty so the bot never
  starts symbol-less.
- `multi` mode is NOT yet routable — it falls back to Binance execution on
  purpose (no cross-exchange order routing built), while the scanner can still
  surface both venues.
