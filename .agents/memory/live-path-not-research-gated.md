---
name: Live dip path is not research-gated (two live paths)
description: AlphaTrade — the default 20-Minute Dip live engine must NEVER be research-gated; a SEPARATE opt-in StrategyLiveEngine path IS gated. Don't confuse them.
---

# Two live paths — only ONE is research-gated

There are now TWO live order paths, mutually exclusive per bot instance:

1. **`live_engine.DipLiveEngine` (`dip_mode=True`, the DEFAULT)** — trades its own
   price signal and must **never** be research-gated (this file). Set by
   `create_bot` for every strategy EXCEPT V2.
2. **`live_engine.StrategyLiveEngine` (`strategy_mode=True`)** — the opt-in,
   research-VALIDATED path. `create_bot` flips to it ONLY when
   `strategy == "EMA_MACD_RSI_VOLUME_V2"`; it consults
   `research.is_strategy_validated(strategy, interval, symbol)` fail-closed in its
   entry branch so only the approved cell (V2@4h → ETH; BTC/SOL blocked) trades.
   This is intentional and does NOT contradict the rule below — it's a *different*
   engine the operator explicitly selects, not the dip path. Regression lock:
   `tests/test_strategy_live_engine.py`.

# Live dip path must NOT consult research

The live trading path (`live_engine.DipLiveEngine`, `bot.TradingBot.dip_mode=True`,
the default) places REAL Binance orders on its own price signal. It must **never**
be gated by the research module's validation / allowlist / verdict
(`research.is_strategy_validated`, `validated_strategies.json`). Research is an
analysis-only product in this monorepo.

**Why:** This has flip-flopped at least twice. An "AUTO-DISABLE gate" kept getting
wired into the live entry path (and a "🔒 AUTO-TRADE PAUSED FOR SAFETY" dashboard
banner), which silently stopped the bot from trading because the committed
allowlist is empty / REJECTs everything (no proven after-fee edge — see
`alphatrade-no-edge-verdict`). The operator's intent is that the bot trades on its
dip signal; whether there's a *backtested* edge is a separate research question
that must not block live execution.

**How to apply:**
- Keep `is_strategy_validated` importable (research module, tests) but NEVER call
  it from `DipLiveEngine._evaluate` or wire `require_validation`/`validate_fn` into
  the dip engine.
- The legacy `dip_mode=False` orchestrator gate in `bot.py` may stay (regression-
  locked by `test_bot_validation_gate.py`) but is unreachable in live mode — do
  not "fix" it by routing live trades through it.
- These money-safety gates are SEPARATE and must always stay active in the live
  path: emergency stop, safe mode, balance check, spending limit, max position
  size, cooldown (30-min post stop-loss), daily-loss auto-stop. (Same
  safety-vs-tuning separation principle as `alphatrade-aggressive-mode`.)
- Regression lock:
  `test_dip_strategy.test_live_dip_ignores_research_reject_but_safety_still_blocks`
  asserts a dip BUY fires with an empty/REJECT allowlist while safe mode still
  blocks.
