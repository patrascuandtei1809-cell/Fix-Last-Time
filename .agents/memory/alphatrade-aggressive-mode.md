---
name: Aggressive Mode safety separation
description: How the operator "Aggressive Mode" profiles stay isolated from the safety layer in the AlphaTrade trading bot.
---

# Aggressive Mode — knobs vs. safety

Operator-selectable trading intensity (Conservative/Balanced/Aggressive/Very
Aggressive) lives in `trading/aggressive_mode.py`. The selected mode is the
durable source of truth in **PostgreSQL** (`DATABASE_URL`, psycopg2), with an
append-only audit table; reads fall back to `Balanced` and never raise when the
DB is down.

## The rule
A mode profile may only tune *frequency / threshold / size* knobs:
`confidence_floor`, `score_threshold_base/floor`, `gpt_prob_floor`,
`global_throttle_sec`, `cooldown_seconds`, `dynamic_size_pct`, `check_every`.

It must **never** write the safety layer: `TradingBot.require_validation` +
`research.is_strategy_validated` (allowlist), `GlobalRiskSettings` caps
(spending/exposure/open-trades/daily-loss/emergency_stop), per-symbol
`emergency_stop`/`max_open_trades`, or exchange checks.

**Why:** aggressive sizing/thresholds must increase *opportunity*, not weaken
*protection*. The two concerns are enforced in different code paths so a mode
change can never silently disable validation or a risk cap. Aggressive size is
only a *request* — `execute_entry()` still floors at the $10 min-notional and
caps at free×0.75, and `GlobalRiskManager.check_global()` still enforces every
cap regardless of mode.

**How to apply:** `apply_profile_to_bot()`/`apply_profile_to_risk()` set ONLY
the knob attributes above. If you add a new knob, keep it out of the safety set
and extend the non-mutation tests in `trading/tests/test_aggressive_mode.py`
(they assert `require_validation`, global-risk vars, and per-symbol safety stay
unchanged across all modes, and that the global gate still blocks after a mode
switch). Confidence floors are pinned to the spec 85/75/65/55.
