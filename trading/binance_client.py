import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
import math


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.client = Client(api_key, api_secret, testnet=testnet)

    def test_connection(self):
        try:
            self.client.ping()
            server_time = self.client.get_server_time()
            return True, f"Connected. Server time: {server_time['serverTime']}"
        except BinanceAPIException as e:
            return False, f"Binance API error: {e.message}"
        except Exception as e:
            return False, f"Connection error: {str(e)}"

    def get_symbol_price(self, symbol: str) -> float:
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_klines(self, symbol: str, interval: str = "5m", limit: int = 200) -> pd.DataFrame:
        raw = self.client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df

    def get_account_balance(self, asset: str = "USDT") -> float:
        account = self.client.get_account()
        for b in account["balances"]:
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def get_all_balances(self) -> dict:
        account = self.client.get_account()
        result = {}
        for b in account["balances"]:
            total = float(b["free"]) + float(b["locked"])
            if total > 0:
                result[b["asset"]] = {
                    "free": float(b["free"]),
                    "locked": float(b["locked"]),
                    "total": total
                }
        return result

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
        floored = math.floor(quantity / step) * step
        return round(floored, precision)

    def get_min_notional(self, symbol: str) -> float:
        info = self.get_symbol_info(symbol)
        for f in info["filters"]:
            if f["filterType"] == "MIN_NOTIONAL":
                return float(f["minNotional"])
        return 10.0

    def place_market_order(self, symbol: str, side: str, quantity: float) -> dict:
        return self.client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
