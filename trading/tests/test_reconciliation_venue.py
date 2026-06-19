"""Regression tests for exchange-scoped ghost reconciliation."""
import sys
from types import SimpleNamespace

import diagnostics


class _Client:
    def get_all_balances(self):
        return {
            "BTC": {"free": 1.0, "locked": 0.0, "total": 1.0},
        }


class _Exchange:
    name = "binance"
    client = _Client()

    @staticmethod
    def get_symbol_filters(_symbol):
        return {"min_qty": 0.000001}


def test_binance_reconcile_never_closes_mexc_trade(monkeypatch):
    trades = [
        {
            "id": "bin-btc",
            "coin": "BTCUSDT",
            "exchange": "binance",
            "side": "BUY",
            "quantity": 0.1,
            "entry_price": 100.0,
            "status": "open",
        },
        {
            "id": "mexc-xrp",
            "coin": "XRPUSDT",
            "exchange": "mexc",
            "side": "BUY",
            "quantity": 10.0,
            "entry_price": 1.0,
            "status": "open",
        },
    ]
    closed = []
    fake_bot = SimpleNamespace(
        load_trades=lambda: trades,
        close_trade=lambda trade_id, *_args, **_kwargs: closed.append(trade_id),
        log_activity=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "bot", fake_bot)

    result = diagnostics.reconcile_ghost_trades(_Exchange())

    assert result["exchange"] == "binance"
    assert result["checked"] == 1
    assert result["ghosts"] == []
    assert closed == []


def test_binance_reconcile_closes_only_binance_ghost(monkeypatch):
    trades = [
        {
            "id": "bin-btc",
            "coin": "BTCUSDT",
            "exchange": "binance",
            "side": "BUY",
            "quantity": 0.1,
            "entry_price": 100.0,
            "status": "open",
        },
        {
            "id": "mexc-xrp",
            "coin": "XRPUSDT",
            "exchange": "mexc",
            "side": "BUY",
            "quantity": 10.0,
            "entry_price": 1.0,
            "status": "open",
        },
    ]
    closed = []

    def _close(trade_id, *_args, **_kwargs):
        closed.append(trade_id)
        return {"id": trade_id}

    fake_bot = SimpleNamespace(
        load_trades=lambda: trades,
        close_trade=_close,
        log_activity=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "bot", fake_bot)
    exchange = _Exchange()
    exchange.client = SimpleNamespace(get_all_balances=lambda: {})

    result = diagnostics.reconcile_ghost_trades(exchange)

    assert result["checked"] == 1
    assert [g["id"] for g in result["ghosts"]] == ["bin-btc"]
    assert closed == ["bin-btc"]
