---
name: Emergency stop is session-only
description: Why the AlphaTrade emergency stop must never be persisted/restored, and where the re-arm paths hide.
---

Emergency stop is a SESSION-ONLY kill switch. It must default OFF and start OFF
on every cold load / browser refresh, turning ON only when the operator clicks
it in the current session.

**Why:** A browser refresh = a NEW Streamlit session, so the one-time settings
loader re-runs. The dashboard used to persist `risk.emergency_stop` into
settings.json and restore the whole risk dict blindly, so a single STOP click
silently halted ALL trading on every subsequent refresh — a LIVE-money outage.

**How to apply:** There are THREE dataclasses that each carry an
`emergency_stop` field — per-symbol `RiskSettings`, account-wide
`GlobalRiskSettings`, and the per-symbol overrides map. Any persist/restore that
touches risk config must (1) NOT write emergency_stop to settings.json, and
(2) skip + force-False emergency_stop on restore for ALL THREE. Fixing only the
top-level `risk` path leaves the global/per-symbol restore loops as latent
re-arm vectors. The snapshot dumps for global_risk / per_symbol_risk use a
fixed key allowlist (no emergency_stop) — keep it that way.
