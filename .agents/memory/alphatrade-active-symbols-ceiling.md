---
name: AlphaTrade active-symbols ceiling vs open-trades caps
description: Two independent ceilings bound bot concurrency; raising one without the other silently truncates the symbol plan.
---

# Two independent concurrency ceilings in bot.py

Bot concurrency is bounded by TWO unrelated limits that must move together:

1. `risk.GlobalRiskSettings` open-trades caps (per-venue: binance/mexc/total) —
   how many positions may be OPEN at once.
2. `bot.MAX_ACTIVE_SYMBOLS` — the hard truncation of `sym_list` in `create_bot`
   (how many WORKERS / scanned symbols exist at all).

**Why:** When per-venue caps were split (Binance 3 + MEXC 15 = 18), the open-trades
cap allowed 18 positions, but `MAX_ACTIVE_SYMBOLS` was still 15, so the 18-symbol
plan (3 pinned majors + 15 scanner alts) was truncated to 15 → only 12 MEXC slots
could ever fill. The cap looked correct in risk.py and in the [SETTINGS] log, but
the bot physically could not run enough workers.

**How to apply:** Any time you raise an open-trades cap (or the scanner top-N),
check `MAX_ACTIVE_SYMBOLS` covers `pinned majors + max MEXC cap`. Keep a regression
test asserting `MAX_ACTIVE_SYMBOLS >= 18` (or the current pinned+cap sum).
