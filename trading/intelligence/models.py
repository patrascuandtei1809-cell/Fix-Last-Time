"""Serializable models used by the shadow intelligence pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    score: float
    weight: float
    reason: str
    available: bool = True

    @property
    def contribution(self) -> float:
        return round(float(self.score) * float(self.weight), 4)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["score"] = round(float(self.score), 2)
        data["weight"] = round(float(self.weight), 4)
        data["contribution"] = self.contribution
        return data


@dataclass(frozen=True)
class MarketSnapshot:
    exchange: str
    symbol: str
    price: Optional[float] = None
    change_pct: Optional[float] = None
    buy_threshold_pct: Optional[float] = None
    volume_ratio: Optional[float] = None
    min_volume_multiple: Optional[float] = None
    trend_ok: Optional[bool] = None
    scanner_score: Optional[float] = None
    quote_volume_24h: Optional[float] = None
    volatility_24h_pct: Optional[float] = None
    market_change_24h_pct: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread_pct: Optional[float] = None
    cross_exchange: Optional[str] = None
    cross_exchange_price: Optional[float] = None
    cross_exchange_spread_pct: Optional[float] = None
    observed_at: str = field(default_factory=utcnow_iso)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketRegimeAssessment:
    regime: str
    confidence: float
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 2)
        return data


@dataclass(frozen=True)
class StrategyAssessment:
    strategy: str
    score: float
    eligible: bool
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["score"] = round(float(self.score), 2)
        return data


@dataclass(frozen=True)
class RouteDecision:
    recommended_strategy: str
    market_regime: str
    total_score: float
    confidence: float
    risk_level: str
    would_trade: bool
    components: Dict[str, ScoreComponent]
    assessments: List[StrategyAssessment]
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    snapshot: Optional[MarketSnapshot] = None
    mode: str = "shadow"
    executable: bool = False
    generated_at: str = field(default_factory=utcnow_iso)
    version: str = "shadow-v1"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": "shadow",
            "executable": False,
            "version": self.version,
            "generated_at": self.generated_at,
            "recommended_strategy": self.recommended_strategy,
            "market_regime": self.market_regime,
            "total_score": round(float(self.total_score), 2),
            "confidence": round(float(self.confidence), 4),
            "risk_level": self.risk_level,
            "would_trade": bool(self.would_trade),
            "components": {
                name: component.to_dict()
                for name, component in self.components.items()
            },
            "assessments": [item.to_dict() for item in self.assessments],
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
        }
