---
name: AlphaTrade chart BUY/SELL marker placement
description: Why trade markers vanished from the dashboard candlestick chart and the tz/axis contract that keeps them on the right candle.
---

# Trade markers must be coerced to the candle axis's tz, snapped, and accounted

The Streamlit dip-bot chart kept "recording trades but showing no BUY/SELL
markers". Multiple compounding causes, all fixed by a dedicated pure module
`trading/chart_markers.py::build_trade_markers(trades, symbol, candle_times)`:

1. **Axis-tz mismatch (the real placement bug).** The candle axis
   (`df_chart["open_time"]`) is **tz-NAIVE Europe/London wall-clock** —
   `binance_client._klines_to_df` converts UTC klines to London then
   `tz_localize(None)`. But trade timestamps are NOT uniform: `open_time` is
   written **UTC-aware** (`_utcnow().isoformat()`) while `close_time` is written
   **naive server-local** (`datetime.now().isoformat()`). The old inline
   coercion localized naive as UTC and, because the axis tz was `None`, never
   converted the UTC `open_time` to London — so markers landed up to a full
   tz-offset (~1h in BST) away from their candle and were clipped out of view.
   **Rule:** coerce EVERY trade timestamp to the axis reference the same way the
   display helper `_to_london` does (`astimezone(Europe/London)`, then tz-strip
   when the axis is naive) before placing a marker.

2. **Silent drops.** A per-trade `try/except: continue` ate any marker that
   raised (e.g. the NameError era). Never swallow per-marker errors — classify
   them into an explicit `unmatched` list with a reason and surface it.

3. **Default view too narrow.** `DEFAULT_WINDOW_HOURS=1` clipped any trade older
   than an hour off-screen by default; the dip bot trades hours apart. Bumped to
   24h.

4. **Every recorded trade is a LONG** (`side=='BUY'`). The BUY marker = position
   OPEN (`open_time`/`entry_price`); the SELL marker = position CLOSE
   (`close_time`/`exit_price`). There is no separate short "SELL" trade.

**Accounting is MARKER-level, not trade-level.** A closed trade owes BOTH a BUY
and a SELL marker. Every branch (including the empty-candle-data early return)
must count each missing marker separately, or the debug panel under-reports
unmatched markers. The dashboard renders a "🔍 Chart markers" panel
(trades found / BUY drawn / SELL drawn / unmatched) + a "Trade found but chart
timestamp not matched" warning straight from this result.

## pandas datetime RESOLUTION mismatch breaks searchsorted

pandas >= 2.0 (droplet runs 3.x) supports multiple datetime units (s/ms/us/ns).
The real candle axis can come back **seconds-resolution** while a trade
timestamp carries **microseconds**. `DatetimeIndex.searchsorted(ts)` then raises
`ValueError: Cannot losslessly convert units` — it refuses to round a finer
scalar DOWN to a coarser index. Tests with matching units never hit it.
**Rule:** normalize BOTH the candle index and every coerced trade timestamp to
`ns` (`_as_ns` → `obj.as_unit("ns")`, guarded for old pandas) before any
comparison/searchsorted. Widening to ns is always lossless; older pandas is
ns-only so it's a no-op. Regression test uses `_candles().as_unit("s")` + a
`.500000` microsecond open_time.

**How to apply:** the module is pure and unit-tested offline (no Streamlit,
network, or Binance) — exercise tz coercion and snapping with synthetic naive
candle ranges + UTC/naive trade strings. Verify markers stay placeable across
zoom by snapping to the nearest candle within a one-candle tolerance.
