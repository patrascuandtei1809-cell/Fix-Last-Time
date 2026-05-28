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

## ACTIVE SCALPER MODE (May 2026 — full reset)

The bot has a **single hardcoded strategy** called `Active Scalper`. All previous
modes (Conservative / Balanced / Aggressive / Ultra / Pro) and their preset
buttons have been removed. There is no Aggressiveness dropdown. AI is always
on, advisory only — it confirms strategy BUY/SELL but **never blocks** them.

Hardcoded spec:
- **2s tick**, **1m candles**, threshold `0.01%` (anti-idle auto-lowers).
- Triggers BUY/SELL on ANY of: price ±0.01%, EMA9 3-bar slope, bounce
  (≥2 reds → green), or momentum flip. No EMA hard veto, no green-candle
  requirement, no "wait for perfect trend".
- **Size**: dynamic — `free_usdt * dynamic_size_pct/100` (default 40%),
  capped at `free_usdt * 0.75` (always leaves 25% buffer), floored at $10
  (Binance min notional). Set via sidebar "% of free USDT per trade" slider.
- **Caps**: 1 open trade per symbol, 3 total open across BTC/ETH/SOL.
  Concentration cap REMOVED (`max_exposure_per_symbol_pct = 100`). SL=0.4%, TP=0.5%.
- **Manages manual trades** the same as bot trades — SL/TP filter is by
  symbol only, not `type=="bot"`.
- **Post-entry**: breakeven SL armed at +0.20%, exits on 2 consecutive red
  candles (constants `AS_BE_ARM_PCT` / `AS_MAX_RED_AFTER_ENTRY` in
  `symbol_worker.py`).
- **Anti-idle**: if no trade for ≥5 min → threshold ×0.5; ≥10 min →
  threshold ×0.25 + forced micro-entry attempt. Threshold restores on next
  trade.
- **Auto-start**: bot launches automatically on every cold start as long as
  LIVE creds are present and the operator has not pressed ⏹ Stop
  (`_user_stopped_bot` session flag).
- **AI behavior** (`ai_engine.ai_decide`): the only HOLDs it ever emits are
  data shortage, balance < $10 (BUY only), max_open reached, or a truly
  motionless market (last candle move below `flat_pct`). Everything else
  gets a directional verdict.

Target throughput: 50–150 trades/day across all three symbols.

## SMART PRIORITY SCALPER (May 2026 addition)

Layer on top of ACTIVE SCALPER — keeps the speed but adds cross-symbol
selection so the bot stops firing simultaneously on all three coins.

- **Every cycle**, each worker computes a `score 0–100` (`strategy.score_market`)
  combining: trend 25 (EMA9 vs EMA21 + slope), momentum 20 (MACD histogram),
  volume 20 (vs 20-bar avg), candle body 15, RSI quality 10, volatility quality 10.
- **MACD added** to `get_indicators()` — needed for momentum weight.
- **Workers no longer place orders themselves** when an orchestrator candidate
  hook is wired. They publish `{symbol, signal, score, confidence, breakdown}`
  to the orchestrator via `_on_candidate` and return.
- **Orchestrator picks ONE winner per cycle:** non-HOLD signal AND
  `score >= score_threshold` (base 60), highest score wins. Ties within 5 pts
  may be resolved by `gpt_advisor.rank_opportunities()` (one short GPT call,
  cached 15s, throttled).
- **Global throttle:** ≥ 30 s between any two new trades, regardless of symbol.
- **Max 2 open trades total** (was 3) — `risk.GlobalRiskSettings.max_open_trades_total`.
- **Anti-idle lowers the THRESHOLD, never bypasses it:** 5 min idle → 50, 10 min
  idle → 40, floor 30. A truly flat market still results in HOLD across all
  symbols rather than forcing a low-quality entry.
- Rank line per cycle: `[RANK] BTC=74(B) ETH=61(B) SOL=48(H) → WINNER=BTCUSDT score=74`
  or `→ HOLD (all < threshold 60)` / `→ THROTTLED (18s left)` / `→ SKIP (max_open 2/2)`.
- Dashboard per-symbol card now shows `SCORE` row (colored by quality tier).
- `execute_entry()` is the new public method that runs the gates+sizing+order+record
  block. Orchestrator calls it on the winner ONLY.

## SMART ACTIVE SCALPER tuning (May 28, 2026)

Quality-frequency refinement on top of SMART PRIORITY SCALPER — trade often
but only when there's an edge.

- **Thresholds**: `score_threshold_base = 55`, `confidence_floor = 55`,
  `gpt_prob_floor = 55`, `global_throttle_sec = 20` (was 30). Anti-idle floor
  unchanged at 30.
- **GPT = HARD edge filter** (was tiebreak only). Whenever ≥1 setup qualifies
  AND GPT is enabled, `rank_opportunities()` is called every cycle (cached 15s
  inside the advisor). GPT must return BOTH its pick AND
  `probability_next_move 0-100`. Trade ONLY if GPT's pick matches our score
  winner AND `probability_next_move >= 55`. Otherwise HOLD with reason
  `GPT veto: …`. If GPT is throttled/cached/disabled, fall back to pure-score
  winner (don't block on transient GPT outage).
- **Score-tiered sizing** in `symbol_worker.execute_entry()`:
  - score 55–64  → 50% × sidebar `% of free USDT` slider (conservative)
  - score 65–74  → 75% × slider (standard)
  - score ≥ 75   → 100% × slider (full conviction)
  Logged per entry: `[BOT] BTCUSDT size tier=mid score=68 base=40% × 0.75 → 30.0%`.
- **Max 2 open trades total** unchanged. With max_open=2 + score winner per
  cycle + 20s throttle, the "if 3 valid → trade only TOP 2" rule emerges
  naturally over consecutive cycles.

## Multi-symbol bot UX (May 2026)

- Default `active_symbols` = **BTC + ETH + SOL** — the orchestrator scans all three each tick.
- Per worker tick we print one structured line: `[BOT] <SYM> signal=<X> reason=<…>` and, when a gate blocks, `[BOT] <SYM> blocked reason=<…> (per-symbol|global|balance gate)`.
- Dashboard renders a **per-symbol overview** strip under the main bot status bar: signal · last check · last order · block reason. Always visible while bot is ON.
- When bot is ON but no successful order in the last 5 min, a yellow **"⏳ BOT ACTIVE BUT WAITING › …"** banner explains *why* (top 3 block reasons across active symbols).
- `EMA Crossover` is strict (4 filters must align) — sidebar warns the user and points them to `Price Movement` as **scalping mode** (lower threshold = more trades). Threshold slider already exposed.
- Balance/PnL are LIVE Binance Spot wallet only. After BUY: USDT free decreases, base asset free increases. After SELL: USDT free increases (or decreases at a loss). Bot never holds funds.

## Refresh-proof bot (May 2026)

- **API keys persist** in `data/.binance_creds.json` (chmod 600, atomic write,
  whitespace stripped on load). On cold start `_init()` auto-loads + reconnects
  the LIVE client unless the user manually disconnected.
- **Bot auto-resumes after server restart.** The Start/Stop buttons set
  `st.session_state.bot_was_running`; the bottom-of-script auto-save persists
  it (single source of truth — no race with manual saves). On the next cold
  start, `_maybe_resume_bot()` rebuilds workers from persisted
  `active_symbols` + per-symbol risk and calls `b.start()`. Logs
  `[BOT] Auto-resumed bot after server restart — symbols=…`.
- **Auth-keys guard in `SymbolWorker.tick()`.** If `exchange.client is None`,
  the worker sets `block_reason="Waiting for API keys"` and returns — bot stays
  alive across ticks. Log line prints only on state transition (no spam).
- **Manual Connect** strips whitespace from key/secret before building
  `BinanceClient`. Secrets never appear in logs or UI (only key prefix).

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
