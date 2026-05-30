---
name: Streamlit + Plotly live-chart zoom persistence
description: Why an auto-refreshing Plotly chart snaps back / flashes, and the gating pattern that fixes it.
---

# Auto-refreshing Plotly chart: zoom-snap + flash

A Streamlit chart driven by `st_autorefresh` (full script rerun every N sec)
will appear to "flash" and reset the user's manual zoom/pan if the figure
re-applies an explicit axis `range`/`autorange=False` on every rerun — the
per-tick axis relayout fights the user's interaction.

**Fix pattern (in `trading/dashboard.py` chart block):** apply the explicit
window/range ONLY when the user actually changed the view (a zoom/reset button
bumps a nonce) or on first render. Track an `applied_nonce` in session_state;
compute `force_view = applied_nonce != current_nonce`. On plain auto-refresh
ticks, set NO range/autorange so Plotly's stable `uirevision`
(`symbol+interval+nonce`) preserves the current zoom/pan. Set
`applied_nonce = current_nonce` right after `st.plotly_chart`.

**Hard tradeoff:** you cannot have BOTH a flash-free chart AND one that
auto-scrolls to the newest candle. Auto-follow requires changing the x-range
every tick (= relayout = flash). The chosen default favors no-flash +
sticky-zoom; the chart no longer slides right on its own — the "Reset window"
button jumps back to the live edge. If live auto-follow is wanted back, it must
be an opt-in toggle and will reintroduce per-tick redraw.

**Why:** the operator explicitly complained about BOTH flashing and zoom
snapping back while trying to inspect history; sticky-zoom + no-flash is the
behavior that satisfies that, at the cost of auto-follow.
