"""Tests for read-only multi-strategy shadow intelligence."""
import json
from types import SimpleNamespace

from intelligence.liquidity_analysis import BookTicker, analyze_liquidity
from intelligence.market_regime import detect_market_regime
from intelligence.models import MarketSnapshot
from intelligence.shadow_pipeline import ShadowIntelligencePipeline
from intelligence.snapshot import PublicMarketObserver


class _ReadOnlyGatewayTrap:
    """Public fetches are allowed; every order method is forbidden."""

    def __init__(self):
        self.fetch_calls = []
        self.order_calls = []

    def __call__(self, exchange, symbol):
        self.fetch_calls.append((exchange, symbol))
        if exchange == "binance":
            return BookTicker(exchange, symbol, 99.9, 100.1, 20.0, 20.0)
        return BookTicker(exchange, symbol, 99.8, 100.2, 20.0, 20.0)

    def place_buy_order(self, *args, **kwargs):
        self.order_calls.append(("BUY", args, kwargs))
        raise AssertionError("shadow intelligence attempted a BUY")

    def place_sell_order(self, *args, **kwargs):
        self.order_calls.append(("SELL", args, kwargs))
        raise AssertionError("shadow intelligence attempted a SELL")

    def place_market_order(self, *args, **kwargs):
        self.order_calls.append(("MARKET", args, kwargs))
        raise AssertionError("shadow intelligence attempted a market order")


def _rec():
    return SimpleNamespace(
        price=100.0,
        change_pct=-0.12,
        buy_threshold=-0.05,
        volume_ratio=1.4,
        trend_ok=True,
        decision="HOLD",
        reason="diagnostic test",
        traded=False,
    )


def test_shadow_pipeline_never_calls_order_functions():
    gateway = _ReadOnlyGatewayTrap()
    observer = PublicMarketObserver(fetcher=gateway, ttl_sec=60)
    pipeline = ShadowIntelligencePipeline(observer=observer)

    first = pipeline.analyze(
        exchange="binance",
        symbol="BTCUSDT",
        rec=_rec(),
        settings=SimpleNamespace(min_volume_multiple=1.0),
        scanner_opportunity={
            "score": 82,
            "volume": 5_000_000,
            "volatility": 4.2,
            "change": 2.1,
        },
    )
    assert observer.wait_for_idle()

    second = pipeline.analyze(
        exchange="binance",
        symbol="BTCUSDT",
        rec=_rec(),
        settings=SimpleNamespace(min_volume_multiple=1.0),
        scanner_opportunity={
            "score": 82,
            "volume": 5_000_000,
            "volatility": 4.2,
            "change": 2.1,
        },
    )

    assert first.executable is False
    assert second.executable is False
    assert second.mode == "shadow"
    assert second.snapshot.spread_pct is not None
    assert second.snapshot.cross_exchange_spread_pct is not None
    assert gateway.fetch_calls
    assert gateway.order_calls == []
    payload = json.loads(json.dumps(second.to_dict()))
    assert payload["mode"] == "shadow"
    assert payload["executable"] is False


def test_liquidity_score_penalizes_wide_spread():
    tight = analyze_liquidity(
        BookTicker("mexc", "TESTUSDT", 99.95, 100.05, 100, 100),
        2_000_000,
    )
    wide = analyze_liquidity(
        BookTicker("mexc", "TESTUSDT", 98.0, 102.0, 1, 1),
        2_000_000,
    )

    assert tight.score > wide.score
    assert wide.risk_level == "high"


def test_liquidity_accepts_scanner_volume_strings():
    result = analyze_liquidity(
        BookTicker("mexc", "TESTUSDT", 99.95, 100.05, 100, 100),
        "2000000",
    )

    assert result.quote_volume_24h == 2_000_000
    assert result.score > 50


def test_market_regime_detects_confirmed_uptrend():
    regime = detect_market_regime(
        MarketSnapshot(
            exchange="binance",
            symbol="BTCUSDT",
            trend_ok=True,
            market_change_24h_pct=3.0,
            volatility_24h_pct=5.0,
        )
    )

    assert regime.regime == "trending_up"
    assert regime.confidence > 70
