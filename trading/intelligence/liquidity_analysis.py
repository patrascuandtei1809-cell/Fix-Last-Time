"""Public, read-only spread and top-of-book diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import requests


_BOOK_URLS = {
    "binance": "https://api.binance.com/api/v3/ticker/bookTicker",
    "mexc": "https://api.mexc.com/api/v3/ticker/bookTicker",
}


@dataclass(frozen=True)
class BookTicker:
    exchange: str
    symbol: str
    bid: float
    ask: float
    bid_qty: float = 0.0
    ask_qty: float = 0.0

    @property
    def mid(self) -> Optional[float]:
        if self.bid <= 0 or self.ask <= 0:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> Optional[float]:
        mid = self.mid
        if not mid:
            return None
        return max(0.0, (self.ask - self.bid) / mid * 100.0)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["mid"] = self.mid
        data["spread_pct"] = self.spread_pct
        return data


@dataclass(frozen=True)
class LiquidityAssessment:
    score: float
    spread_pct: Optional[float]
    quote_volume_24h: Optional[float]
    top_depth_usdt: Optional[float]
    risk_level: str
    reasons: List[str]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["score"] = round(float(self.score), 2)
        return data


def fetch_book_ticker(exchange: str, symbol: str, timeout: float = 3.0) -> BookTicker:
    """Fetch best bid/ask from a public endpoint. Never uses credentials."""
    venue = str(exchange or "").lower()
    if venue not in _BOOK_URLS:
        raise ValueError(f"unsupported public book venue: {venue}")
    response = requests.get(
        _BOOK_URLS[venue],
        params={"symbol": str(symbol or "").upper()},
        timeout=timeout,
    )
    response.raise_for_status()
    raw = response.json()
    return BookTicker(
        exchange=venue,
        symbol=str(raw.get("symbol") or symbol).upper(),
        bid=float(raw.get("bidPrice") or 0.0),
        ask=float(raw.get("askPrice") or 0.0),
        bid_qty=float(raw.get("bidQty") or 0.0),
        ask_qty=float(raw.get("askQty") or 0.0),
    )


def _spread_score(spread_pct: Optional[float]) -> float:
    if spread_pct is None:
        return 50.0
    if spread_pct <= 0.05:
        return 100.0
    if spread_pct <= 0.15:
        return 85.0
    if spread_pct <= 0.30:
        return 65.0
    if spread_pct <= 0.60:
        return 40.0
    return 15.0


def _volume_score(quote_volume: Optional[float]) -> float:
    if quote_volume is None:
        return 50.0
    if quote_volume >= 10_000_000:
        return 100.0
    if quote_volume >= 1_000_000:
        return 85.0
    if quote_volume >= 100_000:
        return 65.0
    if quote_volume >= 10_000:
        return 40.0
    return 20.0


def _depth_score(depth_usdt: Optional[float]) -> float:
    if depth_usdt is None:
        return 50.0
    if depth_usdt >= 10_000:
        return 100.0
    if depth_usdt >= 1_000:
        return 80.0
    if depth_usdt >= 100:
        return 50.0
    return 20.0


def analyze_liquidity(
    ticker: Optional[BookTicker],
    quote_volume_24h: Optional[float],
) -> LiquidityAssessment:
    try:
        quote_volume_24h = (
            float(quote_volume_24h)
            if quote_volume_24h is not None else None
        )
    except (TypeError, ValueError):
        quote_volume_24h = None
    spread = ticker.spread_pct if ticker else None
    depth = None
    if ticker and ticker.bid > 0 and ticker.ask > 0:
        depth = min(ticker.bid * ticker.bid_qty, ticker.ask * ticker.ask_qty)
    score = (
        _spread_score(spread) * 0.50
        + _volume_score(quote_volume_24h) * 0.30
        + _depth_score(depth) * 0.20
    )
    warnings: List[str] = []
    reasons: List[str] = []
    if spread is None:
        warnings.append("Public best bid/ask not available yet")
    else:
        reasons.append(f"Top-of-book spread {spread:.3f}%")
        if spread > 0.60:
            warnings.append("Wide spread observed")
    if quote_volume_24h is None:
        warnings.append("24h quote volume unavailable")
    else:
        reasons.append(f"24h quote volume ${quote_volume_24h:,.0f}")
    if depth is not None:
        reasons.append(f"Visible top depth about ${depth:,.0f}")
    risk = "low" if score >= 75 else "medium" if score >= 45 else "high"
    return LiquidityAssessment(
        score=score,
        spread_pct=spread,
        quote_volume_24h=quote_volume_24h,
        top_depth_usdt=depth,
        risk_level=risk,
        reasons=reasons,
        warnings=warnings,
    )
