"""
Resumable suite runner for AlphaTrade backtests in a constrained sandbox.

WHY THIS EXISTS (sandbox limits, not a new engine):
  • Each shell call is capped at ~120s and background jobs are reaped between
    calls, so the built-in serial fetch (~0.9s/page → ~230s for 180d of 1m) and
    the full 9-cell suite (~560s of compute) cannot run in one shot.
  • This wrapper (a) fetches klines in PARALLEL (page windows computed upfront)
    into the SAME cache files backtest.py expects, and (b) runs ONE symbol×period
    cell at a time, appending metrics to a single results JSON so progress
    survives across calls.

It does NOT change the decision/exit engine — it calls backtest.run_symbol /
backtest.metrics / backtest.walk_forward directly (faithful replay).
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import backtest

RESULTS = os.path.join(backtest.REPORTS_DIR, "suite_results.json")
HOSTS = backtest.DATA_HOSTS
PAGE = 1000
MIN_MS = 60_000  # 1m candle


def _fetch_page(host: str, symbol: str, end_ms: int, retries: int = 4) -> list:
    url = (f"{host}/api/v3/klines?symbol={symbol}&interval=1m"
           f"&limit={PAGE}&endTime={end_ms}")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return []


def _pick_host(symbol: str) -> str:
    end_ms = int(time.time() * 1000)
    for h in HOSTS:
        if _fetch_page(h, symbol, end_ms, retries=1):
            return h
    raise RuntimeError(f"no reachable data host for {symbol}")


def parallel_fetch(symbol: str, days: int, workers: int = 10) -> pd.DataFrame:
    """Fetch `days` of 1m klines concurrently; write backtest's cache file."""
    cache = os.path.join(backtest.DATA_DIR, f"{symbol}_1m_{days}d.csv")
    if os.path.exists(cache):
        age_h = (time.time() - os.path.getmtime(cache)) / 3600
        if age_h < 12:
            df = pd.read_csv(cache)
            print(f"  [cache] {symbol} {days}d {len(df)} candles ({age_h:.1f}h)")
            return df
    os.makedirs(backtest.DATA_DIR, exist_ok=True)
    want = days * 24 * 60
    pages = (want // PAGE) + 2
    now_ms = int(time.time() * 1000)
    step = PAGE * MIN_MS
    end_times = [now_ms - k * step for k in range(pages)]
    host = _pick_host(symbol)
    print(f"  [pfetch] {symbol} {days}d {pages} pages × {workers} workers from {host}…")
    t0 = time.time()
    rows: list = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_page, host, symbol, et): et for et in end_times}
        for f in as_completed(futs):
            rows.extend(f.result() or [])
    if not rows:
        raise RuntimeError(f"no candles fetched for {symbol}")
    seen = {}
    for k in rows:
        seen[int(k[0])] = k
    rows = [seen[t] for t in sorted(seen)]
    df = pd.DataFrame({
        "open_time": [int(k[0]) for k in rows],
        "open":  [float(k[1]) for k in rows],
        "high":  [float(k[2]) for k in rows],
        "low":   [float(k[3]) for k in rows],
        "close": [float(k[4]) for k in rows],
        "volume": [float(k[5]) for k in rows],
    })
    df = df.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)
    if len(df) > want:
        df = df.iloc[-want:].reset_index(drop=True)

    # Continuity guard: a dropped interior page would leave a silent time gap
    # that the +2 page overfetch could otherwise mask. Detect gaps, refetch the
    # affected windows serially, and FAIL LOUD if any remain — never write a
    # gapped cache that would quietly corrupt the backtest.
    for _ in range(4):
        d = df["open_time"].diff()
        gap_idx = d[d != MIN_MS].index[1:]  # skip first (NaN)
        if len(gap_idx) == 0:
            break
        seen = {int(t): None for t in df["open_time"]}
        for gi in gap_idx:
            prev_t = int(df["open_time"].iloc[gi - 1])
            cur_t = int(df["open_time"].iloc[gi])
            t = cur_t
            while t > prev_t:  # refetch pages covering the hole, oldest-first
                for k in _fetch_page(host, symbol, t) or []:
                    seen.setdefault(int(k[0]), k)
                t -= PAGE * MIN_MS
        rows = [v for v in seen.values() if v is not None]
        more = pd.DataFrame({
            "open_time": [int(k[0]) for k in rows],
            "open":  [float(k[1]) for k in rows],
            "high":  [float(k[2]) for k in rows],
            "low":   [float(k[3]) for k in rows],
            "close": [float(k[4]) for k in rows],
            "volume": [float(k[5]) for k in rows],
        })
        df = (pd.concat([df, more]).drop_duplicates(subset="open_time")
              .sort_values("open_time").reset_index(drop=True))
        if len(df) > want:
            df = df.iloc[-want:].reset_index(drop=True)
    final_gaps = int((df["open_time"].diff().iloc[1:] != MIN_MS).sum())
    if final_gaps:
        raise RuntimeError(f"{symbol} {days}d: {final_gaps} unfilled candle gap(s) "
                           f"after refetch — refusing to write gapped cache")
    df.to_csv(cache, index=False)
    print(f"  [ok] {symbol} {len(df)} candles in {time.time()-t0:.1f}s "
          f"({backtest._fmt_ts(int(df.open_time.iloc[0]))} → "
          f"{backtest._fmt_ts(int(df.open_time.iloc[-1]))})")
    return df


def _load_results() -> dict:
    if os.path.exists(RESULTS):
        try:
            return json.load(open(RESULTS))
        except Exception:
            pass
    return {"cells": {}}


def _save_results(d: dict):
    os.makedirs(backtest.REPORTS_DIR, exist_ok=True)
    json.dump(d, open(RESULTS, "w"), indent=2, default=str)


def _trade_to_dict(t) -> dict:
    return t.__dict__ if hasattr(t, "__dict__") else dict(t._asdict())


def _finalize(symbol, days, strategy_name, trades, candles, folds, compute_s):
    res = _load_results()
    key = f"{symbol}:{days}:{strategy_name}"
    m = backtest.metrics(trades)
    wf = backtest.walk_forward(trades, folds)
    m["walk_forward_expectancy_pct"] = [round(f.get("expectancy_pct", 0), 3) for f in wf]
    m["candles"] = candles
    m["compute_s"] = round(compute_s, 1)
    res["cells"][key] = m
    _save_results(res)
    print(f"  [done] {key}: {m['trades']} trades, "
          f"exp={m.get('expectancy_pct',0):+.3f}%/trade, "
          f"PF={m.get('profit_factor',0):.3f}, WR={m.get('win_rate',0):.1f}%, "
          f"net={m.get('sum_net_pct',0):+.1f}% in {m['compute_s']}s")
    return m


def run_cell(symbol: str, days: int, *, strategy_name: str, fee: float,
             slippage: float, sl: float, tp: float, threshold: int,
             conf_floor: int, folds: int, force: bool = False,
             half: int = 0) -> dict:
    """Compute one cell. half=0 → single pass. half∈{1,2} → resumable split
    so a single ~114s/180d replay fits inside two short shell calls."""
    res = _load_results()
    key = f"{symbol}:{days}:{strategy_name}"
    if key in res["cells"] and not force:
        print(f"  [skip] {key} already computed")
        return res["cells"][key]
    df = parallel_fetch(symbol, days)
    is_v2 = strategy_name == backtest.V2_STRATEGY
    common = dict(sl_pct=sl, tp_pct=tp, fee_pct=fee, slip_pct=slippage,
                  score_threshold=threshold, conf_floor=conf_floor,
                  strategy_name=strategy_name, allow_shorts=False, use_atr=is_v2)
    n = len(df)
    partial = os.path.join(backtest.REPORTS_DIR, f"_partial_{symbol}_{days}_{strategy_name}.json")

    if half == 0:
        t0 = time.time()
        tr = backtest.run_symbol(df, symbol, **common)
        return _finalize(symbol, days, strategy_name, tr, n, folds, time.time() - t0)

    mid = (max(backtest.WARMUP, 200) if is_v2 else backtest.WARMUP) + \
          (n - (max(backtest.WARMUP, 200) if is_v2 else backtest.WARMUP)) // 2
    if half == 1:
        t0 = time.time()
        tr1, next_i = backtest.run_symbol(df, symbol, scan_entry_limit=mid,
                                          return_next=True, **common)
        json.dump({"trades": [_trade_to_dict(t) for t in tr1], "next_i": next_i,
                   "candles": n}, open(partial, "w"), default=str)
        print(f"  [half1] {key}: {len(tr1)} trades, resume@{next_i}/{n} "
              f"in {time.time()-t0:.1f}s")
        return {}
    # half == 2
    if not os.path.exists(partial):
        raise RuntimeError(f"half1 partial missing for {key}; run --half 1 first")
    p = json.load(open(partial))
    t0 = time.time()
    tr2 = backtest.run_symbol(df, symbol, scan_start=p["next_i"], **common)
    trades = [backtest.Trade(**t) for t in p["trades"]] + tr2
    m = _finalize(symbol, days, strategy_name, trades, p["candles"], folds,
                  time.time() - t0)
    os.remove(partial)
    return m


REVERSAL_BASELINE_TPD = 48.0  # ~trades/day/symbol of the Reversal Scalper suite


def report(strategy_name: str, symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
           periods=(30, 90, 180)) -> None:
    """Print the metrics table + AUTOMATIC ACCEPT/REJECT verdict for one strategy.

    Rule (operator spec): a cell — and the strategy as a whole — is REJECTED if
    Profit Factor < 1 OR expectancy/trade < 0 (after Binance fees + slippage).
    Only a strategy that is positive on EVERY cell is ACCEPTED.
    """
    res = _load_results()
    cells = res.get("cells", {})
    print("=" * 78)
    print(f"AUTO-VALIDATION REPORT — strategy={strategy_name!r}")
    print("  PASS rule: Profit Factor >= 1 AND expectancy/trade > 0 (after fees)")
    print("=" * 78)
    header = (f"{'cell':<16}{'trades':>7}{'win%':>7}{'PF':>8}"
              f"{'exp%/t':>9}{'maxDD%':>9}{'gross%':>9}{'fees%':>9}"
              f"{'net%':>9}  verdict")
    print(header)
    print("-" * len(header))

    all_pass = True
    any_cell = False
    total_trades = 0
    for days in periods:
        for sym in symbols:
            key = f"{sym}:{days}:{strategy_name}"
            m = cells.get(key)
            label = f"{sym[:3]} {days}d"
            if not m:
                print(f"{label:<16}{'—  not computed yet':>40}")
                all_pass = False
                continue
            any_cell = True
            t = int(m.get("trades", 0))
            total_trades += t
            if t == 0:
                # No trades = strategy never qualified = no proven edge = REJECT.
                print(f"{label:<16}{0:>7}{'—':>7}{'—':>8}{'—':>9}{'—':>9}"
                      f"{'—':>9}{'—':>9}{'—':>9}  ❌ REJECT (no trades)")
                all_pass = False
                continue
            pf = m.get("profit_factor", 0.0)
            exp = m.get("expectancy_pct", 0.0)
            ok = (pf >= 1.0) and (exp > 0.0)
            all_pass = all_pass and ok
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            verdict = "✅ pass" if ok else "❌ REJECT"
            print(f"{label:<16}{t:>7}{m.get('win_rate',0):>7.1f}{pf_s:>8}"
                  f"{exp:>+9.3f}{m.get('max_drawdown_pct',0):>9.2f}"
                  f"{m.get('sum_gross_pct',0):>+9.1f}{m.get('sum_fees_pct',0):>9.1f}"
                  f"{m.get('sum_net_pct',0):>+9.1f}  {verdict}")

    print("=" * 78)
    if not any_cell:
        print("VERDICT: ⚠️  no cells computed — run the suite first.")
        return
    # Trade-frequency reduction vs the Reversal Scalper baseline.
    n_cells = len(symbols) * len(periods)
    total_days = sum(periods) * len(symbols)
    tpd = (total_trades / total_days) if total_days else 0.0
    reduction = (1 - tpd / REVERSAL_BASELINE_TPD) * 100 if REVERSAL_BASELINE_TPD else 0.0
    print(f"Trade frequency: {tpd:.2f} trades/day/symbol  vs Reversal baseline "
          f"≈{REVERSAL_BASELINE_TPD:.0f}  →  {reduction:.1f}% FEWER trades "
          f"(target was ≥90% fewer)")
    if all_pass:
        print("VERDICT: 🟢 ACCEPTED — positive expectancy AND PF≥1 after fees on "
              "every cell.\n         Necessary, NOT sufficient: forward-test tiny "
              "before trusting it.")
    else:
        print("VERDICT: 🔴 REJECTED — fails the rule (PF<1 or expectancy<0, or no "
              "trades) on\n         at least one cell. Strategy must NOT be trusted "
              "with real money as-is.")
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", default="",
                    help="comma list of SYMBOL:DAYS, e.g. BTCUSDT:30,ETHUSDT:30")
    ap.add_argument("--report", default="",
                    help="print ACCEPT/REJECT verdict for the named strategy and exit")
    ap.add_argument("--strategy", default="Reversal Scalper")
    ap.add_argument("--fee", type=float, default=0.1)
    ap.add_argument("--slippage", type=float, default=0.02)
    ap.add_argument("--sl", type=float, default=backtest._DEF_SL)
    ap.add_argument("--tp", type=float, default=backtest._DEF_TP)
    ap.add_argument("--threshold", type=int, default=50)
    ap.add_argument("--conf-floor", type=int, default=30)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--half", type=int, default=0, choices=[0, 1, 2],
                    help="0=single pass; 1/2=resumable split for one cell")
    args = ap.parse_args()

    if args.report:
        report(args.report)
        return

    cells = []
    for c in args.cells.split(","):
        c = c.strip()
        if not c:
            continue
        sym, d = c.split(":")
        cells.append((sym.strip().upper(), int(d)))

    for sym, days in cells:
        print(f"\n▶ {sym} {days}d  [{args.strategy}]")
        run_cell(sym, days, strategy_name=args.strategy, fee=args.fee,
                 slippage=args.slippage, sl=args.sl, tp=args.tp,
                 threshold=args.threshold, conf_floor=args.conf_floor,
                 folds=args.folds, force=args.force, half=args.half)


if __name__ == "__main__":
    main()
