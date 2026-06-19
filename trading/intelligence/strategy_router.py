"""Choose a diagnostic strategy recommendation; never an executable action."""
from __future__ import annotations

from typing import Dict, List

from .models import (
    MarketRegimeAssessment,
    MarketSnapshot,
    RouteDecision,
    ScoreComponent,
    StrategyAssessment,
)


def assess_strategies(
    snapshot: MarketSnapshot,
    regime: MarketRegimeAssessment,
    components: Dict[str, ScoreComponent],
) -> List[StrategyAssessment]:
    dip_score = (
        components["dip"].score * 0.35
        + components["trend"].score * 0.25
        + components["volume"].score * 0.20
        + components["liquidity"].score * 0.20
    )
    trend_score = (
        components["trend"].score * 0.40
        + components["volume"].score * 0.20
        + components["liquidity"].score * 0.20
        + components["volatility"].score * 0.20
    )
    arbitrage_score = (
        100.0 - components["exchange_confidence"].score
        if components["exchange_confidence"].available else 0.0
    )
    return [
        StrategyAssessment(
            strategy="dip_rebound",
            score=dip_score,
            eligible=regime.regime in {"ranging", "recovering", "uncertain"},
            reasons=["Controlled dip plus rebound confirmation"],
        ),
        StrategyAssessment(
            strategy="trend_following",
            score=trend_score,
            eligible=regime.regime == "trending_up",
            reasons=["Positive trend, volume and liquidity alignment"],
        ),
        StrategyAssessment(
            strategy="arbitrage_observation",
            score=arbitrage_score,
            eligible=False,
            reasons=["Diagnostic price comparison only"],
            warnings=["Real arbitrage execution is disabled"],
        ),
    ]


def route_strategy(
    snapshot: MarketSnapshot,
    regime: MarketRegimeAssessment,
    components: Dict[str, ScoreComponent],
    total_score: float,
    assessments: List[StrategyAssessment],
    extra_warnings=None,
) -> RouteDecision:
    warnings = list(snapshot.warnings)
    warnings.extend(extra_warnings or [])
    by_name = {item.strategy: item for item in assessments}
    reasons = list(regime.reasons)

    if components["liquidity"].available and components["liquidity"].score < 30:
        strategy = "avoid"
        reasons.append("Liquidity/spread quality is weak")
    elif regime.regime in {"choppy", "trending_down"}:
        strategy = "avoid"
        reasons.append(f"Market regime is {regime.regime}")
    elif regime.regime == "trending_up":
        strategy = "trend_following"
        reasons.extend(by_name[strategy].reasons)
    elif by_name["dip_rebound"].score >= by_name["trend_following"].score:
        strategy = "dip_rebound"
        reasons.extend(by_name[strategy].reasons)
    else:
        strategy = "observe"
        reasons.append("No strategy has strong enough alignment")

    risk = "low" if total_score >= 75 else "medium" if total_score >= 50 else "high"
    would_trade = bool(
        strategy in {"dip_rebound", "trend_following"}
        and total_score >= 70
    )
    return RouteDecision(
        recommended_strategy=strategy,
        market_regime=regime.regime,
        total_score=total_score,
        confidence=max(0.0, min(1.0, total_score / 100.0)),
        risk_level=risk,
        would_trade=would_trade,
        components=components,
        assessments=assessments,
        reasons=reasons,
        warnings=warnings,
        snapshot=snapshot,
        mode="shadow",
        executable=False,
    )
