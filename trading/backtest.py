"""
AlphaTrade BACKTESTER — honest edge proof.

Replays the bot's REAL decision engine and exit logic over historical Binance
candles, charging realistic fees + slippage, and reports the true edge:
win rate, expectancy, profit factor, and worst drawdown — with walk-forward
(out-of-sample) folds so a single lucky/unlucky window can't fool us.

WHY THIS EXISTS
---------------
Bigger trade size and faster execution do NOT create profit — they only matter
if the strategy has a positive *expectancy* after costs. This script measures
that expectancy on real data BEFORE any more money is risked.

WHAT IT REPLAYS (faithfully — imports the SAME code the live bot runs)
---------------------------------------------------------------------
  • Entry decision  : strategy.weighted_decision(...) (the canonical edge gate)
                      + market_regime.classify_regime(...) + strategy.get_signal(...)
                      qualified exactly like bot.py: signal in {BUY,SELL} AND
                      score>0 AND (score >= score_threshold OR rule_conf >= conf_floor).
  • Exit management : SL (stop_loss_pct), TP (take_profit_pct), breakeven arm
                      at +AS_BE_ARM_PCT, and exit on AS_MAX_RED_AFTER_ENTRY
                      consecutive red candles — same constants as symbol_worker.

HONEST LIMITATIONS (read these)
-------------------------------
  1. The live bot has an *advisory* GPT layer (ai_engine / gpt_advisor) that can
     filter a few trades. GPT is non-deterministic and costs money, so it is NOT
     replayed here. Per the bot's own spec GPT "defaults to trade" and never
     blocks the strategy, so this backtest is a touch MORE permissive (more
     trades) than live — it will not flatter the result.
  2. Binance Spot cannot SHORT. SELL-signal *entries* are not executable on spot
     without margin. The headline result is therefore LONG-ONLY (what the bot
     can actually do). Use --allow-shorts to additionally see the theoretical
     symmetric result (informational only).
  3. Fills are modelled on 1m candle OHLC with slippage; real fills can differ.
     When SL and TP both fall inside one candle we assume the STOP fills first
     (conservative / worst-case).
  4. Past performance does not predict the future. A positive backtest is
     necessary, NOT sufficient, before risking real money.

USAGE
-----
  python backtest.py                          # 30d, BTC/ETH/SOL, 1m, defaults
  python backtest.py --days 60 --symbols BTCUSDT
  python backtest.py --fee 0.1 --slippage 0.02 --threshold 50 --folds 4
  python backtest.py --allow-shorts
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import json
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

# Import the SAME engine the live bot runs (faithful replay).
import strategy
import market_regime

# Exit constants — mirror symbol_worker so the replay matches live behavior.
try:
    from symbol_worker import AS_BE_ARM_PCT, AS_MAX_RED_AFTER_ENTRY
except Exception:
    AS_BE_ARM_PCT = 0.20
    AS_MAX_RED_AFTER_ENTRY = 2

# Risk defaults — mirror risk.SymbolRiskSettings.
try:
    from risk import SymbolRiskSettings
    _DEF_SL = SymbolRiskSettings().stop_loss_pct
    _DEF_TP = SymbolRiskSettings().take_profit_pct
    _ATR_SL_MULT = SymbolRiskSettings().atr_sl_mult
    _ATR_TP_MULT = SymbolRiskSettings().atr_tp_mult
except Exception:
    _DEF_SL, _DEF_TP = 0.4, 0.8
    _ATR_SL_MULT, _ATR_TP_MULT = 1.5, 3.0

# V2 (EMA_MACD_RSI_VOLUME_V2) uses ATR-based SL/TP instead of fixed-% — mirrors
# the live worker, which enables ATR stops for this strategy.
V2_STRATEGY = "EMA_MACD_RSI_VOLUME_V2"

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest")
# Public Binance market-data mirror — reachable even where api.binance.com is
# geo-blocked (HTTP 451). Same kline schema as the live exchange. binance.us is
# a fallback with the same /api/v3/klines contract.
DATA_HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.us",
]


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────
def _fetch_klines_page(host: str, symbol: str, interval: str,
                       end_ms: int, limit: int = 1000) -> list:
    url = (f"{host}/api/v3/klines?symbol={symbol}&interval={interval}"
           f"&limit={limit}&endTime={end_ms}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def fetch_klines(symbol: str, interval: str, days: int,
                 use_cache: bool = True) -> pd.DataFrame:
    """Fetch `days` of klines, paginating backward. Cached to CSV per request."""
    os.makedirs(DATA_DIR, exist_ok=True)
    cache = os.path.join(DATA_DIR, f"{symbol}_{interval}_{days}d.csv")
    if use_cache and os.path.exists(cache):
        age_h = (time.time() - os.path.getmtime(cache)) / 3600
        if age_h < 12:  # reuse recent cache
            df = pd.read_csv(cache)
            print(f"  [cache] {symbol} {interval} {len(df)} candles "
                  f"({age_h:.1f}h old)")
            return df

    want = int(days * 24 * 60 / _interval_minutes(interval))
    end_ms = int(time.time() * 1000)
    rows: list = []
    last_err = None
    host = None
    for h in DATA_HOSTS:  # pick the first reachable host
        try:
            _fetch_klines_page(h, symbol, interval, end_ms, 2)
            host = h
            break
        except Exception as e:
            last_err = e
    if host is None:
        raise RuntimeError(f"No reachable data host for {symbol}: {last_err}")
    print(f"  [fetch] {symbol} {interval} from {host} (~{want} candles)…")

    seen = set()
    while len(rows) < want:
        try:
            page = _fetch_klines_page(host, symbol, interval, end_ms, 1000)
        except Exception as e:
            print(f"  [warn] page fetch failed ({e}); stopping early")
            break
        if not page:
            break
        new = [k for k in page if k[0] not in seen]
        if not new:
            break
        for k in new:
            seen.add(k[0])
        rows.extend(new)
        end_ms = min(k[0] for k in page) - 1
        time.sleep(0.12)  # be polite to the public endpoint

    if not rows:
        raise RuntimeError(f"No candles fetched for {symbol}")

    rows.sort(key=lambda k: k[0])
    df = pd.DataFrame({
        "open_time": [int(k[0]) for k in rows],
        "open":  [float(k[1]) for k in rows],
        "high":  [float(k[2]) for k in rows],
        "low":   [float(k[3]) for k in rows],
        "close": [float(k[4]) for k in rows],
        "volume": [float(k[5]) for k in rows],
    })
    df = df.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)
    df.to_csv(cache, index=False)
    print(f"  [ok] {symbol} {len(df)} candles "
          f"({_fmt_ts(df.open_time.iloc[0])} → {_fmt_ts(df.open_time.iloc[-1])})")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Funding-rate data (alternative edge source — perpetual-swap positioning)
# ─────────────────────────────────────────────────────────────────────────────
# Binance futures (fapi.binance.com) is geo-blocked from Replit (HTTP 451), so
# we source 8h funding history from OKX's public market-data API, which IS
# reachable and serves per-asset SWAP funding. We pair OKX perp funding (the
# market-wide positioning signal) with the Binance spot candles the bot actually
# trades. Funding is a market-wide sentiment read, so the cross-venue pairing is
# acceptable for this exploratory study (and noted honestly in the verdict).
OKX_FUNDING_HOST = "https://www.okx.com"
# Binance spot symbol → OKX SWAP instrument id.
_OKX_INST = {
    "BTCUSDT": "BTC-USDT-SWAP",
    "ETHUSDT": "ETH-USDT-SWAP",
    "SOLUSDT": "SOL-USDT-SWAP",
}


def _okx_inst_id(symbol: str) -> str:
    if symbol in _OKX_INST:
        return _OKX_INST[symbol]
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    return f"{base}-USDT-SWAP"


def fetch_funding_rates(symbol: str, days: int,
                        use_cache: bool = True) -> pd.DataFrame:
    """Fetch ~`days` of settled 8h funding rates from OKX, paginating backward.

    Returns a DataFrame [funding_time(ms), funding_rate(fraction per 8h)] sorted
    oldest→newest. Cached to CSV per (symbol, days). The rate is the realized
    8h funding; `funding_time` is when it SETTLED (so using it for candles whose
    open_time ≥ funding_time introduces no look-ahead).
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    inst = _okx_inst_id(symbol)
    cache = os.path.join(DATA_DIR, f"funding_{symbol}_{days}d.csv")
    if use_cache and os.path.exists(cache):
        age_h = (time.time() - os.path.getmtime(cache)) / 3600
        if age_h < 12:
            df = pd.read_csv(cache)
            print(f"  [cache] funding {symbol} {len(df)} rates ({age_h:.1f}h old)")
            return df

    cutoff_ms = int((time.time() - days * 86400) * 1000)
    rows: list = []
    after = ""  # OKX cursor: returns records OLDER than this fundingTime
    print(f"  [fetch] funding {inst} from OKX (~{days}d)…")
    while True:
        url = (f"{OKX_FUNDING_HOST}/api/v5/public/funding-rate-history"
               f"?instId={inst}&limit=100")
        if after:
            url += f"&after={after}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.loads(r.read())
        except Exception as e:
            print(f"  [warn] funding page fetch failed ({e}); stopping early")
            break
        data = payload.get("data") or []
        if not data:
            break
        for d in data:
            rows.append((int(d["fundingTime"]), float(d["fundingRate"])))
        oldest = min(int(d["fundingTime"]) for d in data)
        after = str(oldest)
        if oldest <= cutoff_ms or len(data) < 100:
            break
        time.sleep(0.12)  # be polite to the public endpoint

    if not rows:
        raise RuntimeError(f"No funding rates fetched for {inst}")
    df = (pd.DataFrame(rows, columns=["funding_time", "funding_rate"])
            .drop_duplicates(subset="funding_time")
            .sort_values("funding_time")
            .reset_index(drop=True))
    df = df[df["funding_time"] >= cutoff_ms].reset_index(drop=True)
    df.to_csv(cache, index=False)
    print(f"  [ok] funding {inst} {len(df)} rates "
          f"({_fmt_ts(int(df.funding_time.iloc[0]))} → "
          f"{_fmt_ts(int(df.funding_time.iloc[-1]))})")
    return df


def merge_funding(df: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """As-of merge the most-recent SETTLED funding rate onto each candle.

    Adds a `funding` column (fraction per 8h). Uses merge_asof backward on
    open_time so a candle only ever sees funding that had ALREADY settled at or
    before its open — no look-ahead. Candles before the first funding obs get
    NaN (the signal treats NaN as "no data" → HOLD).
    """
    out = df.copy()
    if funding is None or len(funding) == 0:
        out["funding"] = np.nan
        return out
    left = out.sort_values("open_time").reset_index(drop=True)
    right = (funding.rename(columns={"funding_time": "open_time",
                                     "funding_rate": "funding"})
                    .sort_values("open_time").reset_index(drop=True))
    merged = pd.merge_asof(left, right[["open_time", "funding"]],
                           on="open_time", direction="backward")
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Basis & cross-exchange spread data (alternative edge sources)
# ─────────────────────────────────────────────────────────────────────────────
# Both probes need an auxiliary CLOSE-price series aligned to the Binance spot
# candles the bot trades:
#   • BASIS    → OKX perpetual-SWAP close (perp price) vs Binance spot close.
#   • XSPREAD  → OKX spot close (second venue) vs Binance spot close.
# Binance futures (fapi) is geo-blocked from Replit (451); OKX market candles ARE
# reachable and serve both SWAP and SPOT history. OKX returns candles newest→
# oldest and paginates backward with `after` (records OLDER than the cursor).
_OKX_BAR = {"5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1H", "2h": "2H", "4h": "4H", "1d": "1D"}


def _okx_bar(interval: str) -> str:
    if interval not in _OKX_BAR:
        raise ValueError(f"OKX has no candle bar for interval {interval!r}")
    return _OKX_BAR[interval]


def fetch_okx_candles(symbol: str, interval: str, days: int, kind: str,
                      use_cache: bool = True) -> pd.DataFrame:
    """Fetch ~`days` of OKX close prices, paginating backward.

    `kind` ∈ {"SWAP","SPOT"} selects the OKX instrument (e.g. BTC-USDT-SWAP vs
    BTC-USDT). Returns a DataFrame [open_time(ms), okx_close] sorted oldest→
    newest. Cached to CSV per (symbol, interval, days, kind).
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    inst = f"{base}-USDT-SWAP" if kind == "SWAP" else f"{base}-USDT"
    bar = _okx_bar(interval)
    cache = os.path.join(DATA_DIR, f"okx_{kind.lower()}_{symbol}_{interval}_{days}d.csv")
    if use_cache and os.path.exists(cache):
        age_h = (time.time() - os.path.getmtime(cache)) / 3600
        if age_h < 12:
            df = pd.read_csv(cache)
            print(f"  [cache] okx {kind} {symbol} {interval} {len(df)} candles "
                  f"({age_h:.1f}h old)")
            return df

    cutoff_ms = int((time.time() - days * 86400) * 1000)
    want = int(days * 24 * 60 / _interval_minutes(interval))
    rows: list = []
    seen = set()
    after = ""  # OKX cursor: returns records OLDER than this ts
    print(f"  [fetch] okx {kind} {inst} {bar} from OKX (~{want} candles)…")
    while len(rows) < want:
        url = (f"{OKX_FUNDING_HOST}/api/v5/market/history-candles"
               f"?instId={inst}&bar={bar}&limit=300")
        if after:
            url += f"&after={after}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.loads(r.read())
        except Exception as e:
            print(f"  [warn] okx candle page fetch failed ({e}); stopping early")
            break
        data = payload.get("data") or []
        if not data:
            break
        new = [d for d in data if int(d[0]) not in seen]
        if not new:
            break
        for d in new:
            ts = int(d[0])
            seen.add(ts)
            rows.append((ts, float(d[4])))  # d[4] = close
        oldest = min(int(d[0]) for d in data)
        after = str(oldest)
        if oldest <= cutoff_ms or len(data) < 300:
            break
        time.sleep(0.12)  # be polite to the public endpoint

    if not rows:
        raise RuntimeError(f"No OKX candles fetched for {inst}")
    df = (pd.DataFrame(rows, columns=["open_time", "okx_close"])
            .drop_duplicates(subset="open_time")
            .sort_values("open_time")
            .reset_index(drop=True))
    df = df[df["open_time"] >= cutoff_ms].reset_index(drop=True)
    df.to_csv(cache, index=False)
    print(f"  [ok] okx {kind} {inst} {len(df)} candles "
          f"({_fmt_ts(int(df.open_time.iloc[0]))} → "
          f"{_fmt_ts(int(df.open_time.iloc[-1]))})")
    return df


def _merge_aux_ratio(df: pd.DataFrame, aux: pd.DataFrame,
                     col: str) -> pd.DataFrame:
    """As-of merge an OKX aux close onto each candle and compute the fractional
    spread `(aux_close − spot_close) / spot_close` as `col`.

    Uses merge_asof backward on open_time so a candle only ever sees an aux
    close at or before its own open boundary — no look-ahead. Both series are
    candles on the SAME interval/UTC boundaries, so the as-of match is normally
    the exact same timestamp; a missing aux bar falls back to the most recent
    prior one. Candles before the first aux obs (or where spot/aux is non-finite)
    get NaN → the signal treats NaN as "no data" → HOLD.
    """
    out = df.copy()
    if aux is None or len(aux) == 0:
        out[col] = np.nan
        return out
    left = out.sort_values("open_time").reset_index(drop=True)
    right = aux[["open_time", "okx_close"]].sort_values("open_time").reset_index(drop=True)
    merged = pd.merge_asof(left, right, on="open_time", direction="backward")
    spot = pd.to_numeric(merged["close"], errors="coerce")
    okx = pd.to_numeric(merged["okx_close"], errors="coerce")
    ratio = (okx - spot) / spot
    merged[col] = ratio.where(np.isfinite(ratio), np.nan)
    return merged.drop(columns=["okx_close"])


def merge_basis(df: pd.DataFrame, perp: pd.DataFrame) -> pd.DataFrame:
    """Add a `basis` column = (perp_close − spot_close)/spot_close (perp
    premium/discount as a fraction), no look-ahead."""
    return _merge_aux_ratio(df, perp, "basis")


def merge_xspread(df: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
    """Add an `xspread` column = (other_venue_close − spot_close)/spot_close
    (cross-exchange price gap as a fraction), no look-ahead."""
    return _merge_aux_ratio(df, other, "xspread")


def _interval_minutes(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    return n * {"m": 1, "h": 60, "d": 1440}.get(unit, 1)


def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# ─────────────────────────────────────────────────────────────────────────────
# Backtest engine
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    symbol: str
    side: str           # BUY (long) | SELL (short, informational)
    entry_ts: int
    exit_ts: int
    entry_price: float  # fill incl. slippage
    exit_price: float   # fill incl. slippage
    bars_held: int
    reason: str
    ret_pct: float      # NET of fees + slippage, as fraction (0.01 = +1%)
    gross_pct: float = 0.0  # GROSS price move (pre-fee), as fraction
    fee_pct: float = 0.0    # round-trip fee charged, as fraction (both sides)
    regime: str = ""        # market regime at entry (for attribution)


WARMUP = 60   # candles needed before the indicators are trustworthy


def run_symbol(df_raw: pd.DataFrame, symbol: str, *,
               sl_pct: float, tp_pct: float,
               fee_pct: float, slip_pct: float,
               score_threshold: int, conf_floor: int,
               strategy_name: str, allow_shorts: bool,
               use_atr: bool = False,
               atr_sl_mult: float = _ATR_SL_MULT,
               atr_tp_mult: float = _ATR_TP_MULT,
               arm_be: bool = True,
               max_red: int = AS_MAX_RED_AFTER_ENTRY,
               qualify_mode: str = "auto",
               block_regimes: tuple = ("RANGE", "DEAD"),
               warmup_bars: int = None,
               scan_start: int = None, scan_entry_limit: int = None,
               return_next: bool = False):
    """Replay the bot over one symbol's candles. Returns closed trades.

    The optional `scan_start` / `scan_entry_limit` / `return_next` arguments
    exist ONLY to let a constrained sandbox split one long replay across
    several short shell calls. Because the replay holds no cross-trade state
    (the outer loop simply resumes at `exit_idx + 1`), restricting the range of
    candles on which NEW entries are scanned, then resuming from the returned
    index, produces the exact same trade list as a single full pass. When all
    three are left at their defaults the behaviour is byte-identical to before.
    """
    # Precompute indicators ONCE on the full series (EMA/RSI/MACD are recursive
    # and need full history); slicing afterward yields identical values to
    # computing on each prefix, but O(N) instead of O(N^2).
    df = strategy.get_indicators(df_raw).reset_index(drop=True)
    n = len(df)
    fee = fee_pct / 100.0
    slip = slip_pct / 100.0
    # V2 is a standalone confirmation strategy — it qualifies on its OWN signal
    # (EMA50>EMA200 + MACD hist + RSI + volume), not the weighted scalper gate,
    # and uses ATR-based SL/TP. V2 needs ≥200 bars for EMA200.
    is_v2 = strategy_name == V2_STRATEGY
    # Self-qualify (use the strategy's OWN get_signal, like V2) vs weighted gate.
    use_signal_qualify = (qualify_mode == "signal") or (qualify_mode == "auto" and is_v2)
    if warmup_bars is not None:
        warmup = max(WARMUP, int(warmup_bars))
    else:
        warmup = max(WARMUP, 200) if is_v2 else WARMUP

    trades: List[Trade] = []
    i = warmup if scan_start is None else max(warmup, scan_start)
    entry_cap = (n - 1) if scan_entry_limit is None else min(scan_entry_limit, n - 1)
    while i < n - 1:
        if i >= entry_cap:  # stop scanning NEW entries past the assigned range
            break
        # Decision uses candles up to and INCLUDING i (fully closed). A short
        # tail slice is enough because indicators are precomputed; the rolling
        # windows inside the engine only look back ~20 bars (V2 needs 200 for
        # EMA200).
        sl_slice = df.iloc[max(0, i - warmup):i + 1]
        try:
            if use_signal_qualify:
                # Self-qualify on the strategy's OWN signal — no weighted gate.
                # Plus a REGIME gate (default: never open in RANGE or DEAD).
                sig, _rr, rconf = strategy.get_signal(sl_slice, strategy_name)
                regime, _ = market_regime.classify_regime(sl_slice)
                qualified = (sig in ("BUY", "SELL") and rconf > 0
                             and (not block_regimes or regime not in block_regimes))
            else:
                regime, _ = market_regime.classify_regime(sl_slice)
                sig, score, _bd, veto = strategy.weighted_decision(
                    sl_slice, ai_signal="HOLD", ai_confidence=0, regime=regime)
                rsig, _rr, rconf = strategy.get_signal(sl_slice, strategy_name)
                # Match bot.py qualify (signal/score/confidence from the SAME
                # candidate): the confidence OR-path only applies when the rule
                # signal agrees with the weighted signal — otherwise opposite-side
                # rule confidence could wrongly qualify a trade.
                qualified = (
                    veto == "" and sig in ("BUY", "SELL") and score > 0
                    and (score >= score_threshold
                         or (rsig == sig and rconf >= conf_floor))
                )
        except Exception:
            i += 1
            continue

        if not qualified:
            i += 1
            continue
        if sig == "SELL" and not allow_shorts:
            i += 1
            continue

        # ATR at the decision candle (V2 ATR SL/TP). Absolute price units.
        atr_abs = 0.0
        if use_atr:
            try:
                atr_abs = float(df["atr"].iloc[i])
                if not np.isfinite(atr_abs) or atr_abs <= 0:
                    atr_abs = 0.0
            except Exception:
                atr_abs = 0.0

        # Enter at NEXT candle's open (no look-ahead).
        entry_idx = i + 1
        raw_entry = float(df["open"].iloc[entry_idx])
        is_long = sig == "BUY"
        entry_fill = raw_entry * (1 + slip) if is_long else raw_entry * (1 - slip)

        # Risk levels anchored to the actual FILL (entry_fill), matching the
        # live worker which checks SL/TP/BE against trade["entry_price"].
        # V2 with ATR available → ATR×mult distances; otherwise fixed-%.
        if use_atr and atr_abs > 0:
            sl_dist = atr_abs * atr_sl_mult
            tp_dist = atr_abs * atr_tp_mult
            sl_price = entry_fill - sl_dist if is_long else entry_fill + sl_dist
            tp_price = entry_fill + tp_dist if is_long else entry_fill - tp_dist
        else:
            sl_price = entry_fill * (1 - sl_pct / 100) if is_long else entry_fill * (1 + sl_pct / 100)
            tp_price = entry_fill * (1 + tp_pct / 100) if is_long else entry_fill * (1 - tp_pct / 100)
        be_arm = entry_fill * (1 + AS_BE_ARM_PCT / 100) if is_long else entry_fill * (1 - AS_BE_ARM_PCT / 100)

        be_armed = False
        red_count = 0
        exit_idx = None
        exit_raw = None
        reason = ""

        j = entry_idx
        while j < n:
            hi = float(df["high"].iloc[j]); lo = float(df["low"].iloc[j])
            op = float(df["open"].iloc[j]); cl = float(df["close"].iloc[j])
            # Live counts red candles on bars AFTER the entry bar (the entry bar
            # is the one we filled into mid-formation), so skip the entry candle.
            post_entry = j > entry_idx

            if is_long:
                eff_sl = entry_fill if be_armed else sl_price
                # Conservative: if both SL and TP touch in one candle, STOP wins.
                if lo <= eff_sl:
                    exit_raw = eff_sl; reason = "BE/SL" if be_armed else "SL"; exit_idx = j; break
                if hi >= tp_price:
                    exit_raw = tp_price; reason = "TP"; exit_idx = j; break
                if arm_be and (not be_armed) and hi >= be_arm:
                    be_armed = True
                if post_entry:
                    red_count = red_count + 1 if cl < op else 0
            else:  # short (informational)
                eff_sl = entry_fill if be_armed else sl_price
                if hi >= eff_sl:
                    exit_raw = eff_sl; reason = "BE/SL" if be_armed else "SL"; exit_idx = j; break
                if lo <= tp_price:
                    exit_raw = tp_price; reason = "TP"; exit_idx = j; break
                if arm_be and (not be_armed) and lo <= be_arm:
                    be_armed = True
                if post_entry:
                    red_count = red_count + 1 if cl > op else 0

            if max_red > 0 and post_entry and red_count >= max_red:
                exit_raw = cl; reason = f"{max_red}-red exit"; exit_idx = j; break
            j += 1

        if exit_idx is None:  # ran off the end — close at last close
            exit_idx = n - 1
            exit_raw = float(df["close"].iloc[exit_idx])
            reason = "end-of-data"

        exit_fill = exit_raw * (1 - slip) if is_long else exit_raw * (1 + slip)
        if is_long:
            gross = exit_fill / entry_fill - 1.0
        else:
            gross = entry_fill / exit_fill - 1.0
        fee_round_trip = 2 * fee  # taker fee each side, as fraction of notional
        ret = gross - fee_round_trip

        trades.append(Trade(
            symbol=symbol, side=sig,
            entry_ts=int(df["open_time"].iloc[entry_idx]),
            exit_ts=int(df["open_time"].iloc[exit_idx]),
            entry_price=entry_fill, exit_price=exit_fill,
            bars_held=exit_idx - entry_idx, reason=reason, ret_pct=ret,
            gross_pct=gross, fee_pct=fee_round_trip,
            regime=str(regime or ""),
        ))
        # Resume scanning AFTER the trade closes (one position at a time/symbol).
        i = exit_idx + 1

    if return_next:
        return trades, i
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────
def metrics(trades: List[Trade]) -> Dict:
    if not trades:
        return {"trades": 0}
    rets = np.array([t.ret_pct for t in trades], dtype=float)
    gross_arr = np.array([t.gross_pct for t in trades], dtype=float)
    fees_arr = np.array([t.fee_pct for t in trades], dtype=float)
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    gross_win = float(wins.sum()); gross_loss = float(-losses.sum())
    # Equity curve (compounded) for drawdown.
    equity = np.cumprod(1 + rets)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(dd.min()) if len(dd) else 0.0
    expectancy = float(rets.mean())
    std = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    sharpe = (expectancy / std * np.sqrt(len(rets))) if std > 0 else 0.0
    return {
        "trades": len(trades),
        "win_rate": float(len(wins) / len(trades) * 100),
        "avg_win_pct": float(wins.mean() * 100) if len(wins) else 0.0,
        "avg_loss_pct": float(losses.mean() * 100) if len(losses) else 0.0,
        "expectancy_pct": expectancy * 100,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "total_return_pct": float((equity[-1] - 1) * 100),
        "max_drawdown_pct": max_dd * 100,
        "sharpe": sharpe,
        "avg_bars_held": float(np.mean([t.bars_held for t in trades])),
        # Gross / fees / net split (additive sums, NOT compounded) — shows how
        # much of the raw price edge the fees eat.
        "sum_gross_pct": float(gross_arr.sum() * 100),
        "sum_fees_pct": float(fees_arr.sum() * 100),
        "sum_net_pct": float(rets.sum() * 100),
        "avg_gross_pct": float(gross_arr.mean() * 100),
        "avg_fee_pct": float(fees_arr.mean() * 100),
    }


def _fmt_metrics(m: Dict) -> str:
    if m.get("trades", 0) == 0:
        return "    (no trades)"
    pf = m["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    sign = "🟢" if m["expectancy_pct"] > 0 else "🔴"
    return (
        f"    trades={m['trades']:<5} win%={m['win_rate']:.1f}  "
        f"avgW=+{m['avg_win_pct']:.2f}% avgL={m['avg_loss_pct']:.2f}%\n"
        f"    {sign} expectancy/trade={m['expectancy_pct']:+.3f}%  "
        f"profit_factor={pf_s}  sharpe={m['sharpe']:.2f}\n"
        f"    GROSS={m.get('sum_gross_pct', 0):+.2f}%  "
        f"FEES=-{m.get('sum_fees_pct', 0):.2f}%  "
        f"NET={m.get('sum_net_pct', 0):+.2f}%  (sum across trades)\n"
        f"    total_return={m['total_return_pct']:+.2f}%  "
        f"max_drawdown={m['max_drawdown_pct']:.2f}%  "
        f"avg_hold={m['avg_bars_held']:.0f} bars"
    )


def walk_forward(trades: List[Trade], folds: int) -> List[Dict]:
    """Split trades chronologically into N folds (out-of-sample consistency)."""
    if folds <= 1 or len(trades) < folds:
        return []
    ts_sorted = sorted(trades, key=lambda t: t.entry_ts)
    size = len(ts_sorted) // folds
    out = []
    for f in range(folds):
        lo = f * size
        hi = (f + 1) * size if f < folds - 1 else len(ts_sorted)
        out.append(metrics(ts_sorted[lo:hi]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Suite — BTC/ETH/SOL × 30/90/180d, gross/fees/net, auto-saved
# ─────────────────────────────────────────────────────────────────────────────
SUITE_PERIODS = [30, 90, 180]
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "data", "backtest_reports")


def run_suite(*, symbols: List[str], interval: str, fee: float, slippage: float,
              sl: float, tp: float, threshold: int, conf_floor: int,
              strategy_name: str, allow_shorts: bool, folds: int,
              use_cache: bool) -> Dict:
    """Backtest every (symbol × period) cell, report gross/fees/net, auto-save.

    Saves a machine-readable JSON and a human-readable TXT report to
    data/backtest_reports/ so results are preserved across runs.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    is_v2 = strategy_name == V2_STRATEGY
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    lines: List[str] = []
    def _emit(s: str = ""):
        print(s)
        lines.append(s)

    _emit("=" * 72)
    _emit("AlphaTrade BACKTEST SUITE — BTC/ETH/SOL × 30/90/180d")
    _emit("=" * 72)
    _emit(f"strategy={strategy_name!r}  interval={interval}  "
          f"fee={fee}%/side  slippage={slippage}%/side")
    if is_v2:
        _emit(f"V2 ATR stops: SL=ATR×{_ATR_SL_MULT}  TP=ATR×{_ATR_TP_MULT}  "
              f"(fixed-% fallback SL={sl}% TP={tp}%)")
    else:
        _emit(f"SL={sl}%  TP={tp}%  score_threshold={threshold}  "
              f"conf_floor={conf_floor}")
    _emit(f"shorts={'ON (informational)' if allow_shorts else 'OFF (spot long-only)'}")
    _emit("-" * 72)

    suite: Dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy_name,
        "interval": interval,
        "fee_pct_per_side": fee,
        "slippage_pct_per_side": slippage,
        "use_atr": is_v2,
        "atr_sl_mult": _ATR_SL_MULT if is_v2 else None,
        "atr_tp_mult": _ATR_TP_MULT if is_v2 else None,
        "sl_pct": sl, "tp_pct": tp,
        "score_threshold": threshold, "conf_floor": conf_floor,
        "allow_shorts": allow_shorts,
        "periods": {},
    }

    for days in SUITE_PERIODS:
        _emit("")
        _emit(f"╔══ PERIOD: {days}d " + "═" * (60 - len(str(days))))
        period_trades: List[Trade] = []
        period_block: Dict = {"days": days, "symbols": {}}
        for sym in symbols:
            _emit(f"\n▶ {sym} ({days}d)")
            try:
                df = fetch_klines(sym, interval, days, use_cache=use_cache)
            except Exception as e:
                _emit(f"  [skip] data error: {e}")
                period_block["symbols"][sym] = {"trades": 0, "error": str(e)}
                continue
            min_bars = (200 if is_v2 else WARMUP) + 50
            if len(df) < min_bars:
                _emit(f"  [skip] not enough candles ({len(df)} < {min_bars})")
                period_block["symbols"][sym] = {"trades": 0,
                                                "error": "insufficient candles"}
                continue
            t0 = time.time()
            tr = run_symbol(
                df, sym, sl_pct=sl, tp_pct=tp,
                fee_pct=fee, slip_pct=slippage,
                score_threshold=threshold, conf_floor=conf_floor,
                strategy_name=strategy_name, allow_shorts=allow_shorts,
                use_atr=is_v2)
            period_trades.extend(tr)
            m = metrics(tr)
            period_block["symbols"][sym] = m
            _emit(f"  [done] {len(tr)} trades in {time.time()-t0:.1f}s")
            _emit(_fmt_metrics(m))

        pm = metrics(period_trades)
        period_block["portfolio"] = pm
        _emit(f"\n  ── {days}d PORTFOLIO (all symbols) ──")
        _emit(_fmt_metrics(pm))
        suite["periods"][str(days)] = period_block

    # ── Summary matrix ───────────────────────────────────────────────────────
    _emit("\n" + "=" * 72)
    _emit("SUMMARY — net expectancy/trade (%) per symbol × period")
    _emit("=" * 72)
    _hdr = "  symbol   " + "".join(f"{str(d)+'d':>12}" for d in SUITE_PERIODS)
    _emit(_hdr)
    for sym in symbols:
        row = f"  {sym:<9}"
        for days in SUITE_PERIODS:
            cell = suite["periods"].get(str(days), {}).get("symbols", {}).get(sym, {})
            if cell.get("trades", 0):
                row += f"{cell['expectancy_pct']:>+11.3f}%"
            else:
                row += f"{'—':>12}"
        _emit(row)

    # ── Persist ──────────────────────────────────────────────────────────────
    base = f"suite_{strategy_name.replace(' ', '_')}_{stamp}"
    json_path = os.path.join(REPORTS_DIR, base + ".json")
    txt_path = os.path.join(REPORTS_DIR, base + ".txt")
    try:
        with open(json_path, "w") as f:
            json.dump(suite, f, indent=2, default=str)
        with open(txt_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        _emit("")
        _emit(f"💾 Saved JSON → {json_path}")
        _emit(f"💾 Saved TXT  → {txt_path}")
    except Exception as e:
        _emit(f"[warn] failed to save report: {e}")

    return suite


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="AlphaTrade honest backtester")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--interval", default="1m")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--fee", type=float, default=0.1,
                    help="taker fee %% per side (Binance Spot default 0.1)")
    ap.add_argument("--slippage", type=float, default=0.02,
                    help="slippage %% per side")
    ap.add_argument("--sl", type=float, default=_DEF_SL, help="stop-loss %%")
    ap.add_argument("--tp", type=float, default=_DEF_TP, help="take-profit %%")
    ap.add_argument("--threshold", type=int, default=50,
                    help="weighted score_threshold (bot.py default 50)")
    ap.add_argument("--conf-floor", type=int, default=30,
                    help="confidence floor for the OR-path (bot.py default 30)")
    ap.add_argument("--strategy", default="Reversal Scalper")
    ap.add_argument("--folds", type=int, default=4,
                    help="walk-forward folds (out-of-sample windows)")
    ap.add_argument("--allow-shorts", action="store_true",
                    help="also trade SELL signals (NOT executable on Spot)")
    ap.add_argument("--suite", action="store_true",
                    help="run the full BTC/ETH/SOL × 30/90/180d suite "
                         "(gross/fees/net) and auto-save a JSON+TXT report")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # ── SUITE MODE — BTC/ETH/SOL × 30/90/180d, auto-saved report. ────────────
    if args.suite:
        run_suite(
            symbols=symbols, interval=args.interval,
            fee=args.fee, slippage=args.slippage, sl=args.sl, tp=args.tp,
            threshold=args.threshold, conf_floor=args.conf_floor,
            strategy_name=args.strategy, allow_shorts=args.allow_shorts,
            folds=args.folds, use_cache=not args.no_cache)
        return

    # V2 uses ATR-based SL/TP — mirror the live worker for the single-run path.
    _use_atr = args.strategy == V2_STRATEGY

    print("=" * 72)
    print("AlphaTrade BACKTEST — honest edge proof (real fees + slippage)")
    print("=" * 72)
    print(f"period={args.days}d  interval={args.interval}  "
          f"strategy={args.strategy!r}")
    print(f"SL={args.sl}%  TP={args.tp}%  BE_arm=+{AS_BE_ARM_PCT}%  "
          f"red_exit={AS_MAX_RED_AFTER_ENTRY}  score_threshold={args.threshold}")
    print(f"fee={args.fee}%/side  slippage={args.slippage}%/side  "
          f"round-trip cost≈{2*(args.fee+args.slippage):.2f}%")
    print(f"shorts={'ON (informational)' if args.allow_shorts else 'OFF (spot long-only)'}")
    print("-" * 72)

    all_trades: List[Trade] = []
    per_symbol: Dict[str, List[Trade]] = {}

    for sym in symbols:
        print(f"\n▶ {sym}")
        try:
            df = fetch_klines(sym, args.interval, args.days,
                              use_cache=not args.no_cache)
        except Exception as e:
            print(f"  [skip] data error: {e}")
            continue
        if len(df) < WARMUP + 50:
            print(f"  [skip] not enough candles ({len(df)})")
            continue
        t0 = time.time()
        tr = run_symbol(
            df, sym, sl_pct=args.sl, tp_pct=args.tp,
            fee_pct=args.fee, slip_pct=args.slippage,
            score_threshold=args.threshold, conf_floor=args.conf_floor,
            strategy_name=args.strategy, allow_shorts=args.allow_shorts,
            use_atr=_use_atr)
        per_symbol[sym] = tr
        all_trades.extend(tr)
        print(f"  [done] {len(tr)} trades in {time.time()-t0:.1f}s")
        print(_fmt_metrics(metrics(tr)))

    print("\n" + "=" * 72)
    print("PORTFOLIO (all symbols combined)")
    print("=" * 72)
    m = metrics(all_trades)
    print(_fmt_metrics(m))

    if args.folds > 1 and all_trades:
        print("\n" + "-" * 72)
        print(f"WALK-FORWARD — {args.folds} chronological out-of-sample windows")
        print("(consistency check: is the edge stable, or one lucky window?)")
        print("-" * 72)
        for idx, fm in enumerate(walk_forward(all_trades, args.folds), 1):
            print(f"  Fold {idx}:")
            print(_fmt_metrics(fm))

    # ── Verdict ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    if m.get("trades", 0) == 0:
        print("  No trades generated — cannot assess edge.")
    else:
        exp = m["expectancy_pct"]
        pf = m["profit_factor"]
        if exp > 0 and pf > 1.1:
            print(f"  🟢 POSITIVE expectancy ({exp:+.3f}%/trade, PF={pf:.2f}) on this")
            print("     window AFTER fees+slippage. Necessary but NOT sufficient:")
            print("     verify the walk-forward folds are CONSISTENTLY positive, then")
            print("     forward-test tiny before trusting it. Past ≠ future.")
        else:
            print(f"  🔴 NON-POSITIVE expectancy ({exp:+.3f}%/trade, PF={pf:.2f}) after")
            print("     fees+slippage. As-is, this strategy LOSES money over many")
            print("     trades. Increasing trade SIZE would only lose faster. The")
            print("     edge (entry/exit rules) must change before risking more.")
    print("=" * 72)


if __name__ == "__main__":
    main()
