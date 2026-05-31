---
name: Binance historical data access from Replit
description: Which market-data hosts are reachable from geo-blocked Replit for fetching real Binance candles (e.g. for backtesting).
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
