"""Multi-exchange abstraction layer.

Public surface:
    from exchanges import Exchange, register, get, all_exchanges, best_exchange_for
    from exchanges.binance import BinanceExchange
"""
from .base import Exchange
from .registry import register, get, all_exchanges, best_exchange_for, clear

__all__ = [
    "Exchange", "register", "get", "all_exchanges",
    "best_exchange_for", "clear",
]
