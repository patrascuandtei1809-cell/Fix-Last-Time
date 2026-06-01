# AlphaTrade — Project Discovery Report

> **What this is:** A LIVE-only Binance **Mainnet** spot scalping bot with a Streamlit
> dashboard. It trades **BTC, ETH, SOL** simultaneously. **Every BUY/SELL is a real
> order on `api.binance.com`.** There is no paper mode, no testnet, no simulated fills.
>
> **Environment note:** On Replit, `api.binance.com` is geo-blocked (HTTP 451), so the
> bot cannot place real orders here — Replit is **development only**. Production runs on
> a **DigitalOcean droplet**.
>
> **Honest health note:** A faithful backtest of the current strategy shows **no positive
> edge** (~−0.24%/trade after costs, ~5% win rate). This report documents how the system
> *works*, not a claim that it is *profitable*. Trading more often does not fix a missing edge.

Generated: 2026-06-01

---

## 1. PROJECT STRUCTURE

### File tree (`trading/`)

```
trading/
├── dashboard.py            # Main Streamlit app — UI, controls, chart, tables, logs (4124 lines)
├── bot.py                  # Multi-symbol orchestrator: workers dict, daemon thread, persistence (1034)
├── symbol_worker.py        # Per-symbol tick logic: signal → gates → real order; SL/TP/BE exits (868)
├── strategy.py             # Indicators, Active Scalper + Reversal signals, weighted_decision engine (722)
├── risk.py                 # SymbolRiskSettings/RiskManager + GlobalRiskSettings/GlobalRiskManager (225)
├── diagnostics.py          # Decision journal, frequency stats, preflight, ghost reconcile, report (562)
├── backtest.py             # Honest offline backtester over CSV klines (488)
├── ai_engine.py            # Local "AI" advisor — ai_decide() refines/overrides strategy signal (252)
├── gpt_advisor.py          # GPT global analyst (OpenAI) — analyze_global() advisory veto (346)
├── market_regime.py        # classify_regime() → DEAD/RANGE/TREND/VOLATILE + score adjust (107)
├── binance_client.py       # LIVE Binance Mainnet API wrapper around python-binance (288)
├── telegram_notifier.py    # Optional Telegram trade/alert notifications (149)
├── secrets_store.py        # Local API-key persistence helper (chmod 600) (83)
├── requirements.txt        # streamlit, python-binance, plotly, pandas, numpy, requests, dateutil
├── exchanges/
│   ├── __init__.py         # Package exports (13)
│   ├── base.py             # Exchange ABC — the interface all exchanges implement (65)
│   ├── binance.py          # BinanceExchange — concrete LIVE implementation (173)
│   └── registry.py         # Registry stub + best_exchange_for() for future smart routing (44)
├── .streamlit/
│   └── config.toml         # Dark theme + server config
└── data/
    ├── settings.json       # Persisted user/bot settings (active_symbols, risk, global_risk)
    ├── activity.json       # Persisted activity log (shared across symbols, capped ~500)
    ├── .binance_creds.json # API key + secret (mode 0600)
    ├── trades/             # Per-symbol trade journals: binance_<symbol>.json (currently empty)
    └── backtest/           # Cached CSV klines for offline backtests (BTC/ETH/SOL, 7/14/30d)
```

### Purpose of every file

| File | Purpose |
|------|---------|
| `dashboard.py` | The entire Streamlit UI: header/ticker, market-overview strip, "WHY NO TRADE?" diagnostics panel, candlestick chart with indicators, open-positions cards, manual trade buttons, activity log, and all sidebar controls. Owns the connect/start/stop flow. |
| `bot.py` | The orchestrator. Holds a module-level `_bot` singleton that survives Streamlit reruns, a dict of `SymbolWorker`s, and **one daemon thread** that loops every tick. Picks one winner per cycle, runs global gates, and owns **all trade/activity/settings persistence**. |
| `symbol_worker.py` | One instance per symbol. Each tick: manages open positions (SL/TP/breakeven/red-exit), computes the signal + weighted score, and publishes a candidate. `execute_entry()` runs the final gates, sizing, and the real order. |
| `strategy.py` | All market math: `get_indicators()`, the `active_scalper_signal`/`reversal_signal` entry signals, and the canonical `weighted_decision()` 6-factor scorer. (`score_market()` also lives here but is now unused — see §10.) |
| `risk.py` | Two-tier risk. `RiskManager` (per-symbol: SL/TP, cooldown, direction lock, session limit). `GlobalRiskManager` (account-wide: total exposure, concentration %, global open cap, daily-loss halt, emergency stop). |
| `diagnostics.py` | Observability + safe maintenance only (no strategy logic). Decision journal, top-10 block reasons, trade-frequency stats, Binance preflight checks, and ghost-trade reconciliation. |
| `backtest.py` | Replays historical CSV klines through the strategy to estimate edge honestly. |
| `ai_engine.py` | Local rule-based "AI" advisor. `ai_decide()` confirms or overrides the strategy signal; advisory, rarely blocks. |
| `gpt_advisor.py` | Optional OpenAI call. `analyze_global()` sends all symbols' state and returns a TRADE/NO_TRADE verdict used as an advisory veto on the winner. |
| `market_regime.py` | Classifies the tape into DEAD/RANGE/TREND/VOLATILE and adjusts the score accordingly. |
| `binance_client.py` | Authenticated LIVE Mainnet wrapper: price, klines (paginated), balances, symbol filters, market orders, fill extraction. |
| `exchanges/base.py` | Abstract `Exchange` interface so other exchanges can be added later. |
| `exchanges/binance.py` | The only concrete implementation today; wraps `BinanceClient`. |
| `exchanges/registry.py` | Holds exchanges and exposes a `best_exchange_for()` stub for future smart routing. |
| `telegram_notifier.py` | Optional push notifications. |
| `secrets_store.py` | Reads/writes `data/.binance_creds.json` securely. |

---

## 2. TRADING FLOW (signal → order)

A single daemon thread in `bot.py` (`TradingBot._inner_loop`) runs every **2s** (`check_every`).

```
TradingBot._inner_loop()  [bot.py]   ── every 2s
  │
  ├─ _refresh_global_state() → all open trades + today's PnL
  ├─ (every 300s) diagnostics.reconcile_ghost_trades()   # safe maintenance
  │
  ├─ Phase A — tick every worker:  SymbolWorker.tick()   [symbol_worker.py]
  │     1. exchange.get_price(symbol)            → auth gate if no client
  │     2. manage OPEN positions: SL / TP / breakeven(+0.20%) / 2-red-candle exit
  │     3. exchange.get_klines(symbol, 1m, 150)  → strategy.get_indicators()
  │     4. market_regime.classify_regime(df)
  │     5. strategy.get_signal()  (active_scalper_signal / reversal_signal)
  │     6. ai_engine.ai_decide()  → may override a strategy HOLD with a direction
  │     7. strategy.weighted_decision(df, ai_signal, ai_conf, regime)
  │           → canonical score 0–100 in the FINAL signal direction
  │     8. publish candidate via _on_candidate({symbol,signal,score,confidence,regime,breakdown})
  │
  ├─ Phase B — pick winner  [bot.py]
  │     • qualify: score > 0 AND (score ≥ score_threshold OR confidence ≥ confidence_floor)
  │     • rank by score, then confidence → top candidate
  │     • gpt_advisor.analyze_global() → may set winner=None (NO_TRADE veto, advisory)
  │
  ├─ Phase C — execute winner  [symbol_worker.execute_entry()]
  │     • global throttle (≥5s since last order?) + global cap (≤30 open?)
  │     • RiskManager.can_open_trade() + GlobalRiskManager.check_global()
  │     • score-tiered sizing → invest amount (floored $10, capped 75% free)
  │     • exchange.place_buy_order()/place_sell_order()  → REAL Binance MARKET order
  │     • extract_fill() → real price/qty → bot.add_trade()  (persist)
  │
  └─ diagnostics.record_cycle(...)  # journal WHY each symbol did/didn't trade
```

**Files involved:** `bot.py` (orchestration), `symbol_worker.py` (tick + execution),
`strategy.py` (signal + score), `ai_engine.py` + `gpt_advisor.py` (advisory),
`market_regime.py` (regime), `risk.py` (gates), `exchanges/binance.py` + `binance_client.py`
(orders), `diagnostics.py` (journal/reconcile).

---

## 3. STRATEGY

### Indicators (`strategy.get_indicators`)

| Indicator | Period(s) |
|-----------|-----------|
| EMA | 9, 21, 50 |
| RSI | 14 |
| Stochastic | %K=14, %D=3 |
| ATR | 14 |
| MACD | fast 12 / slow 26 / signal 9 |
| Volume average | 20-bar |

### Entry conditions

**Active Scalper (`active_scalper_signal`)** triggers BUY/SELL on **ANY** of:
- Price change `abs(pct) ≥ threshold` (base **0.01%**, anti-idle can lower it).
- EMA9 3-bar slope `abs(slope) ≥ 0.005%`.
- Bounce: ≥2 of last 3 candles red + current candle green (BUY); mirror for SELL.
- Momentum flip: previous bar down, current up (BUY); mirror for SELL.

**Reversal Scalper (`reversal_signal`, current default in settings)** — early-reversal:
- RSI extreme/turning (oversold <35 BUY / overbought >65 SELL), OR MACD histogram sign-flip,
  OR volume ≥1.0× avg with a directional candle, OR wick rejection.
- **Hard rule:** skip if the last candle already moved **> 0.3%** (`MAX_LAST_CANDLE_MOVE_PCT`) — don't chase.

### Exit conditions (managed in `symbol_worker.tick`)
- **Stop loss** hit (default **0.4%**).
- **Take profit** hit (default **0.8%** in code; `settings.json` currently **0.6%** — see §9/§10).
- **Breakeven**: once PnL reaches **+0.20%** (`AS_BE_ARM_PCT`), the stop is moved to entry price.
- **2 consecutive red candles** after entry (`AS_MAX_RED_AFTER_ENTRY = 2`) → exit.
- *(An EMA9-break exit existed but is REMOVED/commented — see §10.)*

### Scoring system — `weighted_decision()` (canonical, sums to 100)

| Factor | Max pts | How it's scored |
|--------|--------:|-----------------|
| AI confidence | 20 | `ai_confidence / 5` |
| RSI | 15 | BUY: <30→15, <40→9, <45→4; SELL mirror (>70/>60/>55) |
| EMA trend | 20 | alignment 12 (EMA9 vs EMA21) + slope up to 8 (`slope_pct/0.05`) |
| MACD momentum | 20 | sign 10 + fresh flip 10 (or turning-toward 4) |
| Volume spike | 15 | `min(15, (vol_ratio−1.0)/1.5 × 15)`, credited to candle direction |
| Candle structure | 10 | body fraction up to 6 + wick rejection up to 4 |

Both bull and bear are scored each bar; the regime adjustment is applied to both, and the
**final signal's** direction score is what the orchestrator ranks on.

### Market regime (`market_regime.classify_regime`, 25 bars)

| Regime | Trigger | Score effect |
|--------|---------|--------------|
| DEAD | `atr% < 0.04` AND `vol_ratio < 0.55` | hard cap **30** (effectively can't trade) |
| VOLATILE | `atr% ≥ 0.18` OR (`vol_ratio ≥ 1.6` AND `body% ≥ 0.10`) | no score change, **size ×0.75** |
| TREND | `ema_spread% ≥ 0.05` AND `abs(slope_bars) ≥ 3` | **+5** |
| RANGE | fallback | **−10** |

### Thresholds (defaults in `bot.py`)
- `score_threshold_base = 50`, `score_threshold_floor = 40`, `confidence_floor = 30`.
- Hard vetoes in `weighted_decision`: `DANGER_ATR_PCT = 2.0%`, `FLAT_PCT = 0.005%`, plus NaN/insufficient-data.
- **Anti-idle:** 5 min no trade → threshold −10; 10 min → −20 (floor 40) + forced micro-entry attempt.

---

## 4. RISK MANAGEMENT

Two tiers: per-symbol (`RiskManager`) and account-wide (`GlobalRiskManager`).

| Control | Default | Where |
|---------|---------|-------|
| Stop loss | **0.4%** | `SymbolRiskSettings.stop_loss_pct` |
| Take profit | **0.8%** (code) / 0.6% (settings.json) | `SymbolRiskSettings.take_profit_pct` |
| Breakeven arm | **+0.20%** → stop to entry | `AS_BE_ARM_PCT` (symbol_worker) |
| Red-candle exit | **2** consecutive reds | `AS_MAX_RED_AFTER_ENTRY` |
| Per-symbol cooldown | **5s** | `cooldown_seconds` |
| Direction lock | can't repeat same direction back-to-back | `RiskManager.can_open_trade` |
| Per-symbol max open | **99** (effectively off — global cap rules) | `max_open_trades` / `max_per_symbol` |
| Global max open | **30** | `GlobalRiskSettings.max_open_trades_total` |
| Global throttle | **≥5s** between any two orders | `global_throttle_sec` |
| Max total exposure | **$300** (settings) / $1000 default | `max_total_exposure_usdt` |
| Concentration cap | **100%** (off) | `max_exposure_per_symbol_pct` |
| Daily-loss halt | **5%** | `max_daily_loss_pct` |
| Emergency stop | manual kill switch | `emergency_stop` |

### Position sizing (`execute_entry`)
Base = `free_usdt × dynamic_size_pct/100` (slider default **40%**), then score-tiered:

| Score | Multiplier |
|-------|-----------|
| ≥ 80 | ×1.50 (excellent) |
| ≥ 70 | ×1.25 (strong) |
| ≥ 60 | ×1.00 (standard) |
| ≥ score_floor (50) | ×1.00 (score-min) |
| AI confidence ≥ 30 | ×1.00 (ai-confidence path) |

- **VOLATILE regime** → ×0.75.
- **Ceiling**: capped at `free_usdt × 0.75` (always leaves a 25% reserve).
- **Floor**: `MIN_NOTIONAL = $10` — sizes below $10 are floored **up** to $10 (so small accounts can still trade), provided the 25% reserve allows it.

### Gate order
1. `GlobalRiskManager.check_global` — emergency stop, daily-loss, global cap, exposure, concentration.
2. `RiskManager.can_open_trade` — per-symbol emergency stop, cooldown, direction lock, session limit.
3. Balance gate in `execute_entry` — `invested ≤ free_usdt` and within optional bot budget.

---

## 5. BINANCE INTEGRATION

### API methods (`binance_client.py` wrapped by `exchanges/binance.py`)
- `test_connection` → `ping()` + `get_server_time()`
- `get_price` → public `/ticker/price` or `get_symbol_ticker`
- `get_klines` → OHLCV; **paginates backward** via `endTime` when `limit > 1000`
- `get_all_balances` → `get_account()` → `{asset: {free, locked, total}}` (non-zero only)
- `get_account_balance` → single asset `{asset, free, locked, total}`
- `get_symbol_info`, `get_step_size` (LOT_SIZE filter), `get_min_notional` (NOTIONAL filter)
- `get_symbol_filters` → `{step_size, min_notional, min_qty}`
- `round_quantity` → floors qty to stepSize precision
- `place_buy_order` / `place_sell_order` → `create_order` **MARKET** orders
- `extract_fill` → parses real executed price/qty from the order response

### Order PLACEMENT flow
Winner → throttle/cap gates → risk gates → sizing → `place_buy_order` (qty via `round_quantity`)
→ `extract_fill` for the real price/qty → `bot.add_trade()` persists the OPEN record.

### Order CLOSING flow
Each tick the worker checks SL/TP/breakeven/red-exit on its open positions. On a hit,
`_close_position` calls `place_sell_order` (or buy-to-cover for shorts) → `bot.close_trade()`
writes exit price, reason, and gross P&L.

### Reconciliation (`diagnostics.reconcile_ghost_trades`, every 300s)
Loads local OPEN BUY trades, fetches real Binance balances, derives base asset.
- If Binance total ≤ dust (`min_qty`, or `1e-8`) → **ghost**: close at **entry price** (P&L = 0),
  reason `"RECONCILED — ghost trade: no <asset> balance on Binance…"`.
- If balance present but < 95% of recorded → **mismatch**: logged, **left open** (never auto-closed).

---

## 6. DATABASE / STORAGE

All persistence is flat JSON files under `trading/data/` (no SQL database).

| File | Contents |
|------|----------|
| `data/settings.json` | All bot/user settings (see §9). |
| `data/activity.json` | Activity log array (`time`, `level`, `message`), capped ~500 entries. |
| `data/trades/binance_<symbol>.json` | Per-symbol trade journal (array). Currently empty (no live trades yet). |
| `data/.binance_creds.json` | API key + secret, file mode 0600. |
| `data/backtest/*.csv` | Cached historical klines for offline backtesting. |

### OPEN trade record (`add_trade`)
```json
{
  "id": "a1b2c3d4",            // 8-char UUID
  "coin": "BTCUSDT",
  "exchange": "binance",
  "side": "BUY",               // or SELL
  "entry_price": 60000.0,       // real fill price
  "quantity": 0.00016,          // real filled qty
  "invested": 9.6,              // qty * price (quote USDT)
  "status": "open",
  "type": "bot",               // "bot" or "manual"
  "open_time": "2026-06-01T18:22:00"
}
```

### CLOSED trade record (`close_trade`) — open fields **plus**:
```json
{
  "status": "closed",
  "exit_price": 60300.0,
  "close_time": "2026-06-01T18:25:00",
  "close_reason": "Take profit hit",
  "profit_loss": 0.048,         // GROSS USDT (no fees — see Known Issues)
  "profit_loss_pct": 0.5
}
```

**Persistence helpers:** `load_trades(symbol, exchange)` globs `data/trades/*.json` and filters;
`get_open_trades(symbol)` keeps `status=="open"`; `close_trade(id, exit_price, reason)` scans all
files to find the id, computes P&L, and rewrites the file.

---

## 7. DASHBOARD

Single Streamlit page (`dashboard.py`), top to bottom:

| Section | Metrics shown | Data source |
|---------|---------------|-------------|
| Header / ticker | logo, live price + 24h change, status pills (LIVE/MAINNET/CONNECTED/BOT ON) | shared state in `bot.py` (BinanceExchange) |
| Market overview strip | per-symbol price, 24h change, volume | public ticker/klines |
| Per-symbol cards | signal, score, confidence, regime, last check/order, block reason | `bot.get_all_symbol_state()` + diagnostics |
| **🚦 WHY NO TRADE?** panel | trades today, avg/day, open now, mins since last; current reason per symbol; **top-10 block reasons**; Verify Binance / Reconcile / Full-report buttons | `diagnostics` decision journal |
| Candlestick chart | OHLC + EMA9/21/50, RSI(14), MACD histogram (toggleable); manual vs bot trade markers | `public_klines` (up to 2000 candles, cached 10s) |
| "WHY THIS SIGNAL?" box | AI engine reasoning bullets | `ai_engine` |
| Open positions | per-trade P&L, entry, BE-armed status | `get_open_trades` |
| Manual trading | Force BUY/SELL, Close All, USDT amount (with explicit confirm) | exchange |
| Global risk snapshot | total exposure vs cap | `GlobalRiskManager` |
| Activity log | INFO/SIGNAL/ORDER/AI/ERROR lines | `activity.json` |
| Sidebar | API connect, active symbols (max 3) + view selector, check-interval, threshold, dynamic size %, daily-loss, emergency stop, indicator toggles | `settings.json` |

Equity displayed = **real Binance USDT balance** (`get_account_balance("USDT").total`) — no simulated equity.

---

## 8. BLOCKERS — every reason a trade can be prevented

1. **No API keys** — `SymbolWorker.tick`: `Waiting for API keys` (no `exchange.client`).
2. **Strategy HOLD** — not enough candles; no directional trigger; `LATE — last bar moved >0.3%`.
3. **Weighted-engine hard veto** — insufficient data; NaN/non-finite indicator; `atr% > 2.0` blowout; `flat/dead tape < 0.005%`. (Engine import/throw → fail-closed HOLD.)
4. **Below threshold** — `score < score_threshold` AND `confidence < confidence_floor`.
5. **Not top-ranked** — qualified but another symbol scored higher this cycle (one winner per cycle).
6. **Per-symbol gates** (`RiskManager.can_open_trade`) — per-symbol emergency stop; cooldown active; direction lock (need opposite side); session trade limit; per-symbol cap.
7. **Global gates** (`GlobalRiskManager.check_global`) — GLOBAL emergency stop; daily-loss halt (≤ −5%); global open-trade cap (30); spending/exposure limit ($300/$1000); concentration cap.
8. **Global throttle** — < 5s since the last order anywhere.
9. **GPT veto** — `analyze_global` returns `NO_TRADE` for the winner (advisory).
10. **AI-engine HOLD** — free USDT < $10 (BUY); symbol position cap (n/3); truly motionless market (< 0.005%).
11. **Min-notional / balance** — would be < $10 after the 25% reserve ceiling; insufficient balance.
12. **Order rejected by Binance** — LOT_SIZE/NOTIONAL filter failure, insufficient balance, API error (incl. **451 geo-block on Replit**).

The dashboard's **WHY NO TRADE?** panel and `diagnostics.build_report()` surface the live,
categorized counts of exactly which of these is firing.

---

## 9. CURRENT CONFIGURATION (`data/settings.json`)

```
Active symbols ........ BTCUSDT, ETHUSDT, SOLUSDT
Strategy .............. Reversal Scalper
Interval / tick ....... 1m candles / check_every = 2s
Price threshold ....... 0.01%
bot_was_running ....... false
Manual amount ......... $100
Refresh ............... 5s
Telegram .............. disabled

Indicator toggles ..... EMA, Volume, RSI, MACD, Stoch, Old trades, SL/TP → all ON

Global risk:
  max_total_exposure_usdt ...... 300.0
  max_exposure_per_symbol_pct .. 100   (concentration cap OFF)
  max_open_trades_total ........ 30
  max_daily_loss_pct ........... 5.0
  manage_manual_trades ......... false (bot does NOT touch manual trades)

Per-symbol risk (defaults, per_symbol_risk overrides = {}):
  invest_per_trade ............. 20.0
  max_trade_usdt ............... 20.0
  stop_loss_pct ................ 0.4
  take_profit_pct .............. 0.6     ← note: code default is 0.8 (mismatch, see §10)
  max_open_trades .............. 30
  cooldown_seconds ............. 5
  max_daily_loss_pct ........... 5.0
  max_trades_per_session ....... 0 (unlimited)
  emergency_stop ............... false
```

> `initial_balance: 1000` exists in settings but is **not** used for the equity display
> (real Binance balance is the source of truth).

---

## 10. KNOWN ISSUES

### Dead code
- **`strategy.score_market()`** — ~200 lines, fully superseded by `weighted_decision()`. The
  orchestrator never calls it. Safe to delete after confirming no import references.
- **EMA9-break exit** in `symbol_worker.tick` is explicitly REMOVED/commented, but nearby
  indicator math that fed it still runs.

### Inconsistent / duplicated logic
- **TP mismatch:** code default `take_profit_pct = 0.8` vs `settings.json = 0.6`. Persisted
  settings win at runtime, so the *effective* TP is **0.6%**. The replit.md text (which cites 0.8%
  in places and 0.6% in others) is internally inconsistent — pick one and align all three.
- **Indicator recomputation:** `get_indicators(df)` runs in `SymbolWorker`, then again inside
  `weighted_decision` if columns are missing — wasted CPU on the hot 2s path.
- **Regime logic** is applied both in `SymbolWorker`/`backtest` and partly inside
  `weighted_decision`.

### Potential bugs / fragilities
- **NaN → fake high score:** `weighted_decision` has an explicit `np.isfinite` guard because a
  NaN could otherwise clamp into a high score and fire a LIVE order. Keep this guard; never
  rely on `float(np.nan)` raising (it doesn't).
- **Cooldown vs global-throttle desync:** a worker's per-symbol cooldown can be satisfied while
  the global throttle blocks it, so a ready signal is effectively dropped until the next tick.
  Usually harmless for scalping cadence but worth noting.
- **Anti-idle resets on restart:** the idle timer that lowers the threshold / forces a micro-entry
  resets to zero on every process restart, so a frequently-restarted bot never reaches forced-entry mode.
- **All P&L is GROSS:** `profit_loss` is a raw price difference. Binance fees (~0.1% per side,
  ~0.2% round-trip) are **never recorded**, so every P&L figure on the dashboard overstates net.
  With TP at 0.6% and SL at 0.4%, fees consume a large share of any win — material to profitability.
- **Empty trade journals:** `data/trades/` is empty (no live fills yet), consistent with Replit's
  451 geo-block. Live history will only accumulate on the droplet.

### Strategic (most important)
- **No backtested edge.** A faithful replay shows ~−0.24% expectancy per trade after costs and
  ~5% win rate. The architecture is solid and observable, but **frequency tuning will not create
  an edge** — the entry/exit logic itself needs a real, validated signal before risking more capital.

---

## 11. BOT SUMMARY (for another AI)

AlphaTrade is a **LIVE-only Binance Mainnet spot scalping bot** with a Streamlit dashboard,
written in Python. It trades **BTC, ETH, SOL** concurrently. There is **no paper/testnet** path;
if no authenticated client exists, orders raise rather than simulate.

**Runtime model:** A module-level `TradingBot` singleton (in `bot.py`) survives Streamlit reruns
and runs **one daemon thread** that loops every **2 seconds**. Each loop ticks one `SymbolWorker`
per symbol. A worker (1) manages its open positions (stop-loss 0.4%, take-profit ~0.6%, breakeven
armed at +0.20%, exit on 2 red candles), then (2) computes a signal (`Reversal Scalper` /
`Active Scalper`) refined by a local `ai_engine` advisor, classifies the **market regime**
(DEAD/RANGE/TREND/VOLATILE), and scores the setup 0–100 via the canonical **`weighted_decision`**
engine (AI 20 / RSI 15 / EMA 20 / MACD 20 / volume 15 / candle 10). The worker publishes a
candidate; the orchestrator **qualifies** it (`score>0` AND (`score≥threshold` OR `conf≥floor`)),
**ranks** all candidates, optionally asks a **GPT global analyst** for an advisory NO_TRADE veto,
then runs **two-tier risk gates** (per-symbol cooldown/direction-lock/session; global
exposure/concentration/open-cap-30/daily-loss-5%/emergency-stop) plus a ≥5s global throttle.
The winner is sized by score tier (40% base × 1.0–1.5, ×0.75 if VOLATILE, floored at the $10
Binance minimum, capped at 75% of free USDT) and sent as a **real MARKET order**.

**Persistence** is flat JSON: per-symbol trade journals, a shared activity log, settings, and
chmod-600 credentials. **Diagnostics** (`diagnostics.py`) is observability-only: it journals why
each symbol did/didn't trade every cycle (surfaced as the dashboard "WHY NO TRADE?" panel and a
top-10 report), computes trade-frequency stats, runs Binance preflight checks, and safely
reconciles "ghost" trades (local opens with no matching Binance balance → closed at entry, P&L 0;
partials are flagged, never auto-closed).

**Critical caveats for any future work:** (1) Replit is dev-only — `api.binance.com` returns 451
here; production is a DigitalOcean droplet. (2) The decision journal's reason attribution must
mirror the orchestrator's exact gate order, and ghost-reconcile must only close true dust. (3) All
recorded P&L is **gross** (no fees). (4) A faithful backtest shows **no positive edge today** — be
honest about this and fix the signal before increasing size or frequency.
