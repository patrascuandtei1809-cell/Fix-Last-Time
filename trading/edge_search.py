"""Edge search — drive the existing research framework across timeframes/symbols.

Goal: find ONE strategy with positive expectancy AFTER FEES. This is NOT new
infrastructure — it is a thin runner over `research.run_subcell()` /
`backtest.metrics()` that already exist. It only ADDS the requested matrix:

    timeframes 5m / 15m / 1h / 4h   ×   BTC / ETH / SOL  (each symbol separately)

and emits a single leaderboard with win rate, avg winner, avg loser, expectancy,
profit factor, Sharpe, and max drawdown — all after the same 0.1%/side fee +
0.02%/side slippage (~0.24% round-trip) used everywhere else.

Sandbox-friendly: the Replit shell caps a command at 120s and detached jobs do
NOT survive across calls, so results ACCUMULATE in a JSON file and the run can be
split across several short calls:

    python edge_search.py --tf 1h 4h                 # fast TFs, all strategies
    python edge_search.py --tf 5m --only reversal_scalper
    python edge_search.py --report                   # build leaderboard

Identical input → identical output (deterministic backtest over fixed history).
"""
from __future__ import annotations
import argparse, json, os, time
from datetime import datetime
from typing import Dict, List

import research
from research import CANDIDATES, run_subcell, DEFAULT_FEE, DEFAULT_SLIP

DATA_DIR  = research.DATA_DIR if hasattr(research, "DATA_DIR") else "data/research"
ROWS_PATH = os.path.join(DATA_DIR, "edge_rows.json")

SYMBOLS    = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAMES = ["5m", "15m", "1h", "4h"]

# Day windows per timeframe — balanced so each cell is a few thousand candles
# (fast enough for one synchronous call) while still spanning months of regime
# variety on the slower frames.
TF_DAYS = {"5m": 14, "15m": 45, "1h": 180, "4h": 360}

# A row is a "candidate" only if it traded enough to be trustworthy.
MIN_TRADES_CAND = 10


def _load_rows() -> List[Dict]:
    try:
        with open(ROWS_PATH) as f:
            return json.load(f).get("rows", [])
    except Exception:
        return []


def _save_rows(rows: List[Dict]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = ROWS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"updated_at": datetime.now().isoformat(timespec="seconds"),
                   "fee_pct": DEFAULT_FEE, "slip_pct": DEFAULT_SLIP,
                   "rows": rows}, f, indent=2)
    os.replace(tmp, ROWS_PATH)


def _key(strategy_key: str, tf: str, sym: str) -> str:
    return f"{strategy_key}|{tf}|{sym}"


def run(tfs: List[str], only: List[str] | None, fee: float, slip: float) -> None:
    specs = [s for s in CANDIDATES if (not only or s.key in only)]
    if not specs:
        print(f"no strategies matched --only {only}"); return
    rows = _load_rows()
    index = {_key(r["strategy_key"], r["tf"], r["symbol"]): i
             for i, r in enumerate(rows)}

    for spec in specs:
        for tf in tfs:
            days = TF_DAYS[tf]
            for sym in SYMBOLS:
                t0 = time.time()
                try:
                    m = run_subcell(spec, sym, tf, days, fee=fee, slip=slip)
                except Exception as e:
                    print(f"  [skip] {spec.key} {tf} {sym}: {e}", flush=True)
                    continue
                m.pop("_trades", None)
                row = {
                    "strategy_key": spec.key, "strategy": spec.name,
                    "signal_name": spec.signal_name,
                    "tf": tf, "symbol": sym, "days": days,
                    "trades": m.get("trades", 0),
                    "win_rate": m.get("win_rate", 0.0),
                    "avg_win_pct": m.get("avg_win_pct", 0.0),
                    "avg_loss_pct": m.get("avg_loss_pct", 0.0),
                    "expectancy_pct": m.get("expectancy_pct", 0.0),
                    "profit_factor": (None if m.get("profit_factor") == float("inf")
                                      else m.get("profit_factor", 0.0)),
                    "sharpe": m.get("sharpe", 0.0),
                    "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
                    "error": m.get("error", ""),
                }
                k = _key(spec.key, tf, sym)
                if k in index:
                    rows[index[k]] = row
                else:
                    index[k] = len(rows); rows.append(row)
                _save_rows(rows)  # checkpoint after every cell
                dt = time.time() - t0
                exp = row["expectancy_pct"]
                pf = row["profit_factor"]
                pf_s = "inf" if pf is None else f"{pf:.2f}"
                print(f"  {spec.key:<22} {tf:>3} {sym:<8} "
                      f"trades={row['trades']:<4} exp={exp:+.3f}% PF={pf_s:<5} "
                      f"sharpe={row['sharpe']:+.2f} maxDD={row['max_drawdown_pct']:.1f}% "
                      f"({dt:.0f}s)", flush=True)


def _pf_num(r: Dict) -> float:
    pf = r.get("profit_factor")
    return 1e9 if pf is None else float(pf)


def report() -> None:
    rows = [r for r in _load_rows() if r.get("trades", 0) > 0]
    if not rows:
        print("No results yet. Run the sweep first."); return

    rows.sort(key=lambda r: r["expectancy_pct"], reverse=True)

    print("=" * 100)
    print("FULL LEADERBOARD — every strategy × timeframe × symbol, ranked by "
          "NET expectancy/trade (after fees)")
    print(f"fee={DEFAULT_FEE}%/side  slip={DEFAULT_SLIP}%/side  "
          f"round-trip≈{2*(DEFAULT_FEE+DEFAULT_SLIP):.2f}%   "
          f"min trades for candidate={MIN_TRADES_CAND}")
    print("=" * 100)
    hdr = (f"{'#':>2}  {'strategy':<24}{'tf':>4} {'sym':<7} {'trd':>4} "
           f"{'win%':>5} {'avgW%':>7} {'avgL%':>7} {'exp%':>8} {'PF':>6} "
           f"{'Sharpe':>7} {'maxDD%':>7}  verdict")
    print(hdr); print("-" * len(hdr))
    for i, r in enumerate(rows, 1):
        pf = r.get("profit_factor")
        pf_s = "inf" if pf is None else f"{pf:.2f}"
        pos = r["expectancy_pct"] > 0 and _pf_num(r) >= 1.0
        enough = r["trades"] >= MIN_TRADES_CAND
        verdict = ("✅ CANDIDATE" if (pos and enough)
                   else "⚠️ thin" if pos else "🔴 REJECT")
        print(f"{i:>2}  {r['strategy'][:24]:<24}{r['tf']:>4} {r['symbol'][:7]:<7} "
              f"{r['trades']:>4} {r['win_rate']:>5.1f} {r['avg_win_pct']:>+7.2f} "
              f"{r['avg_loss_pct']:>+7.2f} {r['expectancy_pct']:>+8.3f} {pf_s:>6} "
              f"{r['sharpe']:>+7.2f} {r['max_drawdown_pct']:>7.1f}  {verdict}")

    cands = [r for r in rows
             if r["expectancy_pct"] > 0 and _pf_num(r) >= 1.0
             and r["trades"] >= MIN_TRADES_CAND]
    # Rank candidates by expectancy, then profit factor, then Sharpe.
    cands.sort(key=lambda r: (r["expectancy_pct"], _pf_num(r), r["sharpe"]),
               reverse=True)

    print("\n" + "=" * 100)
    print("TOP 3 CANDIDATES (positive expectancy AND PF≥1 AND "
          f"≥{MIN_TRADES_CAND} trades, after fees)")
    print("=" * 100)
    if not cands:
        print("🔴 NONE. No strategy × timeframe × symbol shows a positive "
              "after-fee expectancy on a tradable sample.")
    else:
        for i, r in enumerate(cands[:3], 1):
            pf = r.get("profit_factor")
            pf_s = "inf" if pf is None else f"{pf:.2f}"
            print(f"  #{i}  {r['strategy']} @ {r['tf']} on {r['symbol']}\n"
                  f"      exp/trade={r['expectancy_pct']:+.3f}%  PF={pf_s}  "
                  f"win%={r['win_rate']:.1f}  Sharpe={r['sharpe']:+.2f}  "
                  f"maxDD={r['max_drawdown_pct']:.1f}%  "
                  f"(avgW={r['avg_win_pct']:+.2f}% avgL={r['avg_loss_pct']:+.2f}% "
                  f"over {r['trades']} trades, {r['days']}d)")

    # Robustness note: is any strategy×tf positive on ALL THREE symbols?
    by_combo: Dict[str, List[Dict]] = {}
    for r in rows:
        by_combo.setdefault(f"{r['strategy_key']}|{r['tf']}", []).append(r)
    robust = []
    for combo, rs in by_combo.items():
        syms = {x["symbol"] for x in rs
                if x["expectancy_pct"] > 0 and _pf_num(x) >= 1.0
                and x["trades"] >= MIN_TRADES_CAND}
        if len(syms) >= 3:
            robust.append(combo)
    print("\nROBUSTNESS: " + (
        "🟢 positive on all 3 symbols → " + ", ".join(robust)
        if robust else
        "🔴 no strategy×timeframe is positive on all three symbols "
        "(any green above is a single-symbol/sample artifact, not a real edge)."))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AlphaTrade edge search")
    ap.add_argument("--tf", nargs="*", default=None,
                    help=f"timeframes to run (default all: {TIMEFRAMES})")
    ap.add_argument("--only", default=None,
                    help="comma-separated strategy keys (default: all candidates)")
    ap.add_argument("--report", action="store_true", help="build leaderboard")
    ap.add_argument("--reset", action="store_true", help="clear accumulated rows")
    ap.add_argument("--fee", type=float, default=DEFAULT_FEE)
    ap.add_argument("--slip", type=float, default=DEFAULT_SLIP)
    args = ap.parse_args()

    if args.reset:
        _save_rows([]); print("cleared edge_rows.json")
    if args.report:
        report()
    elif not args.reset:
        tfs = args.tf or TIMEFRAMES
        only = [s.strip() for s in args.only.split(",")] if args.only else None
        run(tfs, only, args.fee, args.slip)
        print("\n(use `python edge_search.py --report` for the leaderboard)")
