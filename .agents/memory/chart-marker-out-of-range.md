---
name: chart-marker-out-of-range
description: Why chart-marker "unmatched" must split benign out-of-range trades from genuine data errors, and how trade timestamps are normalized.
---

# Chart marker matching: out-of-range vs error

When mapping recorded trades onto the candlestick chart, an "unmatched" marker
has TWO fundamentally different causes that must NOT be lumped together:

- **out_of_range (benign):** the trade is simply older than the fetched candle
  window (~2000 candles) or there are no candles yet (chart still loading).
  Reasons: "open_time/close_time outside chart history", "no candle data on
  chart". This is expected on any account with history and must NEVER raise a
  scary `st.warning`. Show it calmly (caption + list it in the markers panel).
- **error (genuine):** missing/zero entry_price/exit_price, or
  missing/unparseable open_time/close_time. THIS is what deserves a warning.

`MarkerResult` exposes `errors`/`out_of_range` lists + `error_count`/
`out_of_range_count`; `UnmatchedTrade.out_of_range:bool` carries the flag.
Dashboard warns ONLY when `error_count > 0`.

**Why:** operators panicked at "Trade found but chart timestamp not matched — N
marker(s) could not be placed" when the trades were just off-screen history.
The warning conflated normal off-screen trades with real data corruption.

## Timestamp normalization (the naive/aware bug)

Trade timestamps are written with `datetime.now().isoformat()` — **naive
LOCAL** (symbol_worker open_time, bot close_time). The candle axis is naive
Europe/London wall-clock. `_to_axis_time` normalizes EVERY input through a
UTC-aware instant: `pd.Timestamp(...).astimezone(timezone.utc)` (naive →
system-local → UTC; aware → UTC), then `tz_convert(axis_tz)` and strip tz for
the naive axis. Equivalent to the old `astimezone(london)` path but explicit and
host-independent — removes any naive-vs-aware comparison error.

**How to apply:** keep nearest-candle snapping (`_match_candle`, tolerance =
median candle interval, one-interval grace past the edges) — do NOT switch to
hard floor (a test pins 12:30:40 → 12:31). Tests assuming naive==UTC
(`test_naive_*`) depend on a UTC test host (pre-existing assumption).
