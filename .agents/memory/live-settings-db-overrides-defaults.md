---
name: Live settings DB overrides dataclass defaults
description: Why changing LiveSettings dataclass defaults does NOT change the running Market-Low rule, and how to actually enforce a rule invariant.
---

The live Market-Low executor (`DipLiveEngine`) reads its thresholds/filters from
`live_settings.get_settings()`, which returns the **PostgreSQL-persisted** row
(singleton id=1). The `LiveSettings` dataclass defaults only apply when the DB is
empty/unavailable.

**Consequence:** editing the dataclass defaults in `live_settings.py` does NOT
change the running rule when a stale DB row exists (e.g. a droplet whose row has
`trend_filter_on=False`, `min_volume_multiple=0.0` — filters effectively OFF even
though the spec requires them ON).

**Why:** the DB is the source of truth for operator-tunable live controls; defaults
are a fallback, not an override.

**How to apply:** to GUARANTEE a rule invariant (e.g. trend+volume filters always
ON), force-snap the loaded settings at dashboard cold start AND `save_settings(...)`
the correction back, so the droplet self-heals on first boot after deploy. Do this
in the live-settings load block in `dashboard.py` (right after `get_settings()`),
mirroring the strategy/exchange_mode force-snap pattern. Don't rely on dataclass
defaults or on manually editing one environment's DB — the other environment's row
will still win.
