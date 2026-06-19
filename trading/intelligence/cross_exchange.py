"""Read-only Binance/MEXC price comparison."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CrossExchangeComparison:
    primary_exchange: str
    secondary_exchange: str
    primary_price: Optional[float]
    secondary_price: Optional[float]
    difference_pct: Optional[float]
    confidence_score: float
    reasons: List[str]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["confidence_score"] = round(float(self.confidence_score), 2)
        return data


def compare_prices(
    primary_exchange: str,
    primary_price: Optional[float],
    secondary_exchange: str,
    secondary_price: Optional[float],
) -> CrossExchangeComparison:
    difference = None
    reasons: List[str] = []
    warnings: List[str] = []
    confidence = 50.0
    try:
        p1 = float(primary_price or 0.0)
        p2 = float(secondary_price or 0.0)
        if p1 > 0 and p2 > 0:
            midpoint = (p1 + p2) / 2.0
            difference = abs(p1 - p2) / midpoint * 100.0
            reasons.append(
                f"{primary_exchange}/{secondary_exchange} difference "
                f"{difference:.3f}%"
            )
            if difference <= 0.10:
                confidence = 95.0
            elif difference <= 0.50:
                confidence = 80.0
            elif difference <= 1.50:
                confidence = 55.0
            elif difference <= 3.00:
                confidence = 35.0
                warnings.append("Material cross-exchange price difference")
            else:
                confidence = 15.0
                warnings.append("Large cross-exchange difference; data or liquidity risk")
        else:
            warnings.append("Cross-exchange price unavailable")
    except (TypeError, ValueError):
        warnings.append("Invalid cross-exchange price")
    return CrossExchangeComparison(
        primary_exchange=str(primary_exchange or "").lower(),
        secondary_exchange=str(secondary_exchange or "").lower(),
        primary_price=primary_price,
        secondary_price=secondary_price,
        difference_pct=difference,
        confidence_score=confidence,
        reasons=reasons,
        warnings=warnings,
    )
