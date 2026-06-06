"""Pure, testable trade-marker builder for the dashboard candlestick chart.

Maps recorded trades (``trading/data/trades/*.json``) onto BUY / SELL chart
markers, anchored to the candle nearest each trade timestamp. It returns
EXPLICIT counts and an ``unmatched`` list so the dashboard can render a debug
panel + a clear warning instead of silently dropping markers.

Why this exists
---------------
Every recorded trade is a LONG: ``side == "BUY"``. The position OPEN is the
BUY marker (``open_time`` / ``entry_price``); the position CLOSE is the SELL
marker (``close_time`` / ``exit_price``).

Timezone contract (the subtle bug this fixes)
---------------------------------------------
The candle axis (``df_chart["open_time"]``) is **tz-naive Europe/London
wall-clock** (Binance UTC klines are converted to London then tz-stripped).
Trade timestamps are NOT uniform: ``open_time`` is written UTC-aware while
``close_time`` is written naive server-local. To land a marker on the correct
candle EVERY trade timestamp must be coerced to naive Europe/London, mirroring
the dashboard's display helper ``_to_london``. The previous chart code localized
naive timestamps as UTC and never converted the UTC ``open_time`` to London, so
markers were placed up to a full tz-offset away from their candle (and clipped
out of view) — which is why "trades recorded but no markers" happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

DEFAULT_AXIS_TZ = "Europe/London"


@dataclass
class MarkerPoint:
    """A single chart marker resolved to a real candle."""
    x: Any            # snapped candle timestamp (chart axis time)
    y: float          # price — entry_price for BUY, exit_price for SELL
    trade_id: str
    ttype: str        # "bot" | "manual"
    raw_time: Any     # the trade timestamp coerced to axis tz (pre-snap)


@dataclass
class UnmatchedTrade:
    """A trade whose BUY or SELL marker could not be placed on the chart."""
    trade_id: str
    kind: str         # "BUY" | "SELL"
    reason: str


@dataclass
class MarkerResult:
    trades_found: int = 0
    buy: List[MarkerPoint] = field(default_factory=list)
    sell: List[MarkerPoint] = field(default_factory=list)
    unmatched: List[UnmatchedTrade] = field(default_factory=list)

    @property
    def buy_drawn(self) -> int:
        return len(self.buy)

    @property
    def sell_drawn(self) -> int:
        return len(self.sell)

    @property
    def unmatched_count(self) -> int:
        return len(self.unmatched)


def _tid(t: dict) -> str:
    return str(t.get("id") or "?")[:8]


def _as_ns(obj):
    """Normalize a Timestamp/DatetimeIndex to nanosecond resolution.

    pandas >= 2.0 supports multiple datetime resolutions (s/ms/us/ns). When a
    candle index and a trade timestamp end up at *different* units,
    ``DatetimeIndex.searchsorted`` raises ``ValueError: Cannot losslessly
    convert units`` (it refuses to round a finer scalar down to a coarser
    index). Coercing both sides to ``ns`` — always a lossless widening — makes
    the comparison safe. No-op on older pandas (everything is ns there).
    """
    try:
        return obj.as_unit("ns")
    except (AttributeError, ValueError):
        return obj


def _to_axis_time(ts: Any, axis_tz, tzname: str):
    """Coerce any trade timestamp to the candle axis's time reference.

    Mirrors ``dashboard._to_london``: a tz-aware value is converted; a naive
    value is assumed to be server-local and converted via ``astimezone``. When
    the axis is tz-naive (the real case — London wall-clock) the result is
    tz-stripped so it compares directly against the candle index. Returns
    ``None`` if the value is missing/unparseable.
    """
    if ts is None or ts == "":
        return None
    try:
        parsed = pd.to_datetime(ts)
    except Exception:
        return None
    if parsed is None or pd.isna(parsed):
        return None

    london = ZoneInfo(tzname)
    py = parsed.to_pydatetime()
    try:
        # astimezone: naive -> assume system-local; aware -> convert. Same rule
        # the dashboard uses for DISPLAY, so markers align with shown times.
        london_dt = py.astimezone(london)
    except Exception:
        return None
    out = pd.Timestamp(london_dt)

    if axis_tz is None:
        # Candle axis is naive wall-clock -> drop tz so comparison works.
        return _as_ns(out.tz_localize(None))
    # Defensive: tz-aware axis -> express in that tz.
    return _as_ns(out.tz_convert(axis_tz))


def _match_candle(ts, idx: pd.DatetimeIndex, tol: pd.Timedelta):
    """Return the candle timestamp nearest ``ts`` within the data range.

    Returns ``None`` when ``ts`` falls outside the fetched candle history
    (beyond ``tol`` past either edge) — i.e. the marker cannot be placed.
    """
    if len(idx) == 0:
        return None
    cmin, cmax = idx[0], idx[-1]
    if ts < cmin - tol or ts > cmax + tol:
        return None
    pos = int(idx.searchsorted(ts))
    if pos <= 0:
        return idx[0]
    if pos >= len(idx):
        return idx[-1]
    before, after = idx[pos - 1], idx[pos]
    return before if (ts - before) <= (after - ts) else after


def build_trade_markers(
    trades: Optional[list],
    symbol: str,
    candle_times: Any,
    tzname: str = DEFAULT_AXIS_TZ,
) -> MarkerResult:
    """Build BUY/SELL markers for ``symbol`` from recorded ``trades``.

    Parameters
    ----------
    trades : list of trade dicts (any symbols — filtered here by ``coin``).
    symbol : the symbol currently shown on the chart (e.g. ``"BTCUSDT"``).
    candle_times : the chart's candle open-times (``df_chart["open_time"]``),
        a pandas Series / Index / list of timestamps. Defines the placeable
        time range and the candle each marker snaps to.
    tzname : the candle-axis wall-clock zone (default Europe/London).
    """
    result = MarkerResult()
    sym = (symbol or "").upper()
    sym_trades = [
        t for t in (trades or [])
        if (t.get("coin") or "").upper() == sym
    ]
    result.trades_found = len(sym_trades)

    # Build a sorted candle index.
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(pd.Index(list(candle_times))))
    except Exception:
        idx = pd.DatetimeIndex([])
    if len(idx):
        idx = _as_ns(idx.sort_values())
    axis_tz = idx.tz if len(idx) else None

    if len(idx) == 0:
        # No candles to anchor to: every expected marker is unmatched. Account
        # at MARKER level (not trade level) — a closed trade owes both a BUY and
        # a SELL marker, so it contributes two unmatched entries.
        for t in sym_trades:
            tid = _tid(t)
            result.unmatched.append(
                UnmatchedTrade(tid, "BUY", "no candle data on chart"))
            xp = t.get("exit_price")
            ct = t.get("close_time")
            is_closed = (t.get("status") == "closed") or (xp is not None and ct is not None)
            if is_closed:
                result.unmatched.append(
                    UnmatchedTrade(tid, "SELL", "no candle data on chart"))
        return result

    # Tolerance = one candle interval (median spacing) so a trade timestamped a
    # few seconds past the last candle still snaps to it. Floor at 1s.
    if len(idx) >= 2:
        secs = pd.Series(idx).diff().dt.total_seconds().median()
        tol = pd.Timedelta(seconds=float(secs) if secs and secs > 0 else 60.0)
    else:
        tol = pd.Timedelta(minutes=1)

    for t in sym_trades:
        tid = _tid(t)
        ttype = t.get("type", "manual")

        # ── BUY marker (position OPEN) ────────────────────────────────────
        ep = t.get("entry_price")
        if ep is None or float(ep or 0) <= 0:
            result.unmatched.append(
                UnmatchedTrade(tid, "BUY", "missing/zero entry_price"))
        else:
            raw = _to_axis_time(t.get("open_time"), axis_tz, tzname)
            if raw is None:
                result.unmatched.append(
                    UnmatchedTrade(tid, "BUY", "missing/unparseable open_time"))
            else:
                snap = _match_candle(raw, idx, tol)
                if snap is None:
                    result.unmatched.append(
                        UnmatchedTrade(tid, "BUY", "open_time outside chart history"))
                else:
                    result.buy.append(
                        MarkerPoint(snap, float(ep), tid, ttype, raw))

        # ── SELL marker (position CLOSE) — only for closed trades ─────────
        xp = t.get("exit_price")
        ct = t.get("close_time")
        is_closed = (t.get("status") == "closed") or (xp is not None and ct is not None)
        if not is_closed:
            continue
        if xp is None or float(xp or 0) <= 0:
            result.unmatched.append(
                UnmatchedTrade(tid, "SELL", "missing/zero exit_price"))
            continue
        raw = _to_axis_time(ct, axis_tz, tzname)
        if raw is None:
            result.unmatched.append(
                UnmatchedTrade(tid, "SELL", "missing/unparseable close_time"))
            continue
        snap = _match_candle(raw, idx, tol)
        if snap is None:
            result.unmatched.append(
                UnmatchedTrade(tid, "SELL", "close_time outside chart history"))
            continue
        result.sell.append(MarkerPoint(snap, float(xp), tid, ttype, raw))

    return result
