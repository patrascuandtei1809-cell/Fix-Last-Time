"""LIVE Binance Mainnet client only. No testnet. No paper. Real orders."""
import pandas as pd
import requests
import math
import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException

log = logging.getLogger("alphatrade.binance")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

# ── Public Binance REST (no auth needed) — LIVE Mainnet ONLY ──────────────────
# api.binance.com is the canonical LIVE host, but it is geo-blocked (HTTP 451)
# from some regions/hosts (e.g. this dev environment). data-api.binance.vision is
# Binance's OFFICIAL public market-data mirror — identical klines/prices, no auth
# — and stays reachable where the main host is blocked. ONLY read-only public
# market data uses this fallback; authenticated/order calls always go straight to
# api.binance.com via the python-binance Client (LIVE Mainnet only, no testnet).
_PUBLIC_BASES = [
    "https://api.binance.com/api/v3",
    "https://data-api.binance.vision/api/v3",
]
_PUBLIC_BASE = _PUBLIC_BASES[0]  # back-compat; primary LIVE host
_active_public_base: str | None = None


def _public_get(path: str, params: dict, timeout: int = 10):
    """GET a no-auth Binance market-data endpoint, failing over across mirrors.

    Tries the last-known-good base first, then the rest, and caches the winning
    base so a geo-blocked host isn't retried on every call. Raises RuntimeError
    only if EVERY mirror fails.
    """
    global _active_public_base
    bases = list(_PUBLIC_BASES)
    if _active_public_base and _active_public_base in bases:
        bases.remove(_active_public_base)
        bases.insert(0, _active_public_base)
    last_err = None
    for base in bases:
        try:
            r = requests.get(f"{base}{path}", params=params, timeout=timeout)
            r.raise_for_status()
            _active_public_base = base
            return r.json()
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"public GET {path} failed on all Binance mirrors: {last_err}")


def extract_fill(order: dict) -> tuple[float, float]:
    """Return (executed_qty, avg_fill_price) from a real Binance order response.

    Resolution order:
      1. Weighted average of `fills[]` entries (most accurate).
      2. cummulativeQuoteQty / executedQty (Binance always populates these for
         MARKET orders that actually filled).
    Raises RuntimeError if neither is available — callers MUST NOT fall back to
    a UI/ticker price, because that would record a non-real execution.
    """
    fills = order.get("fills") or []
    total_qty   = 0.0
    total_quote = 0.0
    for f in fills:
        try:
            q = float(f.get("qty", 0))
            p = float(f.get("price", 0))
        except (TypeError, ValueError):
            continue
        if q > 0 and p > 0:
            total_qty   += q
            total_quote += q * p
    if total_qty > 0:
        return total_qty, total_quote / total_qty
    try:
        exec_qty   = float(order.get("executedQty") or 0)
        quote_qty  = float(order.get("cummulativeQuoteQty") or 0)
    except (TypeError, ValueError):
        exec_qty = quote_qty = 0.0
    if exec_qty > 0 and quote_qty > 0:
        return exec_qty, quote_qty / exec_qty
    raise RuntimeError(
        f"Cannot extract executed fill from Binance order response: {order!r}"
    )


def extract_fees(order: dict) -> dict:
    """Return REAL Binance commissions grouped by asset from an order response.

    Binance reports a `commission` + `commissionAsset` on every `fills[]` entry.
    The asset varies: USDT (quote), the base coin (BTC/ETH/SOL), or BNB if the
    account pays fees in BNB. We sum per asset and let the caller convert to a
    single USDT figure (it needs live prices, which this low-level client does
    not fetch). Returns {} when no commission data is present (e.g. a response
    that only has cummulativeQuoteQty and no fills[]).

    Example: {"USDT": 0.0123} or {"BTC": 0.00000012, "BNB": 0.00050}.
    """
    out: dict = {}
    for f in order.get("fills") or []:
        try:
            c = float(f.get("commission", 0) or 0)
        except (TypeError, ValueError):
            continue
        a = f.get("commissionAsset")
        if c > 0 and a:
            out[a] = out.get(a, 0.0) + c
    return out


_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def _klines_to_df(rows: list) -> pd.DataFrame:
    """Build the standard OHLCV DataFrame from raw Binance kline rows."""
    df = pd.DataFrame(rows, columns=_KLINE_COLS)
    df["open_time"]  = (pd.to_datetime(df["open_time"],  unit="ms", utc=True)
                          .dt.tz_convert("Europe/London").dt.tz_localize(None))
    df["close_time"] = (pd.to_datetime(df["close_time"], unit="ms", utc=True)
                          .dt.tz_convert("Europe/London").dt.tz_localize(None))
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    # Defensive: pagination can in theory overlap at page boundaries — dedupe
    # by candle open_time and keep strictly increasing (oldest→newest) order.
    df = (df.drop_duplicates(subset="open_time")
            .sort_values("open_time")
            .reset_index(drop=True))
    return df


def public_klines(symbol: str, interval: str = "5m", limit: int = 200) -> pd.DataFrame:
    """Fetch OHLCV candles using the public REST API — no API key required.

    Binance caps each /klines request at 1000 candles. When ``limit`` exceeds
    that we paginate backwards via ``endTime`` so the chart can show thousands
    of candles (full available history), not just the last few hours.
    """
    rows: list = []
    remaining = max(1, int(limit))
    end_time = None  # most-recent first; then walk backwards
    try:
        while remaining > 0:
            chunk = min(1000, remaining)
            params = {"symbol": symbol, "interval": interval, "limit": chunk}
            if end_time is not None:
                params["endTime"] = end_time
            batch = _public_get("/klines", params, timeout=10)
            if not batch:
                break
            rows = batch + rows  # prepend older candles
            remaining -= len(batch)
            # Next page ends just before the oldest candle we just got.
            end_time = int(batch[0][0]) - 1
            if len(batch) < chunk:
                break  # no more history available
    except Exception as e:
        raise RuntimeError(f"Public klines fetch failed: {e}")
    if not rows:
        raise RuntimeError("Public klines fetch returned no data")
    return _klines_to_df(rows)


def public_price(symbol: str) -> float:
    """Get current ticker price — no API key required."""
    try:
        data = _public_get("/ticker/price", {"symbol": symbol}, timeout=8)
        return float(data["price"])
    except Exception as e:
        raise RuntimeError(f"Public price fetch failed: {e}")


def public_24h(symbol: str) -> dict:
    """24-hour stats (% change, high, low, volume) — no auth needed."""
    try:
        d = _public_get("/ticker/24hr", {"symbol": symbol}, timeout=8)
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


# ── Authenticated BinanceClient — LIVE MAINNET ONLY ───────────────────────────
class BinanceClient:
    """Authenticated client against api.binance.com. No testnet, ever."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key    = api_key
        self.api_secret = api_secret
        # Hard-coded testnet=False; we never construct a testnet client.
        self.client     = Client(api_key, api_secret, testnet=False)
        kp = (api_key or "")[:6]
        print(f"[BINANCE] LIVE client created — api_key_prefix={kp}... "
              f"endpoint=api.binance.com", flush=True)
        log.info("BinanceClient LIVE created api_key_prefix=%s... endpoint=api.binance.com", kp)

    # ── Connection test ───────────────────────────────────────────────────────
    def test_connection(self):
        try:
            self.client.ping()
            t = self.client.get_server_time()
            return True, f"Connected — server time: {t['serverTime']}"
        except BinanceAPIException as e:
            return False, f"Binance API error {e.code}: {e.message}"
        except Exception as e:
            return False, f"Connection error: {e}"

    # ── Market data ───────────────────────────────────────────────────────────
    def get_symbol_price(self, symbol: str) -> float:
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_klines(self, symbol: str, interval: str = "5m",
                   limit: int = 200) -> pd.DataFrame:
        # Binance caps each request at 1000 candles; paginate backwards via
        # endTime when more are requested (chart full-history view).
        rows: list = []
        remaining = max(1, int(limit))
        end_time = None
        while remaining > 0:
            chunk = min(1000, remaining)
            kw = dict(symbol=symbol, interval=interval, limit=chunk)
            if end_time is not None:
                kw["endTime"] = end_time
            batch = self.client.get_klines(**kw)
            if not batch:
                break
            rows = batch + rows
            remaining -= len(batch)
            end_time = int(batch[0][0]) - 1
            if len(batch) < chunk:
                break
        if not rows:
            raise RuntimeError("get_klines returned no data")
        return _klines_to_df(rows)

    # ── Account ───────────────────────────────────────────────────────────────
    def get_account_balance(self, asset: str = "USDT") -> dict:
        """Real Binance balance for one asset. Returns {asset, free, locked, total}.
        Raises RuntimeError on API failure (caller MUST handle / surface).
        """
        kp = (self.api_key or "")[:6]
        print(f"[BINANCE] Fetching LIVE balance... asset={asset} api_key_prefix={kp}...",
              flush=True)
        try:
            account = self.client.get_account()
        except BinanceAPIException as e:
            err = f"Binance API error {e.code}: {e.message}"
            print(f"[BINANCE][ERROR] {err}", flush=True)
            log.error("get_account() BinanceAPIException code=%s msg=%s", e.code, e.message)
            raise RuntimeError(err) from e
        except Exception as e:
            err = f"Binance request failed: {e}"
            print(f"[BINANCE][ERROR] {err}", flush=True)
            log.error("get_account() failed: %s", e)
            raise RuntimeError(err) from e

        bals = account.get("balances", [])
        match = next((b for b in bals if b["asset"] == asset), None)
        print(f"[BINANCE] get_account OK LIVE canTrade={account.get('canTrade')} "
              f"accountType={account.get('accountType')} assets={len(bals)} {asset}={match}",
              flush=True)
        log.info("Binance get_account OK LIVE — %d assets, canTrade=%s",
                 len(bals), account.get("canTrade"))

        if match:
            f = float(match["free"]); l = float(match["locked"])
            out = {"asset": asset, "free": f, "locked": l, "total": f + l}
            print(f"[BINANCE] {asset} free={f:.8f} locked={l:.8f} total={f+l:.8f}", flush=True)
            log.info("Balance %s: free=%.8f locked=%.8f total=%.8f", asset, f, l, f + l)
            return out

        print(f"[BINANCE][WARN] Asset {asset} not in balances", flush=True)
        log.warning("Asset %s not present in account balances", asset)
        return {"asset": asset, "free": 0.0, "locked": 0.0, "total": 0.0}

    # Back-compat alias
    def get_asset_balance_full(self, asset: str = "USDT") -> dict:
        return self.get_account_balance(asset)

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

    # ── Orders (REAL Binance Mainnet) ─────────────────────────────────────────
    def place_market_order(self, symbol: str, side: str, quantity: float) -> dict:
        return self.client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity,
        )
