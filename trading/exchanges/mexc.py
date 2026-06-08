"""MEXC Spot LIVE implementation — added ALONGSIDE Binance (never replaces it).

Design goals (operator-specified):
  • REAL MEXC Spot execution, compatible with the existing ``Exchange`` ABC so
    ``create_bot()`` / ``SymbolWorker`` / the live engines can drive it unchanged.
  • DRY-RUN by default. Order methods SIMULATE a fill (no network, no keys
    needed) until ``live_orders=True`` is explicitly passed — one switch.
  • Order-level safety baked in (close to execution, not just in the scanner):
      - cap each BUY to ``max_quote_usdt`` (default 2 USDT),
      - refuse BUY if free USDT < ``min_usdt_balance`` (default 5),
      - refuse BUY on dangerous markets (24h range > ``max_volatility_pct`` or
        24h change < ``min_24h_change_pct``).
  • Withdrawals are NEVER used. Only price/klines/account/order endpoints.
  • API keys are NEVER printed (masked to 6 chars like the Binance path).

MEXC differences from Binance that are handled here:
  • Klines come back as 8-column rows ([t,o,h,l,c,v,close_t,quote_v]).
  • ``priceChangePercent`` on /ticker/24hr is a FRACTION (0.05 = 5%), so we ×100
    to normalise to the percent convention used everywhere else in the app.
  • MARKET BUY spends a quote amount via ``quoteOrderQty``; MARKET SELL uses
    ``quantity`` (base qty).
  • Min order value is typically ~1 USDT (vs Binance 10).
  • Interval ``1h`` must be sent as ``60m``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

import pandas as pd
import requests

from .base import Exchange

_BASE = "https://api.mexc.com"
_RECV_WINDOW = 5000

# Simulated free USDT returned by a DRY-RUN MexcExchange that has no
# authenticated client, so scanner-routed MEXC opportunities can be evaluated
# and logged as simulated (paper) trades instead of being skipped. Has NO effect
# once real MEXC creds are saved (live balance is used) or live_orders is ON.
SIM_DRY_RUN_USDT = 1000.0

# Credentials live next to the Binance creds, same 0600 discipline.
_CREDS_PATH = Path(__file__).resolve().parent.parent / "data" / ".mexc_creds.json"

# MEXC kline interval names differ slightly from Binance.
_INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "60m", "60m": "60m", "4h": "4h",
    "1d": "1d", "1w": "1W", "1W": "1W", "1M": "1M",
}

_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume",
]


# ── Credentials (secure, never logged in full) ───────────────────────────────
def load_mexc_credentials() -> Optional[Tuple[str, str]]:
    """Return (api_key, api_secret) from data/.mexc_creds.json, else None.

    Refuses group/other-readable perms (tightens to 0600), mirroring the
    Binance secrets store. The JSON may use either {api_key,api_secret} or
    {apiKey,secretKey} (some MEXC tooling writes the latter)."""
    if not _CREDS_PATH.exists():
        return None
    try:
        st_mode = _CREDS_PATH.stat().st_mode
        if st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            try:
                os.chmod(_CREDS_PATH, 0o600)
            except OSError:
                pass
        with _CREDS_PATH.open("r") as f:
            data = json.load(f)
        key = (data.get("api_key") or data.get("apiKey") or "").strip()
        sec = (data.get("api_secret") or data.get("secretKey")
               or data.get("secret") or "").strip()
        if not key or not sec:
            return None
        return key, sec
    except Exception as e:  # noqa: BLE001
        print(f"[MEXC-CREDS][ERROR] failed to load credentials: {e}", flush=True)
        return None


def save_mexc_credentials(api_key: str, api_secret: str) -> None:
    """Persist MEXC creds atomically with 0600 perms (owner read/write only)."""
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret are required")
    _CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"api_key": api_key, "api_secret": api_secret}
    tmp = _CREDS_PATH.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    os.replace(tmp, _CREDS_PATH)
    try:
        os.chmod(_CREDS_PATH, 0o600)
    except OSError:
        pass
    print(f"[MEXC-CREDS] saved MEXC credentials (key={api_key[:6]}…) "
          f"mode=600", flush=True)


def has_saved_mexc_credentials() -> bool:
    return _CREDS_PATH.exists()


# ── Public helpers (no auth) ─────────────────────────────────────────────────
def _mexc_interval(interval: str) -> str:
    return _INTERVAL_MAP.get(interval, interval)


def _klines_to_df(rows: list) -> pd.DataFrame:
    """Build the canonical OHLCV DataFrame from MEXC 8-col kline rows.

    A ``trades`` column is added (zeros) so downstream code that expects the
    Binance shape never KeyErrors."""
    norm = [r[:8] for r in rows if len(r) >= 8]
    df = pd.DataFrame(norm, columns=_KLINE_COLS)
    df["open_time"] = (pd.to_datetime(df["open_time"], unit="ms", utc=True)
                       .dt.tz_convert("Europe/London").dt.tz_localize(None))
    df["close_time"] = (pd.to_datetime(df["close_time"], unit="ms", utc=True)
                        .dt.tz_convert("Europe/London").dt.tz_localize(None))
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["trades"] = 0
    df = (df.drop_duplicates(subset="open_time")
            .sort_values("open_time")
            .reset_index(drop=True))
    return df


def public_price(symbol: str) -> float:
    r = requests.get(f"{_BASE}/api/v3/ticker/price",
                     params={"symbol": symbol}, timeout=8)
    r.raise_for_status()
    return float(r.json()["price"])


def public_klines(symbol: str, interval: str = "5m",
                  limit: int = 200) -> pd.DataFrame:
    """OHLCV candles via MEXC public REST. MEXC caps /klines at 1000 rows;
    we paginate backward via ``endTime`` for deeper history."""
    rows: list = []
    remaining = max(1, int(limit))
    end_time = None
    miv = _mexc_interval(interval)
    while remaining > 0:
        chunk = min(1000, remaining)
        params = {"symbol": symbol, "interval": miv, "limit": chunk}
        if end_time is not None:
            params["endTime"] = end_time
        r = requests.get(f"{_BASE}/api/v3/klines", params=params, timeout=10)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows = batch + rows
        remaining -= len(batch)
        end_time = int(batch[0][0]) - 1
        if len(batch) < chunk:
            break
    if not rows:
        raise RuntimeError(f"MEXC public klines returned no data for {symbol}")
    return _klines_to_df(rows)


def public_24h(symbol: Optional[str] = None):
    """24h stats. With ``symbol`` → one normalised dict; without → raw list of
    all symbols (used by the scanner). ``change_pct`` is normalised to PERCENT
    (MEXC reports it as a fraction)."""
    params = {"symbol": symbol} if symbol else None
    r = requests.get(f"{_BASE}/api/v3/ticker/24hr", params=params, timeout=12)
    r.raise_for_status()
    d = r.json()
    if symbol is None:
        return d
    return {
        "price":        float(d.get("lastPrice") or 0),
        "change_pct":   float(d.get("priceChangePercent") or 0) * 100.0,
        "high":         float(d.get("highPrice") or 0),
        "low":          float(d.get("lowPrice") or 0),
        "volume":       float(d.get("volume") or 0),
        "quote_volume": float(d.get("quoteVolume") or 0),
    }


# ── Authenticated client ─────────────────────────────────────────────────────
class MexcClient:
    """Signed MEXC Spot client. HMAC-SHA256, header X-MEXC-APIKEY.

    Only account + order + market-data endpoints are used. Withdrawal endpoints
    are never called. The secret is held in memory only and never printed."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self._session = requests.Session()
        self._session.headers.update({"X-MEXC-APIKEY": api_key})
        self._info_cache: Dict[str, dict] = {}
        print(f"[MEXC] LIVE client created — api_key_prefix={api_key[:6]}… "
              f"endpoint=api.mexc.com", flush=True)

    # ── signing ──
    def _sign(self, params: dict) -> str:
        query = urlencode(params)
        sig = hmac.new(self.api_secret.encode(), query.encode(),
                       hashlib.sha256).hexdigest()
        return f"{query}&signature={sig}"

    def _signed_request(self, method: str, path: str, params: dict) -> dict:
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = _RECV_WINDOW
        url = f"{_BASE}{path}?{self._sign(params)}"
        r = self._session.request(method, url, timeout=12)
        if r.status_code >= 400:
            # MEXC returns {"code":..,"msg":..}; surface msg without secrets.
            try:
                body = r.json()
                raise RuntimeError(f"MEXC {path} {r.status_code}: "
                                   f"{body.get('msg') or body}")
            except ValueError:
                raise RuntimeError(f"MEXC {path} {r.status_code}: {r.text[:200]}")
        return r.json()

    # ── connection test ──
    def test_connection(self) -> Tuple[bool, str]:
        try:
            self._signed_request("GET", "/api/v3/account", {})
            return True, "MEXC connected — account reachable"
        except Exception as e:  # noqa: BLE001
            return False, f"MEXC connection error: {e}"

    # ── market data ──
    def get_symbol_price(self, symbol: str) -> float:
        return public_price(symbol)

    def get_klines(self, symbol: str, interval: str = "5m",
                   limit: int = 200) -> pd.DataFrame:
        return public_klines(symbol, interval, limit=limit)

    # ── account ──
    def get_account_balance(self, asset: str = "USDT") -> dict:
        acct = self._signed_request("GET", "/api/v3/account", {})
        for b in acct.get("balances", []):
            if b.get("asset") == asset:
                free = float(b.get("free") or 0)
                locked = float(b.get("locked") or 0)
                return {"asset": asset, "free": free, "locked": locked,
                        "total": free + locked}
        return {"asset": asset, "free": 0.0, "locked": 0.0, "total": 0.0}

    def get_all_balances(self) -> dict:
        acct = self._signed_request("GET", "/api/v3/account", {})
        out: dict = {}
        for b in acct.get("balances", []):
            free = float(b.get("free") or 0)
            locked = float(b.get("locked") or 0)
            if free + locked > 0:
                out[b["asset"]] = {"free": free, "locked": locked,
                                   "total": free + locked}
        return out

    # ── symbol info / precision ──
    def get_symbol_info(self, symbol: str) -> dict:
        if symbol in self._info_cache:
            return self._info_cache[symbol]
        r = self._session.get(f"{_BASE}/api/v3/exchangeInfo",
                              params={"symbol": symbol}, timeout=12)
        r.raise_for_status()
        syms = r.json().get("symbols", [])
        info = syms[0] if syms else {}
        self._info_cache[symbol] = info
        return info

    # ── orders (REAL) ──
    def place_market_buy_quote(self, symbol: str, quote_amount: float) -> dict:
        """MARKET BUY spending an exact USDT amount (quoteOrderQty)."""
        return self._signed_request("POST", "/api/v3/order", {
            "symbol": symbol, "side": "BUY", "type": "MARKET",
            "quoteOrderQty": quote_amount,
        })

    def place_market_sell_qty(self, symbol: str, qty: float) -> dict:
        """MARKET SELL of an exact base quantity."""
        return self._signed_request("POST", "/api/v3/order", {
            "symbol": symbol, "side": "SELL", "type": "MARKET",
            "quantity": qty,
        })

    def place_market_buy_qty(self, symbol: str, qty: float) -> dict:
        """MARKET BUY of an exact base quantity (deterministic short close)."""
        return self._signed_request("POST", "/api/v3/order", {
            "symbol": symbol, "side": "BUY", "type": "MARKET",
            "quantity": qty,
        })


# ── Exchange adapter ─────────────────────────────────────────────────────────
class MexcExchange(Exchange):
    name = "mexc"

    _SUPPORTED_QUOTES = ("USDT", "USDC")
    _STABLES = ("USDT", "USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "USDD")

    def __init__(
        self,
        client: Optional[MexcClient] = None,
        live_orders: bool = False,          # DRY-RUN unless explicitly enabled
        max_quote_usdt: float = 2.0,        # cap each BUY (operator default)
        min_usdt_balance: float = 5.0,      # refuse BUY below this free USDT
        max_volatility_pct: float = 120.0,  # refuse BUY on >120% 24h range
        min_24h_change_pct: float = -35.0,  # refuse BUY on <-35% 24h change
    ):
        self.client = client
        self.live_orders = bool(live_orders)
        self.max_quote_usdt = float(max_quote_usdt)
        self.min_usdt_balance = float(min_usdt_balance)
        self.max_volatility_pct = float(max_volatility_pct)
        self.min_24h_change_pct = float(min_24h_change_pct)
        mode = "LIVE-ORDERS" if self.live_orders else "DRY-RUN"
        print(f"[MEXC-EX] MexcExchange created — mode={mode} "
              f"max_quote={self.max_quote_usdt} min_bal={self.min_usdt_balance}",
              flush=True)

    # ── market data ──
    def get_price(self, symbol: str) -> float:
        return public_price(symbol)

    def get_klines(self, symbol: str, interval: str = "5m",
                   limit: int = 150) -> pd.DataFrame:
        return public_klines(symbol, interval, limit=limit)

    def get_24h(self, symbol: str) -> Dict:
        return public_24h(symbol)

    # ── account ──
    def get_balance(self, asset: str = "USDT") -> Dict[str, float]:
        if not self.client:
            # DRY-RUN with no MEXC creds: return a deterministic SIMULATED wallet
            # so scanner-routed MEXC opportunities can still be evaluated and
            # logged as simulated (paper) trades instead of being silently
            # skipped at the balance gate. Only USDT carries simulated funds.
            if not self.live_orders:
                free = float(SIM_DRY_RUN_USDT) if asset.upper() == "USDT" else 0.0
                return {"free": free, "locked": 0.0, "total": free}
            raise RuntimeError(
                f"MexcExchange.get_balance({asset}) called without an "
                "authenticated client — save MEXC keys first.")
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
                if k not in self._STABLES and v["total"] > 0
            ]
        except Exception as e:  # noqa: BLE001
            print(f"[MEXC-EX][ERROR] get_positions failed: {e}", flush=True)
            return []

    # ── asset parsing ──
    def _quote_asset(self, symbol: str) -> str:
        for q in self._SUPPORTED_QUOTES:
            if symbol.endswith(q):
                return q
        return "USDT"

    def _base_asset(self, symbol: str) -> str:
        q = self._quote_asset(symbol)
        return symbol[:-len(q)] if symbol.endswith(q) else symbol

    # ── safety gate (BUY only) ──
    def _buy_safety_block(self, symbol: str, quote_amount: float) -> Optional[str]:
        """Return a human reason string if this BUY must be refused, else None."""
        if quote_amount < 1.0:
            return f"quote ${quote_amount:.2f} below MEXC ~$1 min notional"
        # Balance floor — only checkable with an authed client.
        if self.client:
            try:
                bal = self.client.get_account_balance("USDT")
                if bal["free"] < self.min_usdt_balance:
                    return (f"free USDT ${bal['free']:.2f} < "
                            f"${self.min_usdt_balance:.2f} floor")
            except Exception as e:  # noqa: BLE001
                return f"balance check failed: {e}"
        # Dangerous-market filter from 24h stats.
        try:
            s = public_24h(symbol)
            low = s.get("low") or 0
            vol_pct = ((s["high"] - s["low"]) / low * 100.0) if low > 0 else 0.0
            if vol_pct > self.max_volatility_pct:
                return f"24h range {vol_pct:.0f}% > {self.max_volatility_pct:.0f}%"
            if s["change_pct"] < self.min_24h_change_pct:
                return (f"24h change {s['change_pct']:.0f}% < "
                        f"{self.min_24h_change_pct:.0f}%")
        except Exception as e:  # noqa: BLE001
            return f"24h stats unavailable: {e}"
        return None

    # ── order normalisation ──
    def _extract_fill(self, raw: dict, fallback_qty: float,
                      fallback_price: float) -> Tuple[float, float]:
        try:
            exec_qty = float(raw.get("executedQty") or 0)
            quote_qty = float(raw.get("cummulativeQuoteQty") or 0)
        except (TypeError, ValueError):
            exec_qty = quote_qty = 0.0
        if exec_qty > 0 and quote_qty > 0:
            return exec_qty, quote_qty / exec_qty
        # MEXC market acks sometimes omit fills — fall back to our intent.
        return fallback_qty, fallback_price

    def _normalize(self, raw: dict, symbol: str, side: str, qty: float,
                   price: float, dry_run: bool) -> Dict:
        exec_qty, exec_price = self._extract_fill(raw, qty, price)
        notional = exec_qty * exec_price
        fee = notional * self.get_fees(symbol)["taker"]
        return {
            "ok": True,
            "exchange": self.name,
            "symbol": symbol,
            "side": side,
            "qty": exec_qty,
            "price": exec_price,
            "fee": fee,                 # estimated (MEXC acks rarely give it)
            "fee_detail": {},
            "dry_run": dry_run,
            "raw": raw,
        }

    def _fail(self, symbol: str, side: str, qty: float, reason: str) -> Dict:
        print(f"[MEXC-EX] {side} {symbol} REFUSED — {reason}", flush=True)
        return {"ok": False, "exchange": self.name, "symbol": symbol,
                "side": side, "qty": qty, "error": reason}

    def _simulate(self, symbol: str, side: str, qty: float, price: float) -> Dict:
        print(f"[MEXC-EX][DRY-RUN] {side} {symbol} qty={qty} @ ~${price:.6f} "
              f"(no order sent)", flush=True)
        return self._normalize({}, symbol, side, qty, price, dry_run=True)

    # ── orders ──
    def place_buy_order(self, symbol: str, quote_amount: float) -> Dict:
        quote_amount = min(float(quote_amount), self.max_quote_usdt)
        block = self._buy_safety_block(symbol, quote_amount)
        if block:
            return self._fail(symbol, "BUY", 0.0, block)
        price = self.get_price(symbol)
        qty = self.round_quantity(symbol, quote_amount / price)
        if not self.live_orders or not self.client:
            return self._simulate(symbol, "BUY", qty, price)
        print(f"[MEXC-EX] LIVE BUY {symbol} ~${quote_amount:.2f} @ ~${price:.6f}",
              flush=True)
        try:
            raw = self.client.place_market_buy_quote(symbol, quote_amount)
            return self._normalize(raw, symbol, "BUY", qty, price, dry_run=False)
        except Exception as e:  # noqa: BLE001
            return self._fail(symbol, "BUY", qty, str(e))

    def place_buy_order_qty(self, symbol: str, qty: float) -> Dict:
        price = self.get_price(symbol)
        qty = self.round_quantity(symbol, qty)
        if not self.live_orders or not self.client:
            return self._simulate(symbol, "BUY", qty, price)
        print(f"[MEXC-EX] LIVE BUY(qty) {symbol} qty={qty} @ ~${price:.6f}",
              flush=True)
        try:
            raw = self.client.place_market_buy_qty(symbol, qty)
            return self._normalize(raw, symbol, "BUY", qty, price, dry_run=False)
        except Exception as e:  # noqa: BLE001
            return self._fail(symbol, "BUY", qty, str(e))

    def place_sell_order(self, symbol: str, qty: float) -> Dict:
        price = self.get_price(symbol)
        qty = self.round_quantity(symbol, qty)
        if not self.live_orders or not self.client:
            return self._simulate(symbol, "SELL", qty, price)
        print(f"[MEXC-EX] LIVE SELL {symbol} qty={qty} @ ~${price:.6f}",
              flush=True)
        try:
            raw = self.client.place_market_sell_qty(symbol, qty)
            return self._normalize(raw, symbol, "SELL", qty, price, dry_run=False)
        except Exception as e:  # noqa: BLE001
            return self._fail(symbol, "SELL", qty, str(e))

    # ── symbol filters ──
    def get_symbol_filters(self, symbol: str) -> Dict:
        default = {"step_size": 0.000001, "min_notional": 1.0, "min_qty": 0.0}
        if not self.client:
            return default
        try:
            info = self.client.get_symbol_info(symbol)
            step = default["step_size"]
            minn = default["min_notional"]
            minq = default["min_qty"]
            for f in info.get("filters", []) or []:
                if f.get("filterType") == "LOT_SIZE":
                    step = float(f.get("stepSize") or step)
                    minq = float(f.get("minQty") or minq)
                elif f.get("filterType") in ("MIN_NOTIONAL", "NOTIONAL"):
                    minn = float(f.get("minNotional") or f.get("notional") or minn)
            # MEXC exposes precision/min-amount as top-level fields too.
            prec = info.get("baseAssetPrecision")
            if prec is not None:
                step = min(step, 10 ** (-int(prec)))
            if info.get("quoteAmountPrecision"):
                try:
                    minn = float(info["quoteAmountPrecision"])
                except (TypeError, ValueError):
                    pass
            return {"step_size": step, "min_notional": minn, "min_qty": minq}
        except Exception:  # noqa: BLE001
            return default

    def round_quantity(self, symbol: str, qty: float) -> float:
        filters = self.get_symbol_filters(symbol)
        step = filters.get("step_size") or 0.000001
        if step <= 0:
            return round(qty, 6)
        import math
        precision = max(0, int(round(-math.log10(step))))
        floored = math.floor(qty / step) * step
        return round(floored, precision)

    # ── fees ──
    def get_fees(self, symbol: str) -> Dict[str, float]:
        # MEXC standard spot: 0% maker / ~0.05% taker. Conservative default.
        return {"maker": 0.0, "taker": 0.0005}

    def supports(self, symbol: str) -> bool:
        return any(symbol.endswith(q) for q in self._SUPPORTED_QUOTES)
