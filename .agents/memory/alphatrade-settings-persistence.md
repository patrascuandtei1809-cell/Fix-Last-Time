---
name: AlphaTrade settings persistence
description: How persisted dashboard settings can be silently reset on cold start, and what must stay in sync.
---

# AlphaTrade settings persistence pitfalls

The Streamlit dashboard (`trading/dashboard.py`) persists settings to
`trading/data/settings.json`. Three independent layers must agree for a
setting to actually survive a refresh/restart:

1. `_init()` defaults dict — initial session value.
2. `_PERSIST_KEYS` (top-level) and the `risk` / `global_risk` / `per_symbol_risk`
   dict loaders — what gets READ back from disk on cold start.
3. `_collect_settings_snapshot()` — what gets WRITTEN to disk.

**Why this matters:** historically a "force-snap" block ran on every cold start
and re-hardcoded risk numbers (SL/TP, max_open, cooldown, max_open_trades_total)
*after* loading them from disk, so user changes appeared to "reset" (the classic
"max trades keeps resetting to 1" complaint). That block was reduced to only
snap strategy-mode invariants (strategy name, interval, tick, threshold, AI on,
re-instate BTC+ETH+SOL). **Do not reintroduce numeric-risk force-snaps** — they
defeat persistence.

**Sync gotcha:** `st.session_state.risk` (a RiskSettings) and
`st.session_state.risk_manager.settings` are different objects. The sidebar
syncs them (`risk_manager.settings = r`) but that runs late in the script. The
settings-load block must sync them *before* `_maybe_resume_bot()`, or an
auto-resumed worker ticks with stale/default risk for the first cycle.

**How to apply:** when adding a new persisted setting, add it to all three
layers above, confirm the force-snap block does not overwrite it, and if it
feeds the running bot, make sure it's synced before auto-resume.
