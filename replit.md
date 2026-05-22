# AlphaTrade

LIVE-only Binance Mainnet trading dashboard. **Every BUY/SELL is a real order on api.binance.com.** No paper, no testnet, no simulated fills.

## Run & Operate

- **Trading Dashboard**: `cd trading && streamlit run dashboard.py --server.port 5000 --server.address 0.0.0.0`
- Workflow name: `AlphaTrade Dashboard` — already configured, starts automatically

## Stack

- Python 3.11, Streamlit
- Binance API via `python-binance` against **api.binance.com only** (LIVE Mainnet)
- Plotly for candlestick charts with EMA 9/21 and Stochastic oscillator
- Thread-based bot that survives Streamlit page refreshes (global singleton)
- JSON file persistence for trades and activity logs

## Where things live

- `trading/dashboard.py` — main Streamlit app (UI, controls, chart, trade table, log)
- `trading/bot.py` — multi-symbol orchestrator: holds dict of SymbolWorker, one daemon thread, all trade/activity persistence
- `trading/symbol_worker.py` — per-symbol tick logic (signal → risk gate → real exchange order)
- `trading/exchanges/{base,binance,registry}.py` — Exchange ABC + LIVE BinanceExchange + registry stub for future smart routing
- `trading/strategy.py` — EMA Crossover, Price Movement, Momentum (RSI) strategies
- `trading/risk.py` — `SymbolRiskSettings`/`RiskManager` (per-symbol) + `GlobalRiskSettings`/`GlobalRiskManager` (account-wide caps)
- `trading/binance_client.py` — LIVE Binance Mainnet API wrapper, wrapped by `BinanceExchange`
- `trading/data/trades/binance_<symbol>.json` — per-symbol trade history
- `trading/data/activity.json` — persisted activity log (shared across symbols)
- `trading/data/settings.json` — persisted user settings (incl. `active_symbols`, `per_symbol_risk`, `global_risk`)
- `trading/.streamlit/config.toml` — dark theme + server config

## Architecture decisions

- **LIVE Mainnet only.** Every order goes to `api.binance.com`. No paper/testnet/simulated fills. If the client is missing, orders raise — no silent fallback.
- **Multi-symbol** (max 3 — e.g. BTC + ETH + SOL). Each symbol gets a `SymbolWorker` with its own `RiskManager`, last_trade state, session counter.
- **Exchange abstraction.** All trading goes through the `Exchange` interface (`get_price`, `place_buy_order`, ...). Today only `BinanceExchange` exists; the `exchanges.registry` has a `best_exchange_for()` stub for future smart routing.
- **Two-tier risk.** Per-symbol risk (size, SL, TP, max open) AND a `GlobalRiskManager` enforcing total USDT exposure, max % in one symbol, total open trades, and daily-loss auto-stop across all symbols. Global gate runs BEFORE every bot order.
- **One orchestrator thread** iterates all workers per tick (default 30s) — survives Streamlit reruns via module-level `_bot` singleton.
- **Per-symbol persistence** at `data/trades/binance_<symbol>.json`. `add_trade()` routes by `trade["coin"]` + `trade["exchange"]`. `close_trade()` scans all files to find the id. `load_trades(symbol=...)` filters across files.
- **Manual LIVE trades require explicit confirmation.** Clicking BUY/SELL stages a pending order; user must press "✅ Confirm LIVE trade" before any real order is sent.
- **Real balance is the single source of truth.** Dashboard equity = `get_account_balance("USDT").total`. No starting/simulated equity is used for the balance display.

## Adding a new exchange

1. Create `trading/exchanges/<name>.py` subclassing `Exchange` from `base.py`.
2. Implement: `get_price`, `get_klines`, `get_balance`, `place_buy_order`, `place_sell_order`, `get_positions`, `get_fees`, `round_quantity`.
3. In the dashboard, instantiate it and pass `exchange=...` to `bot_module.create_bot(...)`.
4. The registry will hold it automatically; smart routing comes later.

## How to run on DigitalOcean

1. SSH into your droplet
2. `git clone <your-repo> && cd your-repo/trading`
3. `pip install -r requirements.txt`
4. `nohup streamlit run dashboard.py --server.port 80 &` (or use nginx + gunicorn proxy)
5. Or: run inside `screen` or `tmux` so it persists after disconnect

## LIVE trading safety checklist

1. Create your API key at **binance.com → API Management** (Mainnet keys only — testnet keys will be rejected at connect).
2. Enable **Spot Trading** permission. Disable withdrawals.
3. Whitelist your server IP if possible.
4. In the sidebar, paste key + secret and click **Connect to Binance LIVE**.
5. Start small — set Risk per trade % to 0.5–1% and place one manual trade first to verify.
6. Keep emergency stop ready — one click halts all bot activity.

## User preferences

- Dark professional UI (GitHub dark color scheme)
- LIVE Binance Mainnet only — no paper, no testnet, no simulated fills
- Bot must survive page refresh (solved via global thread singleton)
- Manual and bot trades must have distinct markers on chart
- Manual LIVE trades require explicit confirmation before send
- Activity log must explain every decision (buy/sell/skip reasons)

## Gotchas

- Bot/manual trades are disabled until you connect a valid Mainnet API key — there is no paper fallback.
- The bot check interval is in seconds; default 30s = checks every 30 seconds.
- `trading/data/` is auto-created on first run; safe to delete to reset all data.
- Public chart data (24h stats, klines) works without an API key, but no orders can be placed without auth.
