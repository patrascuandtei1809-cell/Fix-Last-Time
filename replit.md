# AlphaTrade

A professional real-time crypto trading platform with Binance integration, multi-strategy auto bot, candlestick chart with indicators, risk management, and detailed activity logging.

## Run & Operate

- **Trading Dashboard**: `cd trading && streamlit run dashboard.py --server.port 5000 --server.address 0.0.0.0`
- Workflow name: `AlphaTrade Dashboard` — already configured, starts automatically

## Stack

- Python 3.11, Streamlit
- Binance API via `python-binance` (testnet + live)
- Plotly for candlestick charts with EMA 9/21 and Stochastic oscillator
- Thread-based bot that survives Streamlit page refreshes (global singleton)
- JSON file persistence for trades and activity logs

## Where things live

- `trading/dashboard.py` — main Streamlit app (UI, controls, chart, trade table, log)
- `trading/bot.py` — multi-symbol orchestrator: holds dict of SymbolWorker, one daemon thread, all trade/activity persistence
- `trading/symbol_worker.py` — per-symbol tick logic (signal → risk gate → exchange order)
- `trading/exchanges/{base,binance,registry}.py` — Exchange ABC + BinanceExchange + registry stub for future smart routing
- `trading/strategy.py` — EMA Crossover, Price Movement, Momentum (RSI) strategies
- `trading/risk.py` — `SymbolRiskSettings`/`RiskManager` (per-symbol) + `GlobalRiskSettings`/`GlobalRiskManager` (account-wide caps)
- `trading/binance_client.py` — Binance API wrapper (testnet + live), wrapped by `BinanceExchange`
- `trading/data/trades/<exchange>_<symbol>.json` — per-symbol trade history (one file per symbol+exchange)
- `trading/data/trades.json.bak` — archived legacy single-symbol trade file (auto-renamed on first boot)
- `trading/data/activity.json` — persisted activity log (shared across symbols)
- `trading/data/settings.json` — persisted user settings (incl. `active_symbols`, `per_symbol_risk`, `global_risk`)
- `trading/.streamlit/config.toml` — dark theme + server config

## Architecture decisions

- **Multi-symbol** (max 3 — e.g. BTC + ETH + SOL). Each symbol gets a `SymbolWorker` with its own `RiskManager`, last_trade state, session counter.
- **Exchange abstraction.** All trading goes through the `Exchange` interface (`get_price`, `place_buy_order`, ...). Today only `BinanceExchange` exists; the `exchanges.registry` has a `best_exchange_for()` stub for future smart routing.
- **Two-tier risk.** Per-symbol risk (size, SL, TP, max open) AND a `GlobalRiskManager` enforcing total USDT exposure, max % in one symbol, total open trades, and daily-loss auto-stop across all symbols. Global gate runs BEFORE every bot order.
- **One orchestrator thread** iterates all workers per tick (default 30s) — survives Streamlit reruns via module-level `_bot` singleton.
- **Per-symbol persistence** at `data/trades/<exchange>_<symbol>.json`. `add_trade()` routes by `trade["coin"]` + `trade["exchange"]`. `close_trade()` scans all files to find the id. `load_trades(symbol=...)` filters across files.
- **`type` field** is always stored on every trade; dashboard uses `.get("type", "manual")` defensively.
- **Manual trades and bot trades** have distinct marker colors/icons on the chart.
- **Stochastic Oscillator** (%K/%D) with overbought/oversold dashed lines below the candlestick chart.

## Adding a new exchange

1. Create `trading/exchanges/<name>.py` subclassing `Exchange` from `base.py`.
2. Implement: `get_price`, `get_klines`, `get_balance`, `place_buy_order`, `place_sell_order`, `get_positions`, `get_fees`, `round_quantity`.
3. In the dashboard, instantiate it and pass `exchange=...` to `bot_module.create_bot(...)`.
4. The registry will hold it automatically; smart routing comes later.

## Data migration (single → multi)

- On first boot of the refactored bot, `data/trades.json` is automatically renamed to `trades.json.bak` (user chose archive-not-migrate). New per-symbol files start fresh under `data/trades/`.

## How to run on DigitalOcean

1. SSH into your droplet
2. `git clone <your-repo> && cd your-repo/trading`
3. `pip install -r requirements.txt`
4. `nohup streamlit run dashboard.py --server.port 80 &` (or use nginx + gunicorn proxy)
5. Or: run inside `screen` or `tmux` so it persists after disconnect

## Switching Testnet → Live safely

1. In the sidebar, toggle OFF "Use Binance Testnet"
2. Enter your **Live** API key and secret (create at binance.com → API Management)
3. Turn OFF "Paper mode" to place real orders
4. Start small — set Risk per trade % to 0.5–1% and test one trade manually first
5. Keep emergency stop ready — one click halts all bot activity

## User preferences

- Dark professional UI (GitHub dark color scheme)
- Full Binance testnet + live support
- Bot must survive page refresh (solved via global thread singleton)
- Manual and bot trades must have distinct markers on chart
- Activity log must explain every decision (buy/sell/skip reasons)

## Gotchas

- Always connect to Binance first (even in paper mode — live prices are needed for chart + signals)
- The bot check interval is in seconds; default 30s = checks every 30 seconds
- `trading/data/` is auto-created on first run; safe to delete to reset all data
- Binance testnet keys expire; regenerate at testnet.binance.vision if connection fails
