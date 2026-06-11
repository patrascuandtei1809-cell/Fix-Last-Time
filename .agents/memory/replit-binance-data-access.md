---
name: Binance historical data access from Replit
description: Geo-blocked hosts vs reachable mirror for real Binance market data; the live app's public reads fail over to the mirror while authed/order calls never do.
---

# Binance data access from geo-blocked Replit

Replit dev is geo-blocked from `api.binance.com` (HTTP 451). But Binance's
public market-data mirror **`https://data-api.binance.vision`** IS reachable
from Replit and serves the **identical** `/api/v3/klines` schema and prices as
live Binance Mainnet. This is what makes on-Replit backtesting of the REAL
strategy possible despite the geo-block.

Reachability probe (May 2026), `/api/v3/klines?symbol=BTCUSDT&interval=1m`:
- `data-api.binance.vision` — **OK** (primary; same data as live)
- `api.binance.us` — OK (fallback; same `/api/v3/klines` contract)
- `api.kraken.com`, `api.exchange.coinbase.com`, `www.okx.com` — OK (different schemas)
- `api.binance.com` — 451 (geo-blocked)
- `api.bybit.com` — 403

**Why:** the live bot trades on api.binance.com which can't be reached from
Replit, so any analysis tooling that needs real Binance candles must use the
.vision mirror (or binance.us). `trading/backtest.py` uses .vision then
binance.us as fallback, paginating backward via `endTime`, caching CSVs to
`trading/data/backtest/`.

**How to apply:** for any future "fetch real Binance history on Replit" need
(backtests, indicator checks, data viz), hit `data-api.binance.vision`, not
`api.binance.com`. It needs no API key (public data only — no orders).

# Live app: public reads fail over, authed/orders never do

The dashboard/bot's READ-ONLY public market data (prices, klines, 24h stats)
must fail over from `api.binance.com` → `data-api.binance.vision` so the Binance
UI (Core Markets, Market-Low %, chart) shows REAL data even from a geo-blocked
host. The shared helper tries each base and caches the last-good one; identical
data + dedupe-by-open_time makes a mid-pagination base switch safe.

**Why:** without this, the entire Binance section renders empty/ERR on Replit and
looks broken even though the code is correct — the operator reads "nothing works".
**How to apply (CRITICAL separation):** ONLY public market data uses the mirror.
Authenticated balance reads and ALL order placement MUST stay on `api.binance.com`
via the python-binance Client — the .vision mirror is data-only (no account/order
endpoints), so routing auth/orders through it would be wrong or impossible. A
chart kline fetch may fall back authed→public (klines are public); a balance or
order must surface the auth/geo error loudly, never silently use the mirror.
