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
- `trading/bot.py` — background bot thread singleton + all trade/activity persistence
- `trading/strategy.py` — EMA Crossover, Price Movement, Momentum (RSI) strategies
- `trading/risk.py` — stop loss, take profit, position sizing, emergency stop
- `trading/binance_client.py` — Binance API wrapper (testnet + live)
- `trading/data/trades.json` — persisted trade history
- `trading/data/activity.json` — persisted activity log
- `trading/.streamlit/config.toml` — dark theme + server config

## Architecture decisions

- Bot runs as a daemon thread using a module-level global (`_bot`) — survives Streamlit script reruns
- All trade/activity data stored in JSON files so state persists across sessions
- `type` field is always stored on every trade; dashboard uses `.get("type", "manual")` defensively
- Manual trades and bot trades have distinct marker colors/icons on the chart
- Stochastic Oscillator (%K/%D) with overbought/oversold dashed lines below the candlestick chart

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
