"""AI risk interface.

No external AI call is enabled in shadow-v1.  This neutral result keeps a
future AI provider behind a read-only interface and prevents it from silently
changing live decisions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from .models import MarketRegimeAssessment, MarketSnapshot


@dataclass(frozen=True)
class AIRiskAssessment:
    enabled: bool
    source: str
    risk_level: str
    safety_score: float
    summary: str
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["safety_score"] = round(float(self.safety_score), 2)
        return data


def assess_ai_risk(
    _snapshot: MarketSnapshot,
    _regime: MarketRegimeAssessment,
) -> AIRiskAssessment:
    return AIRiskAssessment(
        enabled=False,
        source="disabled",
        risk_level="unknown",
        safety_score=50.0,
        summary="AI risk provider disabled; neutral diagnostic score only",
        warnings=["AI does not approve, reject, or place orders in shadow-v1"],
    )
