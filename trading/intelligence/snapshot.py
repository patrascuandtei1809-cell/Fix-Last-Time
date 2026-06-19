"""Build market snapshots and maintain a non-blocking public-data cache."""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional, Tuple

from .cross_exchange import CrossExchangeComparison
from .liquidity_analysis import BookTicker, LiquidityAssessment, fetch_book_ticker
from .models import MarketSnapshot


class PublicMarketObserver:
    """Asynchronously refresh public book tickers.

    ``get`` never waits for the network. It returns the last cached observation
    and schedules a daemon refresh when data is absent or stale. This keeps
    diagnostics from delaying the live trading cycle.
    """

    def __init__(
        self,
        *,
        fetcher: Callable[[str, str], BookTicker] = fetch_book_ticker,
        ttl_sec: float = 60.0,
        failure_ttl_sec: float = 300.0,
        max_pending: int = 8,
        enabled: bool = True,
    ):
        self.fetcher = fetcher
        self.ttl_sec = float(ttl_sec)
        self.failure_ttl_sec = float(failure_ttl_sec)
        self.max_pending = max(1, int(max_pending))
        self.enabled = bool(enabled)
        self._cache: Dict[Tuple[str, str], Tuple[float, Optional[BookTicker]]] = {}
        self._pending = set()
        self._lock = threading.RLock()

    def get(self, exchange: str, symbol: str) -> Optional[BookTicker]:
        if not self.enabled:
            return None
        key = (str(exchange or "").lower(), str(symbol or "").upper())
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached:
                written_at, value = cached
                ttl = self.ttl_sec if value is not None else self.failure_ttl_sec
                if now - written_at < ttl:
                    return value
            if key not in self._pending and len(self._pending) < self.max_pending:
                self._pending.add(key)
                threading.Thread(
                    target=self._refresh,
                    args=key,
                    daemon=True,
                    name=f"shadow-book-{key[0]}-{key[1]}",
                ).start()
            return cached[1] if cached else None

    def _refresh(self, exchange: str, symbol: str) -> None:
        key = (exchange, symbol)
        value = None
        try:
            value = self.fetcher(exchange, symbol)
        except Exception:
            value = None
        finally:
            with self._lock:
                self._cache[key] = (time.monotonic(), value)
                self._pending.discard(key)

    def seed(self, ticker: BookTicker) -> None:
        key = (ticker.exchange.lower(), ticker.symbol.upper())
        with self._lock:
            self._cache[key] = (time.monotonic(), ticker)

    def wait_for_idle(self, timeout: float = 2.0) -> bool:
        """Testing/diagnostic helper; never used by the live cycle."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            with self._lock:
                if not self._pending:
                    return True
            time.sleep(0.01)
        with self._lock:
            return not self._pending


def build_snapshot(
    *,
    exchange: str,
    symbol: str,
    rec,
    settings=None,
    scanner_opportunity: Optional[dict] = None,
    liquidity: Optional[LiquidityAssessment] = None,
    primary_ticker: Optional[BookTicker] = None,
    cross_exchange: Optional[CrossExchangeComparison] = None,
) -> MarketSnapshot:
    opp = scanner_opportunity or {}
    warnings = []
    if liquidity:
        warnings.extend(liquidity.warnings)
    if cross_exchange:
        warnings.extend(cross_exchange.warnings)
    return MarketSnapshot(
        exchange=str(exchange or "binance").lower(),
        symbol=str(symbol or "").upper(),
        price=getattr(rec, "price", None),
        change_pct=getattr(rec, "change_pct", None),
        buy_threshold_pct=getattr(rec, "buy_threshold", None),
        volume_ratio=getattr(rec, "volume_ratio", None),
        min_volume_multiple=(
            getattr(settings, "min_volume_multiple", None)
            if settings is not None else None
        ),
        trend_ok=getattr(rec, "trend_ok", None),
        scanner_score=opp.get("score"),
        quote_volume_24h=opp.get("volume") or opp.get("quote_volume"),
        volatility_24h_pct=opp.get("volatility"),
        market_change_24h_pct=opp.get("change") or opp.get("change_pct"),
        bid=primary_ticker.bid if primary_ticker else None,
        ask=primary_ticker.ask if primary_ticker else None,
        spread_pct=liquidity.spread_pct if liquidity else None,
        cross_exchange=(
            cross_exchange.secondary_exchange if cross_exchange else None
        ),
        cross_exchange_price=(
            cross_exchange.secondary_price if cross_exchange else None
        ),
        cross_exchange_spread_pct=(
            cross_exchange.difference_pct if cross_exchange else None
        ),
        warnings=warnings,
    )
