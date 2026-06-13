---
name: AlphaTrade venue mode & legacy exit
description: Why a "missing/blank dashboard" usually means venue mode, and why legacy holdings must never be auto-liquidated.
---

# "Full layout disappeared" → check exchange_mode FIRST

The dashboard has two venue sections gated by `_show_binance` / `_show_mexc`,
derived from `settings.json` `exchange_mode` (`binance` | `mexc` | `multi`).
`mexc` mode hides the entire Binance half; `multi` shows both.

**Why:** A report of "the dashboard is gone / blank / broken" frequently maps to
the venue mode hiding a section, NOT a render crash. The actual blank-page
crashes are unhandled exceptions early in the script (e.g. the diagnostics
tz-aware datetime subtraction).

**How to apply:** Before assuming the UI is broken, (1) check `exchange_mode`,
and (2) confirm a clean render headlessly with Streamlit `AppTest`
(`AppTest.from_file("dashboard.py").run()` → assert no `at.exception`). The
Binance 451 "restricted location" message during this is the expected Replit
geo-block, not a failure.

# Never silently auto-liquidate legacy/orphan holdings

Legacy Binance coins (anything held that isn't BTC/ETH/SOL or a stablecoin) are
display-only. "Close them without bad loss" is only computable when a recorded
entry/cost basis exists (i.e. an OPEN tracked position). Orphan dust has no
basis, so "no bad loss" is undefined.

**Why:** Auto-selling user-owned assets based on incomplete accounting is an
unacceptable financial risk in a LIVE-only system. Dust ghost-reconcile may
auto-close TRUE dust, but real holdings must not be sold by a background loop.

**How to apply:** Surface a non-destructive "exit candidate (≥ breakeven)"
indicator only for legacy coins with a recorded position; keep the actual SELL
operator-confirmed. Do not add a silent legacy liquidator unless the user
explicitly opts in with hard limits.
