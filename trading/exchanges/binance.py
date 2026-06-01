"""Binance LIVE Mainnet implementation. Authenticated client REQUIRED for orders."""
from typing import Dict, List
import pandas as pd

from .base import Exchange
from binance_client import (
    BinanceClient, public_klines, public_price, public_24h, extract_fill,
    extract_fees,
)


class BinanceExchange(Exchange):
    name = "binance"

    _SUPPORTED_QUOTES = ("USDT", "BUSD", "USDC")

    def __init__(self, client: BinanceClient | None = None):
        """client: authenticated BinanceClient.
        For chart-only / pre-connect display, client may be None — but every
        order method will raise. There is NO paper fallback.
        """
        self.client = client

    # ── Market data ──────────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> float:
        if self.client:
            try:
                return self.client.get_symbol_price(symbol)
            except Exception as e:
                print(f"[BINANCE-EX] auth price failed, falling back to public: {e}",
                      flush=True)
        return public_price(symbol)

    def get_klines(self, symbol: str, interval: str = "5m",
                   limit: int = 150) -> pd.DataFrame:
        if self.client:
            try:
                return self.client.get_klines(symbol, interval, limit=limit)
            except Exception as e:
                print(f"[BINANCE-EX] auth klines failed, falling back to public: {e}",
                      flush=True)
        return public_klines(symbol, interval, limit=limit)

    def get_24h(self, symbol: str) -> Dict:
        return public_24h(symbol)

    # ── Account ──────────────────────────────────────────────────────────────
    def get_balance(self, asset: str = "USDT") -> Dict[str, float]:
        if not self.client:
            raise RuntimeError(
                f"BinanceExchange.get_balance({asset}) called without an "
                "authenticated client — connect a LIVE Binance API key first."
            )
        return self.client.get_account_balance(asset)

    def get_positions(self) -> List[Dict]:
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

    # ── Symbol asset parsing ─────────────────────────────────────────────────
    def _quote_asset(self, symbol: str) -> str:
        for q in self._SUPPORTED_QUOTES:
            if symbol.endswith(q):
                return q
        return "USDT"

    def _base_asset(self, symbol: str) -> str:
        q = self._quote_asset(symbol)
        return symbol[:-len(q)] if symbol.endswith(q) else symbol

    _STABLES = ("USDT", "BUSD", "USDC", "FDUSD", "TUSD", "DAI")

    def _fee_to_usdt(self, fees: dict, symbol: str, fill_price: float) -> float:
        """Convert Binance commissions (grouped by asset) into ONE USDT figure.

        • quote/stablecoin commission → taken as-is.
        • base-coin commission        → × the fill price (its USDT value).
        • any other asset (e.g. BNB)  → × its live public USDT price.
        A conversion that can't be priced is skipped (better to under-report a
        tiny BNB fee than to crash a recorded trade)."""
        if not fees:
            return 0.0
        base = self._base_asset(symbol)
        total = 0.0
        for asset, amt in fees.items():
            try:
                amt = float(amt)
            except (TypeError, ValueError):
                continue
            if amt <= 0:
                continue
            if asset in self._STABLES:
                total += amt
            elif asset == base:
                total += amt * float(fill_price or 0)
            else:
                try:
                    total += amt * public_price(f"{asset}USDT")
                except Exception:
                    pass
        return total

    # ── Orders (REAL) ────────────────────────────────────────────────────────
    def _normalize_order(self, raw: dict, symbol: str, side: str,
                         qty: float, fallback_price: float) -> Dict:
        # Always derive qty + price from the actual Binance response.
        # extract_fill raises if Binance returned no executable fill data,
        # which is treated as an order failure (NEVER fall back to ticker).
        exec_qty, exec_price = extract_fill(raw)
        fee_detail = extract_fees(raw)
        fee_usdt   = self._fee_to_usdt(fee_detail, symbol, exec_price)
        return {
            "ok": True,
            "exchange": self.name,
            "symbol": symbol,
            "side": side,
            "qty": exec_qty,
            "price": exec_price,
            "fee": fee_usdt,           # REAL commission converted to USDT
            "fee_detail": fee_detail,  # raw per-asset commissions
            "raw": raw,
        }

    def place_buy_order(self, symbol: str, quote_amount: float) -> Dict:
        if not self.client:
            raise RuntimeError(
                f"BinanceExchange.place_buy_order({symbol}) refused — no "
                "authenticated client. LIVE orders require a connected API key."
            )
        price = self.get_price(symbol)
        qty   = self.round_quantity(symbol, quote_amount / price)
        print(f"[BINANCE-EX] LIVE BUY {symbol} qty={qty} (~${quote_amount:.2f} @ ${price:.4f})",
              flush=True)
        try:
            raw = self.client.place_market_order(symbol, "BUY", qty)
            return self._normalize_order(raw, symbol, "BUY", qty, price)
        except Exception as e:
            print(f"[BINANCE-EX][ERROR] BUY failed: {e}", flush=True)
            return {"ok": False, "exchange": self.name, "symbol": symbol,
                    "side": "BUY", "qty": qty, "error": str(e)}

    def place_buy_order_qty(self, symbol: str, qty: float) -> Dict:
        """MARKET BUY by exact base qty — used for deterministic short closes."""
        if not self.client:
            raise RuntimeError(
                f"BinanceExchange.place_buy_order_qty({symbol}) refused — no "
                "authenticated client. LIVE orders require a connected API key."
            )
        qty = self.round_quantity(symbol, qty)
        price = self.get_price(symbol)
        print(f"[BINANCE-EX] LIVE BUY (qty) {symbol} qty={qty} @ ~${price:.4f}",
              flush=True)
        try:
            raw = self.client.place_market_order(symbol, "BUY", qty)
            return self._normalize_order(raw, symbol, "BUY", qty, price)
        except Exception as e:
            print(f"[BINANCE-EX][ERROR] BUY(qty) failed: {e}", flush=True)
            return {"ok": False, "exchange": self.name, "symbol": symbol,
                    "side": "BUY", "qty": qty, "error": str(e)}

    def place_sell_order(self, symbol: str, qty: float) -> Dict:
        if not self.client:
            raise RuntimeError(
                f"BinanceExchange.place_sell_order({symbol}) refused — no "
                "authenticated client. LIVE orders require a connected API key."
            )
        qty = self.round_quantity(symbol, qty)
        price = self.get_price(symbol)
        print(f"[BINANCE-EX] LIVE SELL {symbol} qty={qty} @ ~${price:.4f}", flush=True)
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

    # ── Fees ─────────────────────────────────────────────────────────────────
    def get_fees(self, symbol: str) -> Dict[str, float]:
        return {"maker": 0.0010, "taker": 0.0010}

    def supports(self, symbol: str) -> bool:
        return any(symbol.endswith(q) for q in self._SUPPORTED_QUOTES)
