---
name: AlphaTrade venue-mode plan separation
description: The UI venue selector and the bot's symbol/venue plan must share ONE mode-aware resolver, or MEXC-only mode silently trades LIVE Binance.
---

# Venue selector and bot plan must agree

The dashboard has an `exchange_mode` selector (`multi` / `binance` / `mexc`).
Gating the UI sections by mode is NOT enough: the bot's symbol/venue plan is a
SEPARATE path, and if it ignores `exchange_mode` the UI gating is purely
cosmetic while the bot keeps trading the other venue.

**The bug:** the scanner-driven plan helper unconditionally pinned BTC/ETH/SOL to
the **binance** venue for every mode (`resolve_live_plan`). In MEXC-only mode the
bot therefore still spun up LIVE Binance major workers — the "bot keeps returning
to BTC/ETH/SOL" complaint, and a real safety leak (Binance is LIVE-only).

**The rule:** route ALL "which symbols on which venue" decisions through ONE
mode-aware resolver (`bot.resolve_plan_for_mode`) so the UI and the bot can never
disagree:
- `mexc`    → MEXC scanner alts ONLY (never pin Binance majors). May be empty
  pre-scan → caller falls back to static symbols, still MEXC-routed.
- `binance` → the 3 pinned majors only.
- `multi`   → majors pinned to Binance + MEXC scanner alts.

**Why:** Binance is LIVE-only. A single-venue mode that leaks an opportunity onto
the other exchange places real orders the operator never selected.

**How to apply:** when adding venues or modes, extend `resolve_plan_for_mode`
(the single source of truth) — do NOT branch venue logic inside the dashboard.
`create_bot` already routes correctly: with a `symbol_venues` map it builds only
referenced venues; with an empty map it routes every worker by `exchange_mode`.
So STATIC mode (scanner OFF) needs no venue map — empty `{}` + `exchange_mode`
is correct. Regression guard lives in `test_scanner_driven.py`
(`test_plan_for_mode_*`): MEXC mode must emit zero `exchange=="binance"` entries.
