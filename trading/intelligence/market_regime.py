"""Deterministic, read-only market regime classification."""
from __future__ import annotations

from .models import MarketRegimeAssessment, MarketSnapshot


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def detect_market_regime(snapshot: MarketSnapshot) -> MarketRegimeAssessment:
    trend = snapshot.trend_ok
    change_24h = _number(snapshot.market_change_24h_pct)
    volatility = _number(snapshot.volatility_24h_pct)
    reasons = []

    if trend is True and change_24h is not None and change_24h >= 1.0:
        reasons.append("Upturn confirmed with positive 24h momentum")
        return MarketRegimeAssessment("trending_up", 82.0, reasons)
    if trend is False and change_24h is not None and change_24h <= -1.0:
        reasons.append("Weak short-term trend with negative 24h momentum")
        return MarketRegimeAssessment("trending_down", 78.0, reasons)
    if volatility is not None and volatility >= 15.0 and trend is not True:
        reasons.append("High volatility without confirmed upturn")
        return MarketRegimeAssessment("choppy", 75.0, reasons)
    if change_24h is not None and abs(change_24h) <= 2.0:
        if volatility is None or volatility <= 10.0:
            reasons.append("Contained 24h movement suggests a range")
            return MarketRegimeAssessment("ranging", 68.0, reasons)
    if trend is True:
        reasons.append("Short-term upturn exists but broader context is incomplete")
        return MarketRegimeAssessment("recovering", 60.0, reasons)
    reasons.append("Not enough aligned data for a strong regime label")
    return MarketRegimeAssessment("uncertain", 40.0, reasons)
