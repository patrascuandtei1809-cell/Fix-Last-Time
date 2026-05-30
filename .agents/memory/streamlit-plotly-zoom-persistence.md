---
name: Streamlit + Plotly live-chart zoom control
description: Why an auto-refreshing Plotly chart gets "stuck" zoomed out or snaps back, and the deterministic fix.
---

# Auto-refreshing Plotly chart: making zoom controllable

A Streamlit chart driven by `st_autorefresh` (full script rerun every few sec)
fights the user over the axis range. Two opposite failure modes, both real:

1. **Snap-back**: if the figure sets an explicit `range` AND `uirevision`
   changes every tick, the user's zoom is wiped on each refresh.
2. **Stuck zoomed out**: if `uirevision` is STABLE across refresh, Plotly
   PRESERVES the user's last scroll/box-zoom gesture and **ignores** the
   explicit `range` you set in the figure. A user who scroll-zooms out to days
   stays stuck there forever, and pressing your Reset button only helps for one
   frame. (This is the trap I fell into by gating range on an interaction nonce —
   do NOT do that.)

**Key fact:** when `uirevision` is unchanged, a user's interactive zoom WINS
over an explicit figure `range`. When `uirevision` changes, the figure `range`
wins and the gesture is discarded.

**Deterministic fix that actually works (server-controlled view):**
- Always set an explicit bounded x-range `[view_start, view_end]`
  (`autorange=False`).
- Make `uirevision` change every render (key it on a monotonically-increasing
  per-render counter). This guarantees the explicit range wins every tick, so
  the view is whatever the server decides — never a stale stuck gesture.
- Drive zoom with BUTTONS (zoom in/out/reset) that change a window value; that
  window is canonical because it's re-applied each render. Scroll/box-zoom
  becomes momentary (resets on next refresh) — acceptable; buttons are the UX.
- **Bound the window to the candle data you actually hold**
  (`view_end = data_end`, `view_start = max(data_start, view_end - window)`).
  Otherwise zooming out shows empty space + old trade markers floating where
  there are no candles (a 1m chart only fetches ~33h of candles).

**Why:** the operator kept zooming out and the chart stayed blown out to ~10
days of empty space; the nonce-gated approach let Plotly autorange/preserve a
stale gesture. A per-render uirevision + data-bounded window made the view
deterministic and controllable.
