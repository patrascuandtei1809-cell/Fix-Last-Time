---
name: AlphaTrade approval-rule lock-in tests
description: How the strategy-approval verdict is made testable and what each gate means, including the max-drawdown gate's exact role.
---

# Approval-rule verdict engine + lock-in tests

The strategy-approval decision lives in TWO pure, testable places, exercised by
`trading/tests/` (run with `python -m pytest trading/tests`):

- `validate_candidates.classify_candidate(base, sens_rows, wf, mc)` → the deep
  ROBUST / WEAK / REJECTED verdict for the hand-picked 4h candidates.
- `research._verdict(subcells, agg, wf)` → the strict ACCEPT / REJECT that gates
  the live allowlist; `research.is_strategy_validated()` is the default-safe live
  auto-disable gate.

## Max-drawdown gate is a ROBUST-BLOCKER, not a reject trigger
**Rule:** a base max drawdown beyond `MAX_DD_LIMIT_PCT` (40%) sets `dd_ok=False`
and prevents ROBUST, but is deliberately EXCLUDED from the `rejected` hard-fail
set — so a DD-only breach downgrades to **WEAK**, never REJECTED.
**Why:** the task spec lists "max drawdown exceeds limit" as a failure-of-approval
condition, but a deep drawdown alone (with otherwise-healthy stats) is "not
proven robust", not "proven broken". Conflating the two would wrongly REJECT
candidates that are merely risky. A test pins `verdict=="WEAK"` AND
`gates["rejected"] is False` for the DD-only case.
**How to apply:** if you ever add DD to the reject criteria, that anti-regression
test will (correctly) fail — don't "fix" it by moving DD into `rejected`.

## Refactor invariant: report output must stay byte-identical
`bootstrap_expectancy()` was extracted from `_monte_carlo()`; `_monte_carlo`
keeps drawing from the SAME returned rng so the reshuffle-DD numbers match a
single combined draw. Tests use `bootstrap_expectancy` directly for a fast,
seeded MC CI. If you re-split the RNG usage, re-verify `report()` prints the same
ETH=ROBUST / SOL×2=WEAK verdicts and numbers.

## Locked guarantees (don't let these silently regress)
Only V2 ETH @4h is ROBUST; both SOL candidates stay WEAK; no 1m/5m/15m/1h cell
is ever ACCEPT; allowlist empty unless explicitly approved (proven by code path:
`save_validated([])` → empty → `is_strategy_validated()` returns False).
