"""Read-only market intelligence for AlphaTrade.

Everything in this package is diagnostic-only.  It may observe public market
data and describe what a strategy *would* prefer, but it cannot place orders or
alter the live engine's decision.
"""

from .models import (
    MarketRegimeAssessment,
    MarketSnapshot,
    RouteDecision,
    ScoreComponent,
    StrategyAssessment,
)

__all__ = [
    "MarketRegimeAssessment",
    "MarketSnapshot",
    "RouteDecision",
    "ScoreComponent",
    "StrategyAssessment",
]
