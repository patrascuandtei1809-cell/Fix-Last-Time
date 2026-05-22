"""Common interface every exchange implementation must satisfy.

LIVE-only — no testnet, no paper, no simulation.
"""
from abc import ABC, abstractmethod
from typing import List, Dict
import pandas as pd


class Exchange(ABC):
    """Abstract exchange. Concrete impls live in exchanges/<name>.py."""

    name: str = "unknown"   # human-readable id, e.g. "binance"

    # ── Market data ──────────────────────────────────────────────────────────
    @abstractmethod
    def get_price(self, symbol: str) -> float: ...

    @abstractmethod
    def get_klines(self, symbol: str, interval: str = "5m",
                   limit: int = 150) -> pd.DataFrame: ...

    # ── Account ──────────────────────────────────────────────────────────────
    @abstractmethod
    def get_balance(self, asset: str = "USDT") -> Dict[str, float]: ...

    @abstractmethod
    def get_positions(self) -> List[Dict]: ...

    # ── Orders ───────────────────────────────────────────────────────────────
    @abstractmethod
    def place_buy_order(self, symbol: str, quote_amount: float) -> Dict: ...

    @abstractmethod
    def place_sell_order(self, symbol: str, qty: float) -> Dict: ...

    @abstractmethod
    def place_buy_order_qty(self, symbol: str, qty: float) -> Dict:
        """MARKET BUY of an exact BASE quantity (used to close short positions
        deterministically — no slippage on the quote conversion)."""
        ...

    # ── Symbol filters ───────────────────────────────────────────────────────
    @abstractmethod
    def get_symbol_filters(self, symbol: str) -> Dict: ...

    @abstractmethod
    def round_quantity(self, symbol: str, qty: float) -> float: ...

    # ── Routing inputs (future smart routing) ────────────────────────────────
    @abstractmethod
    def get_fees(self, symbol: str) -> Dict[str, float]: ...

    def supports(self, symbol: str) -> bool:
        return True

    def healthcheck(self) -> tuple:
        try:
            self.get_price("BTCUSDT")
            return True, f"{self.name} reachable"
        except Exception as e:
            return False, f"{self.name} unreachable: {e}"

    def __repr__(self) -> str:
        return f"<Exchange name={self.name} LIVE>"
