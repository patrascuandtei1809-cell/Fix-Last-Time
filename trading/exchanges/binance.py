"""Binance implementation of the Exchange interface.

Wraps the existing binance_client.BinanceClient (auth) and the public REST
helpers (public_klines / public_price). Falls back to public when no auth.
"""
from typing import Dict, List, Optional
import pandas as pd

from .base import Exchange
from binance_client import (
    BinanceClient, public_klines, public_price, public_24h,
)


class BinanceExchange(Exchange):
    name = "binance"

    # Symbols Binance is allowed to trade. None = all supported.
    _SUPPORTED_QUOTES = ("USDT", "BUSD", "USDC")

    def __init__(self, client: Optional[BinanceClient] = None,
                 testnet: bool = False):
        """
        client: authenticated BinanceClient, or None for public-only mode.
        testnet: True = use testnet endpoints for public data too.
        """
        self.client = client
        self.testnet = bool(testnet if client is None else client.testnet)

    # ── Market data ──────────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> float:
        if self.client:
            try:
                return self.client.get_symbol_price(symbol)
            except Exception as e:
                print(f"[BINANCE-EX] auth price failed, falling back to public: {e}",
                      flush=True)
        return public_price(symbol, testnet=self.testnet)

    def get_klines(self, symbol: str, interval: str = "5m",
                   limit: int = 150) -> pd.DataFrame:
        if self.client:
            try:
                return self.client.get_klines(symbol, interval, limit=limit)
            except Exception as e:
                print(f"[BINANCE-EX] auth klines failed, falling back to public: {e}",
                      flush=True)
        return public_klines(symbol, interval, limit=limit, testnet=self.testnet)

    def get_24h(self, symbol: str) -> Dict:
        return public_24h(symbol, testnet=self.testnet)

    # ── Account ──────────────────────────────────────────────────────────────
    def get_balance(self, asset: str = "USDT") -> Dict[str, float]:
        if not self.client:
            # No auth = no real balance. Return zeros so callers handle it.
            return {"asset": asset, "free": 0.0, "locked": 0.0,
                    "total": 0.0, "testnet": self.testnet}
        return self.client.get_account_balance(asset)

    def get_positions(self) -> List[Dict]:
        """Spot 'positions' = any non-zero non-quote asset balance."""
        if not self.client:
            return []
        try:
            balances = self.client.get_all_balances()
            return [
                {"asset": k, "qty": v["total"], "free": v["free"],
                 "locked": v["locked"]}
                for k, v in balances.items()
                if k not in ("USDT", "BUSD", "USDC") and v["total"] > 0
            ]
        except Exception as e:
            print(f"[BINANCE-EX][ERROR] get_positions failed: {e}", flush=True)
            return []

    # ── Orders ───────────────────────────────────────────────────────────────
    def _normalize_order(self, raw: dict, symbol: str, side: str,
                         qty: float, fallback_price: float) -> Dict:
        """Convert Binance order response → uniform shape used by SymbolWorker."""
        fills = raw.get("fills") or [{}]
        fill_price = float(fills[0].get("price", fallback_price))
        return {
            "ok": True,
            "exchange": self.name,
            "symbol": symbol,
            "side": side,
            "qty": float(raw.get("executedQty", qty)),
            "price": fill_price,
            "raw": raw,
        }

    def place_buy_order(self, symbol: str, quote_amount: float) -> Dict:
        if not self.client:
            return {"ok": False, "exchange": self.name, "symbol": symbol,
                    "side": "BUY", "error": "no auth client (paper only)"}
        price = self.get_price(symbol)
        qty   = self.round_quantity(symbol, quote_amount / price)
        print(f"[BINANCE-EX] BUY {symbol} qty={qty} (~${quote_amount:.2f} @ ${price:.4f})",
              flush=True)
        try:
            raw = self.client.place_market_order(symbol, "BUY", qty)
            return self._normalize_order(raw, symbol, "BUY", qty, price)
        except Exception as e:
            print(f"[BINANCE-EX][ERROR] BUY failed: {e}", flush=True)
            return {"ok": False, "exchange": self.name, "symbol": symbol,
                    "side": "BUY", "qty": qty, "error": str(e)}

    def place_sell_order(self, symbol: str, qty: float) -> Dict:
        if not self.client:
            return {"ok": False, "exchange": self.name, "symbol": symbol,
                    "side": "SELL", "error": "no auth client (paper only)"}
        qty = self.round_quantity(symbol, qty)
        price = self.get_price(symbol)
        print(f"[BINANCE-EX] SELL {symbol} qty={qty} @ ~${price:.4f}", flush=True)
        try:
            raw = self.client.place_market_order(symbol, "SELL", qty)
            return self._normalize_order(raw, symbol, "SELL", qty, price)
        except Exception as e:
            print(f"[BINANCE-EX][ERROR] SELL failed: {e}", flush=True)
            return {"ok": False, "exchange": self.name, "symbol": symbol,
                    "side": "SELL", "qty": qty, "error": str(e)}

    # ── Symbol filters ───────────────────────────────────────────────────────
    def get_symbol_filters(self, symbol: str) -> Dict:
        if not self.client:
            return {"step_size": 0.000001, "min_notional": 10.0, "min_qty": 0.0}
        try:
            info = self.client.get_symbol_info(symbol)
            step  = 0.000001
            minn  = 10.0
            minq  = 0.0
            for f in info.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    minq = float(f.get("minQty", 0))
                elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                    minn = float(f.get("minNotional") or f.get("notional") or 10)
            return {"step_size": step, "min_notional": minn, "min_qty": minq}
        except Exception:
            return {"step_size": 0.000001, "min_notional": 10.0, "min_qty": 0.0}

    def round_quantity(self, symbol: str, qty: float) -> float:
        if self.client:
            try:
                return self.client.round_quantity(symbol, qty)
            except Exception:
                pass
        return round(qty, 6)

    # ── Fees (smart routing input) ───────────────────────────────────────────
    def get_fees(self, symbol: str) -> Dict[str, float]:
        """Binance spot default: 0.10% maker/taker. Could be fetched live."""
        return {"maker": 0.0010, "taker": 0.0010}

    def supports(self, symbol: str) -> bool:
        return any(symbol.endswith(q) for q in self._SUPPORTED_QUOTES)
