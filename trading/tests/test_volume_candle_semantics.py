"""Regression tests for live dip volume/trend candle semantics."""
import pandas as pd

import live_engine as le
import live_settings as ls


class _Exchange:
    name = "binance"
    client = object()

    def __init__(self, closes, volumes):
        self.closes = closes
        self.volumes = volumes
        self.buy_calls = []

    def get_price(self, _symbol):
        return float(self.closes[-1])

    def get_klines(self, _symbol, _interval, limit=25):
        return pd.DataFrame({
            "close": self.closes[-limit:],
            "volume": self.volumes[-limit:],
        })

    @staticmethod
    def get_balance(_asset):
        return {"free": 1000.0, "total": 1000.0}

    def place_buy_order(self, symbol, quote):
        self.buy_calls.append((symbol, quote))
        price = self.get_price(symbol)
        return {"ok": True, "price": price, "qty": quote / price, "fee": 0.0}


def _settings():
    settings = ls.LiveSettings()
    settings.safe_mode = False
    settings.buy_threshold_pct = -0.20
    settings.volume_filter_on = True
    settings.min_volume_multiple = 1.5
    settings.trend_filter_on = False
    return settings


def _evaluate(exchange):
    return le.DipLiveEngine(
        exchange=exchange,
        cooldown=ls.CooldownStore(),
    ).evaluate(
        symbol="BTCUSDT",
        settings=_settings(),
        open_trades=[],
        current_exposure=0.0,
        global_gate_fn=lambda _amount, _symbol: (True, ""),
    )


def test_volume_uses_latest_completed_candle():
    # Completed candle is 2x average; current partial candle is only 0.05x.
    exchange = _Exchange(
        closes=[102.5] * 20 + [100.0, 100.0],
        volumes=[1.0] * 20 + [2.0, 0.05],
    )

    result = _evaluate(exchange)

    assert result.volume_ratio >= 1.5
    assert result.traded is True
    assert exchange.buy_calls


def test_price_threshold_reason_precedes_volume_reason():
    # Positive move is not a dip. Low volume is secondary and must not hide the
    # real primary rejection reason in BUY AUDIT.
    exchange = _Exchange(
        closes=[100.0] * 21 + [101.0],
        volumes=[1.0] * 21 + [0.01],
    )

    result = _evaluate(exchange)

    assert result.traded is False
    assert "Waiting for dip" in result.reason
    assert not exchange.buy_calls
