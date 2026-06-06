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

## TRUTHMODE reversal — manual zoom must STICK (Binance-like)

The requirement later flipped: the operator wanted a Binance-style chart where a
manual scroll/box-zoom is KEPT across refresh/bot-scan/price-tick and only
resets on explicit Reset buttons or symbol/timeframe change. That means we now
WANT failure-mode #2 ("stuck") deliberately. Canonical design now in use:

- **Stable `uirevision`** keyed on `symbol-interval-nonce` (e.g.
  `trade_chart_fixed-…`). `nonce` bumps ONLY on Zoom-in/out / Reset buttons;
  symbol/interval change naturally rotates the key. So plain reruns keep
  uirevision unchanged → the user's interactive X+Y zoom WINS and is preserved;
  the explicit `range` is ignored. Reset/zoom buttons bump the nonce → explicit
  range wins → view resets. This single mechanism satisfies "keep exact X AND Y".
- **Do NOT try to capture Plotly relayout (zoom/pan) into session_state** —
  plain `st.plotly_chart` exposes only selection events (`on_select`), not
  zoom/pan relayout. uirevision is the supported persistence path; a code
  reviewer may flag "range not stored from interaction" but that capture is
  neither feasible without a 3rd-party component nor needed.
- **session_state x-range (`chart_saved_xrange`) is for the FROZEN case only.**
  An "Auto-follow latest" toggle: ON recomputes the window each tick so it
  tracks the newest candle; OFF reuses the saved range so the underlying figure
  range does NOT drift forward *before* any manual gesture (uirevision can't
  preserve a gesture that never happened). The Y-axis auto-fits to the candles
  inside the current X window; in OFF mode that window is frozen so Y is stable
  too, and after any manual zoom uirevision keeps the user's Y regardless.

**Pick by intent:** server-controlled deterministic view → per-render uirevision
+ explicit range (top of file). Binance-like sticky manual zoom → stable
uirevision keyed on a button/symbol/interval nonce (this section).
