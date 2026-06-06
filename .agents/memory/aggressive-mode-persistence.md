---
name: Aggressive-mode (trading intensity) persistence
description: DB-vs-JSON precedence for the trading-intensity mode store and the test-hermeticity rule it requires.
---

# Trading-intensity persistence: DB authoritative, JSON is a fallback (not a mirror)

The trading-intensity / aggressive-mode store (`trading/aggressive_mode.py`) persists in
Postgres when available, with a local JSON file fallback for no-DB droplets.

Rules (do not regress):
- **`get_mode`**: when the DB connection succeeds it is AUTHORITATIVE — an empty row
  returns the safe DEFAULT and must NOT consult the JSON file. JSON is read only when
  the DB is unavailable OR the query errored.
- **`set_mode`**: write JSON ONLY when the DB write did not succeed (fallback, not
  mirror). Mirroring on DB success pollutes DB-backed environments (incl. the test DB)
  and makes a stale on-disk file shadow the DB in later reads.

**Why:** the original mirror-write created `data/aggressive_mode.json` during DB-backed
tests; a later test then read the stale file (DB empty → fell through to JSON) and got
`Very Aggressive` instead of the DEFAULT `Balanced`. Also the droplet originally had no
JSON fallback at all, so `set_mode` silently failed and the dashboard selector and its
description disagreed (the "Very Aggressive vs Balanced" contradiction).

**How to apply:**
- Tests for the JSON path must `monkeypatch` BOTH `am._conn` (→ `None`, to force no-DB)
  and `am._JSON_PATH` (→ a tmp file) so they never touch the real `data/` file.
- The runtime JSON file `trading/data/aggressive_mode.json` is gitignored (runtime
  state, like activity.json/creds) — never commit it.
- The Streamlit selector is kept consistent by driving both the widget (key
  `aggro_intensity_sel`) and the description from the SAME persisted value, rerunning on
  save and snapping the widget back on a (now near-impossible) save failure.
