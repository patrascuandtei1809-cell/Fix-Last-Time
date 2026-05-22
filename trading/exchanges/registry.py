"""Exchange registry — single point of lookup.

The registry is intentionally tiny. `best_exchange_for()` is the extension
point for future smart routing (compare fees / liquidity / price).
"""
from typing import Dict, List, Optional
from .base import Exchange

_exchanges: Dict[str, Exchange] = {}


def register(ex: Exchange) -> None:
    """Register an exchange. Replaces existing entry with same name."""
    _exchanges[ex.name] = ex
    print(f"[REGISTRY] registered exchange: {ex.name} testnet={ex.testnet}", flush=True)


def get(name: str) -> Optional[Exchange]:
    return _exchanges.get(name)


def all_exchanges() -> List[Exchange]:
    return list(_exchanges.values())


def clear() -> None:
    """Remove all registered exchanges (used on credential change)."""
    _exchanges.clear()


def best_exchange_for(symbol: str, side: str = "BUY") -> Optional[Exchange]:
    """Return the best exchange for this symbol/side.

    STUB for future smart routing. Today returns the first registered
    exchange that supports the symbol. Future impl will compare:
      - latest price (best bid for SELL, best ask for BUY)
      - taker fee
      - 24h volume / liquidity
      - withdrawal cost (if cross-exchange)
    """
    for ex in _exchanges.values():
        if ex.supports(symbol):
            return ex
    return None
