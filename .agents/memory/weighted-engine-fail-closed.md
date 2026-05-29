---
name: Weighted engine fail-closed
description: The canonical edge engine on the LIVE trading bot must fail closed; NaN coercion pitfall.
---

# Weighted decision engine must FAIL CLOSED

On the AlphaTrade LIVE Binance bot, `strategy.weighted_decision()` is the single
canonical edge gate (it replaced the parallel `score_market()` path in the
worker). When the only edge gate can throw or be unimportable, you MUST treat
that failure as a hard veto (HOLD, score=0), never let it fall through to a
weaker qualification path.

**Why:** there was a real LIVE-order safety gap — a throwing weighted engine
left the veto flag empty, and the orchestrator could still qualify a candidate
on the AI-confidence path and place a real Binance order with zero weighted
protection. Architect failed the review on exactly this.

**How to apply:** any time you make one component THE gate for a real-money /
irreversible action, audit every failure path (exception, import error, None
return) and default it to "block", plus a defense-in-depth check downstream
(orchestrator requires `score>0`; `execute_entry()` guards `score>0` as an
invariant so a direct/manual call can't bypass it).

# NaN coercion pitfall

`float(np.nan)` does NOT raise — it returns `nan`, and `min/max` clamping can
coerce a NaN into a fake extreme score that fires a trade. Always add an
explicit `np.isfinite(...)` check over every extracted indicator before scoring,
and return a HOLD veto on any non-finite value. A bare `try/except float(...)`
is NOT enough.
