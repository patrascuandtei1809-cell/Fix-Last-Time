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
- **Manual trades are PROTECTED by default** (see "MANUAL TRADE PROTECTION"
  section below). The bot only manages (`SL/TP`/breakeven/red-exit) trades it
  opened itself (`type=="bot"`) unless the operator turns ON "Allow bot to
  manage manual trades".
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

## MANUAL TRADE PROTECTION + SETTINGS PERSISTENCE (May 29, 2026)

Operator-facing safety + persistence pass. Four changes:

1. **MANUAL TRADE PROTECTION (default ON).**
   - New flag `GlobalRiskSettings.manage_manual_trades` (default **False**).
   - `SymbolWorker` takes `manage_manual_trades` and filters the SL/TP/exit
     loop: `my_open` now keeps a trade only if `manage_manual_trades` OR
     `trade["type"]=="bot"`. With the default OFF, the bot **never** closes
     positions the operator opened manually (no SL/TP, breakeven, or
     red-candle exit). It still *displays* them.
   - Threaded through `bot.create_bot(manage_manual_trades=...)` and passed at
     all three dashboard `create_bot` call sites from the global-risk flag.
   - Sidebar toggle "**Allow bot to manage manual trades**" in the 🌐 Global
     risk expander, with a 🔒/⚠️ caption. Persisted in `settings.json`.

2. **ALL dashboard settings persist** across refresh/restart. Added the chart
   indicator toggles (`show_ema/volume/rsi/macd/stoch/old_trades/sl_tp`) to
   `_init` defaults, `_PERSIST_KEYS`, and `_collect_settings_snapshot()`.
   SL/TP/max-open/cooldown/exposure/timeframe/active-symbols already persisted
   via the `risk`/`global_risk`/top-level snapshot.

3. **No more force-snap of risk numbers.** The old cold-start block that
   re-wrote `max_open_trades`, SL/TP, cooldown, and `max_open_trades_total`
   on every load (which caused "max trades keeps resetting to 1") is REMOVED.
   Only the *strategy-mode* invariants (strategy name, interval, 2s tick,
   threshold, AI on, BTC+ETH+SOL re-instate) are still snapped. Risk numbers
   now load from `settings.json` and win.

4. **Indicator defaults = ALL ON** (RSI/EMA/MACD/Stoch/Volume/Trades/SL-TP).
   **`max_open_trades_total` default = 30** (was 10). Per-symbol "Max open
   trades" slider range widened 1–20 → 1–50. The "Reset to … defaults" button
   now snaps to TP 0.8 / per-symbol cap off / 30 total / cooldown 5s (no
   longer resets to 1).

5. **MOBILE UI.** Added a `@media (max-width:768px)` block: kills horizontal
   overflow (`overflow-x:hidden`, `max-width:100vw`), wraps Streamlit
   horizontal blocks so columns stack vertically, makes buttons full-width,
   and caps plotly charts / dataframes / images to viewport width.

## WEIGHTED SCORING — stop over-filtering, fail closed (May 29, 2026)

Replaced the hard "strategy HOLD → blanket veto" with a **6-factor weighted
decision** that is now the **single canonical** edge engine.

1. **`strategy.weighted_decision(df, ai_signal, ai_confidence, regime)`** scores
   BOTH directions from 6 weighted factors (sum 100): AI confidence 20, RSI 15,
   EMA trend 20 (align 12 + slope 8), MACD 20 (sign 10 + flip/turn 10/4),
   volume spike 15, candle structure 10. Regime-adjusts **both** bull & bear
   (`apply_regime_to_score`) and returns them in the breakdown.
2. **HARD risk veto ONLY for dangerous conditions** — insufficient data,
   non-finite/NaN indicators (`np.isfinite` guard — `float(np.nan)` does NOT
   raise), `atr% > DANGER_ATR_PCT (2.0)`, or `DEAD` regime + flat move
   < `FLAT_PCT (0.005%)`. Everything else gets a directional score; trade when
   `score >= threshold`.
3. **Weighted score IS canonical end-to-end.** `score_market()` is no longer
   used by the worker (kept in `strategy.py`, unused). In `symbol_worker.tick()`
   the candidate `score` = weighted conviction in the **FINAL** signal direction
   (`BUY→bull`, `SELL→bear`) — never ranked by the losing side. Orchestrator
   qualifies on this score; `execute_entry()` sizing mirrors it.
4. **FAIL CLOSED.** If `weighted_decision` is unimportable → `w_veto="weighted
   engine unavailable"`; if it throws → `w_veto="weighted engine error"`. Either
   forces HOLD (score=0) before any execution — a broken engine can never place
   a LIVE order via the AI-confidence path. Orchestrator also requires
   `score > 0`, and `execute_entry()` guards `score>0` as an invariant (blocks
   direct/manual bypass).
5. **Consolidated per-symbol decision log** every tick: `[DECISION] BTC
   ai=BUY/62 score=58 weighted=58(BUY) regime=TREND veto=- → BUY` plus
   `[VETO] … HARD risk veto → HOLD (…)` when a danger gate fires.
6. **Manual-trade protection intact** — `my_open` keeps a trade only if
   `manage_manual_trades OR (type=='bot' AND not manual)`; bot trades tagged
   `manual=False`, all 3 dashboard manual paths tagged `manual=True`.

## FINAL STABLE MODE — fix execution / make the bot ACT (May 29, 2026)

Operator: the bot still wasn't trading (over-filtered). Made entry SIMPLE so it
acts on real signals instead of waiting for perfect conditions. Risk caps kept
safe (SL 0.4% / TP 0.8% / 30 total open — untouched).

1. **SIMPLE ENTRY (OR rule).** Orchestrator (`bot.py`) qualifies a candidate on
   a directional signal AND **(score ≥ score_threshold OR AI confidence ≥
   confidence_floor)** — no longer both. `confidence_floor 50→30`. The sizing
   gate in `symbol_worker.execute_entry()` mirrors this exactly (tiers:
   ≥80→30%, ≥70→20%, ≥60→10%, ≥score_floor→10% "score-min", conf≥30→10%
   "ai-confidence", else block) so a winner can never be **qualified upstream
   then blocked at sizing**. The orchestrator stamps its effective
   `score_threshold` onto the winner so anti-idle (threshold→40) stays
   consistent at sizing.
2. **AI priority.** `ai_decide()` already overrides a strategy HOLD with a
   directional verdict (conf floored ≥40 when it picks a side), so strategy HOLD
   never blocks an AI BUY/SELL.
3. **Removed blockers in `strategy.reversal_signal()`.** Volume gate **1.1×→1.0×**
   and the strict "ALL 5 must align" momentum stacking is gone — now fires on the
   CORE trigger (RSI extreme/turning OR MACD momentum flip) + vol ≥1.0 + a
   directional candle; body/RSI-depth only ADD confidence.
4. **GPT is ADVISORY.** The global analyst veto only fires when GPT returns
   `NO_TRADE` for OUR winner symbol (narrow "reject obvious bad ones").
   Symbol-mismatch / direction-mismatch / low-probability **no longer veto**.
5. **Force activity from cold start.** Worker idle is measured from
   `_last_trade_at or _created_at`, so the 10-min forced-micro-entry fires even
   before the first trade (not only after).
6. **Logging.** AI decision (`[AI]`), score (`[SCAN]`/`[RANK]`), and explicit
   blocked reasons at every gate (per-symbol / global / sizing).

## FIX FINAL — anti-overfilter + full-history chart (May 29, 2026)

Operator reported the bot wasn't trading (over-filtered) and the chart only
showed a few hours. Six changes:

1. **`max_open_trades_total` default = 30** and the dev seed `settings.json`
   bumped 10→30. Risk numbers load from `settings.json` BEFORE the bot starts
   (`risk_manager.settings` is synced from persisted `risk` *before*
   `_maybe_resume_bot()`), so an auto-resumed worker never ticks with stale
   defaults.
2. **Startup log** prints the effective caps:
   `[SETTINGS] max_open_trades (per-symbol)=… | max_open_trades_total (global)=… | manage_manual_trades=…`.
3. **Loosened filtering so the bot actually trades** (in `bot.py`
   `TradingBot.__init__`): `score_threshold_base 60→50`,
   `score_threshold_floor 55→40`, `confidence_floor 60→50`,
   `gpt_prob_floor 55→50`. `strategy.reversal_signal()` volume gate
   **1.5×→1.1×** (conf math uses `max(0, vol_ratio-1.1)`). **GPT prompt
   reworded to DEFAULT-TO-TRADE** — only returns `NO_TRADE` for obviously-bad
   / dead / no-volume markets; it is a light filter, not a gatekeeper.
4. **Full-history chart.** `binance_client.public_klines` and
   `BinanceClient.get_klines` now **paginate backward** (`endTime`, 1000/req
   chunks) so they can return thousands of candles; `_klines_to_df` dedupes by
   `open_time` and sorts oldest→newest. The dashboard chart always fetches
   `CHART_CANDLES = 2000` (auth if connected, else public) and prefers that
   deep set over the ~150-candle bot shared df (which is kept small for signal
   speed); falls back to the bot df only if the deep fetch fails. Zoom-out
   (already capped at 720h) now reveals the full fetched history instead of a
   few hours.
5. **Chart fetch is cached** (`@st.cache_data(ttl=10)`) so the 2000-candle
   paginated download doesn't re-run on every 5s Streamlit rerun. NOTE: cache
   params must NOT be `_`-prefixed — Streamlit treats leading-underscore args
   as *unhashed*, which would collapse all symbols/intervals to one cache key.
6. **Manual-trade protection unchanged** — bot still never sells manual
   positions unless "Allow bot to manage manual trades" is ON.

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

## EARLY REVERSAL SCALPER (May 28, 2026 — predictive entry)

Converted from lagging trend-confirmation to **early-reversal** scalping.
Goal: enter BEFORE the move, accept more small losses to catch early turns.

- **New signal `reversal_signal()` in `trading/strategy.py`** (registered as
  strategy name `"Reversal Scalper"` in `get_signal()`). Fires on ANY of:
  - RSI <35 (BUY oversold) / >65 (SELL overbought)
  - MACD hist sign-flip vs previous bar (momentum shift)
  - Volume > 1.5× 20-bar average (BUY if green candle, SELL if red)
  - Wick rejection: lower_wick ≥ 2× body AND > 40% of bar → BUY;
    symmetric upper_wick → SELL
  Confidence 50 + 10 per extra trigger (+10 if volume spike), cap 90.

- **`score_market()` reweighted (sum = 100):**
  - reversal  40 — RSI extreme (0-15) + MACD sign-flip (0-15) + wick (0-10)
  - volume    25 — vol_ratio ≥1.0 ramps to 25 pts at 2.5×
  - momentum  20 — MACD hist direction matches the trade
  - trend     15 — EMA9/EMA21 alignment (minor — we catch turns, not trends)
  Regime adjustment kept (DEAD cap 30, RANGE −10, TREND +5, VOLATILE 0).

- **GPT role rewritten** (`gpt_advisor.analyze_global` prompt). It is now an
  EARLY REVERSAL FILTER: probability = confidence in a short-term reversal,
  NOT a trend prediction. Trade only if probability ≥ 65. Return shape
  unchanged so orchestrator gating is unchanged.

- **Strict EARLY-ONLY entry gates (v3 — quality filter):**
  - HARD RULE: skip if last candle move > **0.3%** (move already happened).
  - **BUY = ALL of:** RSI<40 AND rising · MACD hist flipping up · vol≥**1.5×**avg
    · **strong green body (≥50% of range)** · price ≤ EMA9 (not extended)
  - **SELL = ALL of:** RSI>60 AND falling · MACD hist flipping down · vol≥**1.5×**avg
    · **strong red body (≥50% of range)** · price ≥ EMA9 (stretched)
  - Momentum-strength filter (vol≥1.5× + body strength) → fewer, higher-quality
    trades. Failed-gate diagnostic logged on every HOLD.

- **Risk/reward ~2:1:** SL 0.4% / TP **0.8%** (was 0.6%) — let winners run.

- **Faster execution / higher concurrency (AGGRESSIVE):**
  - Per-symbol cooldown **5 s**, global throttle **5 s** between any 2 entries.
  - `max_open_trades_total` = **10** (only balance + cooldown + global cap limit).
  - **Per-symbol cap removed** (`max_per_symbol = max_open_trades = 99`) — global
    cap is the only ceiling.
  - GPT prob floor 65 → **55** (filter, not predictor; reject only clearly bad).
  - Score threshold stays 60, 2 s loop unchanged.

- **Aggressive sizing tiers** (smaller per-trade since we allow up to 20
  concurrent positions): 60-69 → 10%, 70-79 → 20%, ≥80 → 30% of free USDT.
  VOLATILE ×0.75, ceiling free×0.75 (25% reserve), $10 floor.

- **Default strategy on cold start = `"Reversal Scalper"`** (dashboard
  force-snap). Sidebar still hides the dropdown.

## SMART AI SCALPING BOT (May 28, 2026 — surgical upgrade)

Quality-first layer on top of SMART ACTIVE SCALPER. Same plumbing, tighter
thresholds, GPT promoted to a global analyst, and a new market-regime gate.

- **New file `trading/market_regime.py`**: `classify_regime(df)` returns one
  of `DEAD / RANGE / TREND / VOLATILE` from ATR%, EMA9/21 separation,
  EMA slope, and rolling volume. `apply_regime_to_score(score, regime)`
  caps DEAD at 30, subtracts 10 in RANGE, adds 5 in TREND, leaves VOLATILE
  untouched (but downsized — see below).
- **Thresholds (Nov 2026 retune — all 65 → 60 to trade more often while
  staying smart)**: `score_threshold_base=60`, `confidence_floor=60`,
  `gpt_prob_floor=65` (GPT = filter, not predictor — reject low-confidence
  setups). `global_throttle_sec=10`. Anti-idle floor `55`
  (was 60) — a truly motionless market still HOLDs.
- **Per-symbol cooldown 30 → 15s**. TP 0.6%, SL 0.4%, BE arm +0.20%,
  **max 3 open trades total** (one per active symbol — BTC/ETH/SOL),
  1 per symbol.
- **GPT = GLOBAL ANALYST** (`gpt_advisor.analyze_global`). Every cycle with
  ≥1 qualified candidate, the orchestrator sends a structured payload for
  ALL three symbols (signal/regime/score/confidence/price/breakdown) and
  GPT returns `{action:TRADE|NO_TRADE, symbol, direction, probability,
  confidence, risk_level, reason}`. Throttled+cached 10s. Trade only if
  action=TRADE AND symbol==score winner AND direction==winner.signal AND
  probability ≥ 65. On transient outage (None) → fall back to pure score
  winner. `rank_opportunities()` kept as a backward-compat shim.
- **Absolute score-tiered sizing** in `symbol_worker.execute_entry()`
  (decoupled from the sidebar slider — Nov 2026 retune, flatter & larger):
  - score 60–69  → 30% of free USDT (standard)
  - score 70–79  → 40% of free USDT (strong)
  - score ≥ 80   → 50% of free USDT (excellent)
  - VOLATILE regime → ×0.75 (downsize when risk is high)
  - Floor $10 (Binance min notional), ceiling free × 0.75 (25% reserve).
- **Per-symbol regime** surfaced on the dashboard card (color-coded:
  TREND green / RANGE mint / VOLATILE yellow / DEAD red) and logged on
  every scan: `[SCAN] BTC score=72 signal=BUY conf=68 regime=TREND atr%=0.18`.

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
