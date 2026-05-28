"""
Market regime classifier — SMART AI SCALPING BOT (May 2026).

Given the indicator-enriched OHLCV dataframe (output of `strategy.get_indicators`),
returns one of:
    DEAD     — very low ATR, tiny candles, low volume      → never trade
    RANGE    — sideways, EMAs flat/crossed, mixed candles  → only super-high score
    TREND    — clean EMA structure, momentum continuing    → normal aggressive scalp
    VOLATILE — large candles + volume spike                → trade OK, downsize risk

The classifier is conservative: missing data or insufficient bars → "DEAD" so
the bot doesn't trade on a noisy/empty snapshot.
"""
from __future__ import annotations
from typing import Tuple, Dict
import pandas as pd
import numpy as np


# Tunables — kept here so they're easy to find / log next to the result.
_DEAD_ATR_PCT      = 0.04   # ATR % of price below this → DEAD candidate
_DEAD_VOL_RATIO    = 0.55   # avg-relative volume below this → DEAD candidate
_TREND_EMA_SPREAD  = 0.05   # |EMA9 - EMA21| / price (%) above this → trend candidate
_TREND_SLOPE_BARS  = 3      # consecutive same-direction EMA9 closes
_VOLATILE_ATR_PCT  = 0.18   # ATR % of price above this → VOLATILE candidate
_VOLATILE_VOL_RATIO = 1.6   # volume vs 20-bar avg above this → VOLATILE bias
_BODY_BIG_PCT      = 0.10   # last candle body % of price above this → punchy bar


def classify_regime(df: pd.DataFrame) -> Tuple[str, Dict[str, float]]:
    """Return (regime, telemetry) where regime ∈ {DEAD, RANGE, TREND, VOLATILE}.

    Telemetry keys: atr_pct, vol_ratio, ema_spread_pct, body_pct, slope_bars.
    """
    tele: Dict[str, float] = {
        "atr_pct": 0.0, "vol_ratio": 0.0, "ema_spread_pct": 0.0,
        "body_pct": 0.0, "slope_bars": 0,
    }
    if df is None or len(df) < 25:
        return "DEAD", tele

    try:
        last  = df.iloc[-1]
        price = float(last["close"])
        if price <= 0:
            return "DEAD", tele

        # ATR % of price — volatility relative to price.
        atr = float(last.get("atr", 0) or 0)
        atr_pct = (atr / price) * 100.0 if price > 0 else 0.0
        tele["atr_pct"] = round(atr_pct, 4)

        # Volume vs 20-bar average.
        vol_avg = float(df["volume"].tail(20).mean() or 0)
        vol_now = float(last.get("volume", 0) or 0)
        vol_ratio = (vol_now / vol_avg) if vol_avg > 0 else 0.0
        tele["vol_ratio"] = round(vol_ratio, 3)

        # EMA9 vs EMA21 spread as % of price.
        ema9  = float(last.get("ema9", 0) or 0)
        ema21 = float(last.get("ema21", 0) or 0)
        ema_spread_pct = abs(ema9 - ema21) / price * 100.0 if price > 0 else 0.0
        tele["ema_spread_pct"] = round(ema_spread_pct, 4)

        # EMA9 slope — count consecutive same-direction bars (last 5).
        ema9_series = df["ema9"].tail(_TREND_SLOPE_BARS + 1).values
        slope_dir   = 0
        if len(ema9_series) >= 2:
            diffs = np.diff(ema9_series)
            if all(d > 0 for d in diffs):    slope_dir = +len(diffs)
            elif all(d < 0 for d in diffs):  slope_dir = -len(diffs)
        tele["slope_bars"] = int(slope_dir)

        # Last candle body size as % of price.
        body     = abs(float(last["close"]) - float(last["open"]))
        body_pct = body / price * 100.0 if price > 0 else 0.0
        tele["body_pct"] = round(body_pct, 4)
    except Exception:
        return "DEAD", tele

    # ── classification (order matters: check DEAD first, then VOLATILE,
    #    then TREND, fall through to RANGE) ─────────────────────────────────
    if atr_pct < _DEAD_ATR_PCT and vol_ratio < _DEAD_VOL_RATIO:
        return "DEAD", tele

    if atr_pct >= _VOLATILE_ATR_PCT or (
       vol_ratio >= _VOLATILE_VOL_RATIO and body_pct >= _BODY_BIG_PCT):
        return "VOLATILE", tele

    if (ema_spread_pct >= _TREND_EMA_SPREAD
            and abs(slope_dir) >= _TREND_SLOPE_BARS):
        return "TREND", tele

    return "RANGE", tele


# Score adjustment (called by strategy.score_market via regime arg).
# TREND  → +5 bonus, RANGE → -10 penalty, VOLATILE → 0 (size is downsized
# elsewhere), DEAD → hard cap score at 30 so it cannot pass the 65 floor.
def apply_regime_to_score(score: int, regime: str) -> int:
    if regime == "DEAD":
        return min(int(score), 30)
    if regime == "TREND":
        return min(100, int(score) + 5)
    if regime == "RANGE":
        return max(0, int(score) - 10)
    return int(score)   # VOLATILE: no score change; downsize at execution
