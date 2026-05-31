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
except Exception:
    _DEF_SL, _DEF_TP = 0.4, 0.8

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
    ret_pct: float      # net of fees + slippage, as fraction (0.01 = +1%)


WARMUP = 60   # candles needed before the indicators are trustworthy


def run_symbol(df_raw: pd.DataFrame, symbol: str, *,
               sl_pct: float, tp_pct: float,
               fee_pct: float, slip_pct: float,
               score_threshold: int, conf_floor: int,
               strategy_name: str, allow_shorts: bool) -> List[Trade]:
    """Replay the bot over one symbol's candles. Returns closed trades."""
    # Precompute indicators ONCE on the full series (EMA/RSI/MACD are recursive
    # and need full history); slicing afterward yields identical values to
    # computing on each prefix, but O(N) instead of O(N^2).
    df = strategy.get_indicators(df_raw).reset_index(drop=True)
    n = len(df)
    fee = fee_pct / 100.0
    slip = slip_pct / 100.0

    trades: List[Trade] = []
    i = WARMUP
    while i < n - 1:
        # Decision uses candles up to and INCLUDING i (fully closed). A short
        # tail slice is enough because indicators are precomputed; the rolling
        # windows inside the engine only look back ~20 bars.
        sl_slice = df.iloc[max(0, i - WARMUP):i + 1]
        try:
            regime, _ = market_regime.classify_regime(sl_slice)
            sig, score, _bd, veto = strategy.weighted_decision(
                sl_slice, ai_signal="HOLD", ai_confidence=0, regime=regime)
            rsig, _rr, rconf = strategy.get_signal(sl_slice, strategy_name)
        except Exception:
            i += 1
            continue

        # Match bot.py qualify (signal/score/confidence from the SAME candidate):
        # the confidence OR-path only applies when the rule signal agrees with
        # the weighted signal — otherwise opposite-side rule confidence could
        # wrongly qualify a trade.
        qualified = (
            veto == "" and sig in ("BUY", "SELL") and score > 0
            and (score >= score_threshold
                 or (rsig == sig and rconf >= conf_floor))
        )
        if not qualified:
            i += 1
            continue
        if sig == "SELL" and not allow_shorts:
            i += 1
            continue

        # Enter at NEXT candle's open (no look-ahead).
        entry_idx = i + 1
        raw_entry = float(df["open"].iloc[entry_idx])
        is_long = sig == "BUY"
        entry_fill = raw_entry * (1 + slip) if is_long else raw_entry * (1 - slip)

        # Risk levels anchored to the actual FILL (entry_fill), matching the
        # live worker which checks SL/TP/BE against trade["entry_price"].
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
                if (not be_armed) and hi >= be_arm:
                    be_armed = True
                if post_entry:
                    red_count = red_count + 1 if cl < op else 0
            else:  # short (informational)
                eff_sl = entry_fill if be_armed else sl_price
                if hi >= eff_sl:
                    exit_raw = eff_sl; reason = "BE/SL" if be_armed else "SL"; exit_idx = j; break
                if lo <= tp_price:
                    exit_raw = tp_price; reason = "TP"; exit_idx = j; break
                if (not be_armed) and lo <= be_arm:
                    be_armed = True
                if post_entry:
                    red_count = red_count + 1 if cl > op else 0

            if post_entry and red_count >= AS_MAX_RED_AFTER_ENTRY:
                exit_raw = cl; reason = f"{AS_MAX_RED_AFTER_ENTRY}-red exit"; exit_idx = j; break
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
        ret = gross - 2 * fee  # taker fee each side, as fraction of notional

        trades.append(Trade(
            symbol=symbol, side=sig,
            entry_ts=int(df["open_time"].iloc[entry_idx]),
            exit_ts=int(df["open_time"].iloc[exit_idx]),
            entry_price=entry_fill, exit_price=exit_fill,
            bars_held=exit_idx - entry_idx, reason=reason, ret_pct=ret,
        ))
        # Resume scanning AFTER the trade closes (one position at a time/symbol).
        i = exit_idx + 1

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────
def metrics(trades: List[Trade]) -> Dict:
    if not trades:
        return {"trades": 0}
    rets = np.array([t.ret_pct for t in trades], dtype=float)
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
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

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
            strategy_name=args.strategy, allow_shorts=args.allow_shorts)
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
