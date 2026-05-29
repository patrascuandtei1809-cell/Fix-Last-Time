---
name: Entry gate invariant
description: The two-stage entry gating in the AlphaTrade bot (orchestrator qualify + worker sizing) must agree on the same rule.
---

# Entry-gate invariant (AlphaTrade scalping bot)

Entry decisions pass through TWO independent gates:
1. **Orchestrator qualification** in `trading/bot.py` — picks the winning candidate.
2. **Sizing gate** in `trading/symbol_worker.py` `execute_entry()` — decides the
   position size, and returns early (blocks) if size resolves to 0.

**Rule:** both gates must apply the SAME entry predicate. If the orchestrator
qualifies a candidate but the sizing gate uses a stricter floor, the trade is
**qualified upstream then silently blocked at sizing** — the bot looks "decided
to trade" in the rank log but never places an order.

**Why:** this exact mismatch was shipped twice. The orchestrator used an OR rule
(score ≥ threshold OR confidence ≥ floor) while the sizing tiers only credited
score ≥ 60, so confidence-only and low-score winners were dropped at sizing. Both
review rounds flagged it.

**How to apply:** when you change the entry rule (thresholds, score/confidence
floors, anti-idle threshold lowering), change it in BOTH places. The robust
pattern: the orchestrator stamps its effective `score_threshold` onto the winner
dict before calling `execute_entry()`, and the sizing gate reads that stamped
floor instead of hardcoding one — so anti-idle threshold changes stay consistent
end-to-end.
