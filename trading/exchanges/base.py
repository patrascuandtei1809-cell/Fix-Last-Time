"""Common interface every exchange implementation must satisfy.

Designed so smart routing (compare prices/fees across exchanges) can be added
later without touching SymbolWorker or the orchestrator.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import pandas as pd


class Exchange(ABC):
    """Abstract exchange. Concrete impls live in exchanges/<name>.py."""

    name: str = "unknown"   # human-readable id, e.g. "binance"
    testnet: bool = False   # sandbox vs production

    # ── Market data ──────────────────────────────────────────────────────────
    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """Latest ticker price for symbol (e.g. 'BTCUSDT')."""

    @abstractmethod
    def get_klines(self, symbol: str, interval: str = "5m",
                   limit: int = 150) -> pd.DataFrame:
        """OHLCV candles. Returns columns: open_time, open, high, low, close, volume."""

    # ── Account ──────────────────────────────────────────────────────────────
    @abstractmethod
    def get_balance(self, asset: str = "USDT") -> Dict[str, float]:
        """Return {'asset','free','locked','total'} for one asset."""

    @abstractmethod
    def get_positions(self) -> List[Dict]:
        """Open positions across all symbols (spot: non-zero asset balances)."""

    # ── Orders ───────────────────────────────────────────────────────────────
    @abstractmethod
    def place_buy_order(self, symbol: str, quote_amount: float) -> Dict:
        """Market BUY worth `quote_amount` USDT. Returns normalized order dict
        with at minimum: {ok, symbol, side, qty, price, raw}."""

    @abstractmethod
    def place_sell_order(self, symbol: str, qty: float) -> Dict:
        """Market SELL of `qty` base asset. Returns normalized order dict."""

    # ── Symbol filters (for qty rounding, min notional) ──────────────────────
    @abstractmethod
    def get_symbol_filters(self, symbol: str) -> Dict:
        """Return {step_size, min_notional, min_qty}. Used for sizing."""

    @abstractmethod
    def round_quantity(self, symbol: str, qty: float) -> float:
        """Round qty to the exchange's allowed step size."""

    # ── Routing inputs (future smart routing) ────────────────────────────────
    @abstractmethod
    def get_fees(self, symbol: str) -> Dict[str, float]:
        """Return {'maker': float, 'taker': float} as decimals, e.g. 0.001."""

    def supports(self, symbol: str) -> bool:
        """Whether this exchange can trade `symbol`. Override for filtering."""
        return True

    # ── Connection health ────────────────────────────────────────────────────
    def healthcheck(self) -> tuple:
        """Return (ok: bool, message: str). Default tries a price fetch."""
        try:
            self.get_price("BTCUSDT")
            return True, f"{self.name} reachable"
        except Exception as e:
            return False, f"{self.name} unreachable: {e}"

    def __repr__(self) -> str:
        return f"<Exchange name={self.name} testnet={self.testnet}>"
