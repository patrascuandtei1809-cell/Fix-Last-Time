---
name: st_autorefresh must render early, not at page bottom
description: Why a Streamlit page "scrolls to the bottom on its own" on each auto-refresh, and the fix.
---

# st_autorefresh placement controls scroll jump

Symptom: operator leaves the dashboard untouched and it "scrolls to the bottom
on its own" every few seconds.

**Why:** `streamlit_autorefresh.st_autorefresh()` renders a real (invisible)
iframe component wherever it is called. If that call is the LAST statement in a
long script, the iframe is the bottom-most element; each auto-refresh re-mounts
it and the browser scrolls down to it. Result: the page keeps jumping to the
bottom on every tick.

**Rule:** call `st_autorefresh(...)` EARLY — right after the init that populates
its interval input (e.g. `refresh_secs`) — so the iframe sits at the top and the
scroll position stays put. Keep exactly ONE keyed instance; a duplicate call
double-mounts the timer.

**How to apply:** any Streamlit page that feels like it "won't stay scrolled"
or "jumps" on refresh — check where the autorefresh component is rendered first.
Minor tradeoff: if the call runs before persisted settings load, the first paint
uses the default interval until the next rerun adopts the saved value (harmless).
