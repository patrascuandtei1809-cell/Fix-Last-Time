import pandas as pd
import requests
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ── Public Binance REST (no auth needed) ──────────────────────────────────────
_PUBLIC_BASE = "https://api.binance.com/api/v3"
_TESTNET_BASE = "https://testnet.binance.vision/api/v3"


def public_klines(symbol: str, interval: str = "5m", limit: int = 200,
                  testnet: bool = False) -> pd.DataFrame:
    """Fetch OHLCV candles using the public REST API — no API key required."""
    base = _TESTNET_BASE if testnet else _PUBLIC_BASE
    try:
        r = requests.get(
            f"{base}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        raise RuntimeError(f"Public klines fetch failed: {e}")

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df["open_time"]  = (pd.to_datetime(df["open_time"],  unit="ms", utc=True)
                          .dt.tz_convert("Europe/London").dt.tz_localize(None))
    df["close_time"] = (pd.to_datetime(df["close_time"], unit="ms", utc=True)
                          .dt.tz_convert("Europe/London").dt.tz_localize(None))
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def public_price(symbol: str, testnet: bool = False) -> float:
    """Get current ticker price — no API key required."""
    base = _TESTNET_BASE if testnet else _PUBLIC_BASE
    try:
        r = requests.get(f"{base}/ticker/price",
                         params={"symbol": symbol}, timeout=8)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        raise RuntimeError(f"Public price fetch failed: {e}")


def public_24h(symbol: str, testnet: bool = False) -> dict:
    """24-hour stats (% change, high, low, volume) — no auth needed."""
    base = _TESTNET_BASE if testnet else _PUBLIC_BASE
    try:
        r = requests.get(f"{base}/ticker/24hr",
                         params={"symbol": symbol}, timeout=8)
        r.raise_for_status()
        d = r.json()
        return {
            "price":        float(d.get("lastPrice", 0)),
            "change_pct":   float(d.get("priceChangePercent", 0)),
            "high":         float(d.get("highPrice", 0)),
            "low":          float(d.get("lowPrice", 0)),
            "volume":       float(d.get("volume", 0)),
            "quote_volume": float(d.get("quoteVolume", 0)),
        }
    except Exception as e:
        raise RuntimeError(f"24h stats fetch failed: {e}")


# ── Authenticated BinanceClient (API key required) ────────────────────────────
class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.testnet    = testnet
        self.client     = Client(api_key, api_secret, testnet=testnet)

    # ── Connection test ───────────────────────────────────────────────────────
    def test_connection(self):
        try:
            self.client.ping()
            t = self.client.get_server_time()
            return True, f"Connected — server time: {t['serverTime']}"
        except BinanceAPIException as e:
            return False, f"Binance API error: {e.message}"
        except Exception as e:
            return False, f"Connection error: {e}"

    # ── Market data (uses auth client — works on testnet) ─────────────────────
    def get_symbol_price(self, symbol: str) -> float:
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_klines(self, symbol: str, interval: str = "5m",
                   limit: int = 200) -> pd.DataFrame:
        raw = self.client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        df["open_time"]  = (pd.to_datetime(df["open_time"],  unit="ms", utc=True)
                              .dt.tz_convert("Europe/London").dt.tz_localize(None))
        df["close_time"] = (pd.to_datetime(df["close_time"], unit="ms", utc=True)
                              .dt.tz_convert("Europe/London").dt.tz_localize(None))
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df

    # ── Account ───────────────────────────────────────────────────────────────
    def get_account_balance(self, asset: str = "USDT") -> float:
        account = self.client.get_account()
        for b in account["balances"]:
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def get_all_balances(self) -> dict:
        account = self.client.get_account()
        result  = {}
        for b in account["balances"]:
            total = float(b["free"]) + float(b["locked"])
            if total > 0:
                result[b["asset"]] = {
                    "free":   float(b["free"]),
                    "locked": float(b["locked"]),
                    "total":  total,
                }
        return result

    # ── Symbol info ───────────────────────────────────────────────────────────
    def get_symbol_info(self, symbol: str) -> dict:
        return self.client.get_symbol_info(symbol)

    def get_step_size(self, symbol: str) -> float:
        info = self.get_symbol_info(symbol)
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                return float(f["stepSize"])
        return 0.001

    def round_quantity(self, symbol: str, quantity: float) -> float:
        step = self.get_step_size(symbol)
        if step == 0:
            return quantity
        precision = int(round(-math.log10(step)))
        floored   = math.floor(quantity / step) * step
        return round(floored, precision)

    def get_min_notional(self, symbol: str) -> float:
        info = self.get_symbol_info(symbol)
        for f in info["filters"]:
            if f["filterType"] == "MIN_NOTIONAL":
                return float(f["minNotional"])
        return 10.0

    # ── Orders ────────────────────────────────────────────────────────────────
    def place_market_order(self, symbol: str, side: str, quantity: float) -> dict:
        return self.client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
        )
