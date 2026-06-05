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

## Droplet de-facto config = the COMMITTED settings.json

`trading/data/settings.json` is git-tracked, and the production droplet's
`restart-bot.sh` runs `git stash` on it before `git pull` (and does not pop it
back). Net effect: **on every droplet restart the operator's in-UI settings are
discarded and the COMMITTED settings.json values become the live config.** So
if the operator complains a setting "always resets" (e.g. max_open_trades back
to 1, or the bot trading too rarely), check the committed values in
settings.json first — fixing the committed file is what actually changes droplet
behavior. The in-app sliders hold fine within a running session; the reset is
restart-driven.

**Why:** runtime state should not really be version-controlled; until it is
untracked + gitignored, the committed file is authoritative on the droplet.

**How to apply:** to change effective droplet defaults, edit the committed
settings.json (keep it consistent with the "Reset to … defaults" button, which
represents the operator's intended config). A permanent fix is to untrack it
(`git rm --cached`) + gitignore so UI changes survive restarts.

## The LIVE dip path uses a SEPARATE Postgres store, not settings.json

The 20-Minute Dip live path (`live_engine.py`) reads its thresholds from
`live_settings.get_settings()`, which loads a single JSONB row (`id=1`) from the
Postgres table `trading_live_settings` — a store entirely independent of
`settings.json`. `get_settings()` deliberately **never overwrites the stored row
with code defaults** (only returns defaults when the row is missing).

**Why this matters:** editing the `LiveSettings` dataclass defaults
(`buy_threshold_pct` etc.) does **NOT** change live behavior if a row already
exists — the stale persisted values keep driving real orders. To actually change
the effective live thresholds you must ALSO rewrite the stored row
(`get_settings()` → set fields → `save_settings()`).

**Do NOT add a forced startup normalization** that re-writes the row to defaults
every boot — that would defeat operator tuning, exactly like the old
settings.json force-snap. Update the row once (migration-style) instead.

**How to apply:** any change to a live-threshold default must be paired with a
one-time `save_settings()` of the new value in every environment that has a row
(this dev DB and the droplet's DB are separate rows).
