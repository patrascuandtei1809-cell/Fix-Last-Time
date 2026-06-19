"""Explainable component scoring for shadow decisions."""
from __future__ import annotations

from typing import Dict, Tuple

from .ai_risk import AIRiskAssessment
from .cross_exchange import CrossExchangeComparison
from .liquidity_analysis import LiquidityAssessment
from .models import MarketSnapshot, ScoreComponent


WEIGHTS = {
    "dip": 0.22,
    "trend": 0.18,
    "volume": 0.14,
    "liquidity": 0.16,
    "volatility": 0.10,
    "exchange_confidence": 0.10,
    "ai_risk": 0.05,
    "scanner": 0.05,
}


def _component(name, score, reason, available=True) -> ScoreComponent:
    return ScoreComponent(
        name=name,
        score=max(0.0, min(100.0, float(score))),
        weight=WEIGHTS[name],
        reason=reason,
        available=available,
    )


def _dip(snapshot: MarketSnapshot) -> ScoreComponent:
    if snapshot.change_pct is None or snapshot.buy_threshold_pct in (None, 0):
        return _component("dip", 50, "Dip data unavailable", False)
    change = float(snapshot.change_pct)
    threshold = abs(float(snapshot.buy_threshold_pct))
    if change >= 0:
        return _component("dip", 10, f"No dip: change {change:+.3f}%")
    ratio = abs(change) / max(threshold, 1e-9)
    if ratio < 0.5:
        score = 25
    elif ratio < 1.0:
        score = 45 + ratio * 20
    elif ratio <= 4.0:
        score = 80 + min(15, (ratio - 1.0) * 5)
    elif ratio <= 12.0:
        score = 70
    else:
        score = 35
    return _component(
        "dip", score,
        f"Dip {change:+.3f}% equals {ratio:.1f}x the entry threshold",
    )


def _trend(snapshot: MarketSnapshot) -> ScoreComponent:
    if snapshot.trend_ok is True:
        return _component("trend", 90, "Short-term upturn confirmed")
    if snapshot.trend_ok is False:
        return _component("trend", 20, "Short-term upturn not confirmed")
    return _component("trend", 50, "Trend confirmation unavailable", False)


def _volume(snapshot: MarketSnapshot) -> ScoreComponent:
    if snapshot.volume_ratio is None:
        return _component("volume", 50, "Volume ratio unavailable", False)
    ratio = float(snapshot.volume_ratio)
    required = float(snapshot.min_volume_multiple or 1.0)
    relative = ratio / max(required, 1e-9)
    if relative >= 1.5:
        score = 95
    elif relative >= 1.0:
        score = 80
    elif relative >= 0.5:
        score = 50
    else:
        score = 20
    return _component(
        "volume", score,
        f"Volume {ratio:.2f}x versus required {required:.2f}x",
    )


def _volatility(snapshot: MarketSnapshot) -> ScoreComponent:
    if snapshot.volatility_24h_pct is None:
        return _component("volatility", 50, "24h volatility unavailable", False)
    value = float(snapshot.volatility_24h_pct)
    if 2.0 <= value <= 12.0:
        score = 85
    elif 1.0 <= value < 2.0:
        score = 55
    elif 12.0 < value <= 25.0:
        score = 55
    elif value > 25.0:
        score = 20
    else:
        score = 35
    return _component("volatility", score, f"24h volatility {value:.2f}%")


def _scanner(snapshot: MarketSnapshot) -> ScoreComponent:
    if snapshot.scanner_score is None:
        return _component("scanner", 50, "Scanner score unavailable", False)
    value = max(0.0, min(100.0, float(snapshot.scanner_score)))
    return _component("scanner", value, f"Scanner score {value:.0f}/100")


def score_snapshot(
    snapshot: MarketSnapshot,
    liquidity: LiquidityAssessment,
    cross_exchange: CrossExchangeComparison,
    ai_risk: AIRiskAssessment,
) -> Tuple[Dict[str, ScoreComponent], float]:
    components = {
        "dip": _dip(snapshot),
        "trend": _trend(snapshot),
        "volume": _volume(snapshot),
        "liquidity": _component(
            "liquidity", liquidity.score,
            "; ".join(liquidity.reasons) or "Liquidity data incomplete",
            available=liquidity.spread_pct is not None,
        ),
        "volatility": _volatility(snapshot),
        "exchange_confidence": _component(
            "exchange_confidence",
            cross_exchange.confidence_score,
            "; ".join(cross_exchange.reasons)
            or "Cross-exchange comparison unavailable",
            available=cross_exchange.difference_pct is not None,
        ),
        "ai_risk": _component(
            "ai_risk", ai_risk.safety_score, ai_risk.summary,
            available=ai_risk.enabled,
        ),
        "scanner": _scanner(snapshot),
    }
    total = sum(item.contribution for item in components.values())
    return components, round(total, 2)
