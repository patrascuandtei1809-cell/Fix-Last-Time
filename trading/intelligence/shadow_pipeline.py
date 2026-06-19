"""Orchestrate AlphaTrade intelligence after the live decision is complete."""
from __future__ import annotations

import threading
from typing import Optional

from .ai_risk import assess_ai_risk
from .cross_exchange import compare_prices
from .liquidity_analysis import analyze_liquidity
from .market_regime import detect_market_regime
from .scoring import score_snapshot
from .snapshot import PublicMarketObserver, build_snapshot
from .strategy_router import assess_strategies, route_strategy


class ShadowIntelligencePipeline:
    """Read-only diagnostics.

    The API deliberately accepts no order callback, risk-manager callback, or
    live-engine mutation hook. It returns a serializable recommendation only.
    """

    def __init__(self, observer: Optional[PublicMarketObserver] = None):
        self.observer = observer or PublicMarketObserver()

    def analyze(
        self,
        *,
        exchange: str,
        symbol: str,
        rec,
        settings=None,
        scanner_opportunity: Optional[dict] = None,
    ):
        venue = str(exchange or "binance").lower()
        other = "mexc" if venue == "binance" else "binance"
        primary_book = self.observer.get(venue, symbol)
        secondary_book = self.observer.get(other, symbol)
        quote_volume = (scanner_opportunity or {}).get("volume")
        liquidity = analyze_liquidity(primary_book, quote_volume)
        primary_price = (
            primary_book.mid if primary_book and primary_book.mid
            else getattr(rec, "price", None)
        )
        secondary_price = (
            secondary_book.mid
            if secondary_book and secondary_book.mid else None
        )
        cross = compare_prices(venue, primary_price, other, secondary_price)
        snapshot = build_snapshot(
            exchange=venue,
            symbol=symbol,
            rec=rec,
            settings=settings,
            scanner_opportunity=scanner_opportunity,
            liquidity=liquidity,
            primary_ticker=primary_book,
            cross_exchange=cross,
        )
        regime = detect_market_regime(snapshot)
        ai_risk = assess_ai_risk(snapshot, regime)
        components, total = score_snapshot(snapshot, liquidity, cross, ai_risk)
        assessments = assess_strategies(snapshot, regime, components)
        return route_strategy(
            snapshot,
            regime,
            components,
            total,
            assessments,
            extra_warnings=liquidity.warnings + cross.warnings + ai_risk.warnings,
        )


_DEFAULT_PIPELINE = None
_DEFAULT_LOCK = threading.Lock()


def get_default_shadow_pipeline() -> ShadowIntelligencePipeline:
    global _DEFAULT_PIPELINE
    with _DEFAULT_LOCK:
        if _DEFAULT_PIPELINE is None:
            _DEFAULT_PIPELINE = ShadowIntelligencePipeline()
        return _DEFAULT_PIPELINE
