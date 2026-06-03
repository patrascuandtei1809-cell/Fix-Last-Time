"""
research.py — Strategy Research Framework (June 2026)
====================================================

ONE job: prove (or honestly DISPROVE) a positive after-fee trading edge.

The operator asked us to STOP expanding backend/institutional infrastructure and
instead PROVE whether any strategy actually makes money after Binance fees. This
module is the answer. It is a thin research layer on top of the existing honest
backtest engine (`backtest.run_symbol`, which already models real fees +
slippage + walk-forward).

What it adds on top of the raw engine:

  1. Strategy Research Framework — a `StrategySpec` registry describing each
     candidate strategy: which signal, which timeframes, which EXIT policy
     (ATR vs fixed %, breakeven on/off, red-candle exit on/off), and how it
     qualifies (its own signal vs the weighted scalper gate).
  2. Multi-timeframe backtesting — sweeps strategy × timeframe × symbol ×
     period. 1m scalping is already proven dead after the ~0.24% round-trip
     fee, so the candidates here live on 15m / 1h / 4h where a real move can
     clear the fee hurdle.
  3. Trade attribution — every closed trade is grouped by exit reason, entry
     regime, and symbol so we can SEE where the money is made or lost.
  4. Fee-adjusted expectancy — NET expectancy/trade (after fees + slippage) is
     the PRIMARY ranking metric. Gross is shown only to expose how much the
     fees eat.
  5. Automatic ranking — a single leaderboard across all (strategy × timeframe)
     cells, sorted by net expectancy, with an ACCEPT/REJECT verdict per cell.
  6. Auto-disable gate — the ACCEPTED cells are persisted to
     data/research/validated_strategies.json. The live bot reads this allowlist
     and REFUSES to auto-trade any (strategy, timeframe) that is not on it.
     Default-safe: empty/missing allowlist → NO auto-trading at all. Manual
     trades are never affected.

HONEST by construction: if nothing clears the fee hurdle, the verdict is
REJECTED and the allowlist stays empty — the bot auto-disables itself rather
than promise an edge that does not exist.
"""

from __future__ import annotations

import os
import json
import time
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from collections import defaultdict
from typing import List, Dict, Optional, Tuple

import backtest
from backtest import Trade, run_symbol, metrics, walk_forward, fetch_klines

# ─────────────────────────────────────────────────────────────────────────────
# Paths + persistence
# ─────────────────────────────────────────────────────────────────────────────
RESEARCH_DIR   = os.path.join(os.path.dirname(__file__), "data", "research")
VALIDATED_PATH = os.path.join(RESEARCH_DIR, "validated_strategies.json")

# Cost model — mirror backtest.main() defaults so research matches the suite:
# Binance Spot taker 0.1%/side + 0.02%/side slippage → ~0.24% round-trip.
DEFAULT_FEE  = 0.1    # % per side
DEFAULT_SLIP = 0.02   # % per side

# ─────────────────────────────────────────────────────────────────────────────
# Acceptance rule (honest, strict)
# ─────────────────────────────────────────────────────────────────────────────
# A (strategy × timeframe) cell is ACCEPTED only if, after fees + slippage:
#   • every (symbol × period) sub-cell that produced ≥ MIN_TRADES trades has
#     net expectancy > 0 AND profit_factor ≥ 1.0, AND
#   • the aggregate produced ≥ MIN_TOTAL_TRADES trades (enough to be meaningful),
#     AND
#   • walk-forward holds: a majority of out-of-sample folds are positive.
# Anything else → REJECTED. No partial credit, no curve-fitting to one period.
MIN_TRADES       = 5     # per sub-cell to count toward the verdict
MIN_TOTAL_TRADES = 20    # aggregate minimum for a trustworthy verdict
WF_FOLDS         = 4     # walk-forward folds
MIN_SYMBOLS      = 2     # an edge must hold on ≥2 symbols, not one lucky coin
MIN_TRADED_FRAC  = 0.60  # ≥60% of attempted sub-cells must reach MIN_TRADES
                         # (breadth guard: stops 1 strong cell from passing while
                         #  the rest are sparse/excluded)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Research Framework — the registry
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class StrategySpec:
    """Everything the engine needs to backtest ONE candidate strategy."""
    key:         str                     # stable id (used in the allowlist)
    name:        str                     # display name
    signal_name: str                     # value passed to strategy.get_signal()
    timeframes:  List[str]               # intervals to sweep (e.g. ["1h","4h"])
    # Exit policy
    use_atr:     bool  = True            # ATR-based SL/TP (let winners run)
    sl_pct:      float = 0.4             # fixed-% fallback SL
    tp_pct:      float = 0.8             # fixed-% fallback TP
    atr_sl_mult: float = backtest._ATR_SL_MULT
    atr_tp_mult: float = backtest._ATR_TP_MULT
    arm_be:      bool  = False           # scalper breakeven snap (OFF for HTF)
    max_red:     int   = 0               # red-candle exit (0 = OFF for HTF)
    # Qualification
    qualify_mode: str   = "signal"       # "signal" = own get_signal; "weighted"
    block_regimes: Tuple[str, ...] = ("RANGE", "DEAD")
    warmup_bars:  int   = 210            # HTF trend strats need EMA200
    score_threshold: int = 50            # only used by qualify_mode="weighted"
    conf_floor:      int = 30
    allow_shorts: bool = False           # spot is long-only
    needs_funding: bool = False          # merge perp funding onto candles first
    needs_basis:   bool = False          # merge perp-vs-spot basis onto candles
    needs_xspread: bool = False          # merge cross-exchange spread onto candles
    periods:     Optional[List[int]] = None   # override SUITE_PERIODS for this spec
    symbols:     Optional[List[str]] = None   # override SUITE_SYMBOLS for this spec
    note:        str   = ""


# Candidate registry. The first entry is the CURRENT LIVE config (1m reversal
# scalper with scalper exits) kept as a BASELINE so the report proves, in the
# same run, that 1m is dead after fees. The rest are the higher-timeframe
# candidates whose targets can actually clear the ~0.24% round-trip cost.
CANDIDATES: List[StrategySpec] = [
    StrategySpec(
        key="reversal_scalper_1m", name="Reversal Scalper (1m baseline)",
        signal_name="Reversal Scalper", timeframes=["1m"],
        use_atr=False, sl_pct=0.4, tp_pct=0.8,
        arm_be=True, max_red=backtest.AS_MAX_RED_AFTER_ENTRY,
        qualify_mode="weighted", warmup_bars=60, periods=[7],
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        note="Current live config — expected REJECT (fees > edge on 1m). "
             "Full multi-symbol 1m baseline (BTC/ETH/SOL, 7d) re-proves, in the "
             "same run, that 1m is dead after fees; the weighted gate is slow "
             "per-candle so the window is kept short.",
    ),
    StrategySpec(
        key="donchian_breakout", name="Donchian Breakout (HTF)",
        signal_name="Donchian Breakout", timeframes=["5m", "15m", "1h", "4h"],
        use_atr=True, arm_be=False, max_red=0,
        qualify_mode="signal", warmup_bars=210, periods=[90, 180],
        note="Long-only trend breakout; ATR exits let winners run.",
    ),
    StrategySpec(
        key="trend_pullback", name="Trend Pullback (HTF)",
        signal_name="Trend Pullback", timeframes=["5m", "15m", "1h", "4h"],
        use_atr=True, arm_be=False, max_red=0,
        qualify_mode="signal", warmup_bars=210, periods=[90, 180],
        note="Long-only buy-the-dip in an uptrend; ATR exits.",
    ),
    StrategySpec(
        key="ema_macd_rsi_vol_v2", name="EMA/MACD/RSI/Volume V2 (HTF)",
        signal_name=backtest.V2_STRATEGY, timeframes=["5m", "15m", "1h", "4h"],
        use_atr=True, arm_be=False, max_red=0,
        qualify_mode="signal", warmup_bars=210, periods=[90, 180],
        note="Long-only confluence trend strategy on higher timeframes.",
    ),
    # ── ALTERNATIVE EDGE SOURCE: perpetual-swap funding (NOT a price pattern) ──
    # Funding encodes crowd positioning, not price shape, so the ~0.24% spot
    # round-trip fee cannot erase it the way it erases a 1m price wiggle. Both
    # readings of the SAME signal source are tested honestly. Funding settles
    # every 8h → only higher timeframes make sense. block_regimes=() so the
    # funding edge is judged on its own, not gated by a price-regime filter.
    # Data-history limit: the only Replit-reachable funding source (OKX, since
    # Binance fapi is geo-blocked 451) serves ~92 days (3 months) of 8h funding.
    # So periods are sized to that real window — single 90d period (1d candles
    # would give too few bars). 4h & 8h are the economically-meaningful sweep
    # timeframes; 5m is also included to honour the canonical-pipeline invariant
    # that every HTF candidate sweeps 5m. NOTE: funding is a step function (one
    # value per 8h), so at 5m the rolling z-score degenerates into a step-edge
    # detector that fires whenever its window straddles an 8h funding change —
    # economically meaningless, and it duly REJECTs. Kept for coverage parity,
    # not as a real funding edge. Honest constraint, noted in the verdict: this
    # is a SHORT-window probe, not a multi-year proof.
    StrategySpec(
        key="funding_contrarian", name="Funding Contrarian (perp)",
        signal_name="Funding Contrarian", timeframes=["5m", "4h", "8h"],
        use_atr=True, arm_be=False, max_red=0,
        qualify_mode="signal", block_regimes=(), warmup_bars=60,
        needs_funding=True, periods=[90],
        note="LONG when perp funding is unusually NEGATIVE (shorts over-crowded "
             "→ squeeze). OKX perp funding paired with Binance spot candles; "
             "ATR exits, no scalper exits.",
    ),
    StrategySpec(
        key="funding_momentum", name="Funding Momentum (perp)",
        signal_name="Funding Momentum", timeframes=["5m", "4h", "8h"],
        use_atr=True, arm_be=False, max_red=0,
        qualify_mode="signal", block_regimes=(), warmup_bars=60,
        needs_funding=True, periods=[90],
        note="LONG when perp funding is unusually POSITIVE (crowd funding the "
             "long → ride). Same data source as contrarian, opposite reading.",
    ),
    # ── ALTERNATIVE EDGE SOURCE: perp-vs-spot BASIS (NOT a price pattern) ──────
    # Basis = (perp_price − spot_price)/spot_price — perp premium/discount, a
    # continuous read of leverage/positioning that the ~0.24% spot round-trip
    # cannot trivially erase. OKX perpetual-SWAP close paired with Binance spot
    # close (Binance fapi is geo-blocked 451 from Replit). Both readings tested.
    # block_regimes=() so the basis edge is judged on its own. Continuous series,
    # so unlike funding it is meaningful at every timeframe — swept at 5m/1h/4h
    # (5m kept for the canonical coverage invariant). Honest constraint: OKX
    # history-candles caps the window, so this is a single-period probe, not a
    # multi-year proof.
    StrategySpec(
        key="basis_contrarian", name="Basis Contrarian (perp−spot)",
        signal_name="Basis Contrarian", timeframes=["5m", "1h", "4h"],
        use_atr=True, arm_be=False, max_red=0,
        qualify_mode="signal", block_regimes=(), warmup_bars=60,
        needs_basis=True, periods=[90],
        note="LONG when perp basis is unusually NEGATIVE (perp discount / shorts "
             "crowded → squeeze). OKX perp close vs Binance spot close; ATR exits.",
    ),
    StrategySpec(
        key="basis_momentum", name="Basis Momentum (perp−spot)",
        signal_name="Basis Momentum", timeframes=["5m", "1h", "4h"],
        use_atr=True, arm_be=False, max_red=0,
        qualify_mode="signal", block_regimes=(), warmup_bars=60,
        needs_basis=True, periods=[90],
        note="LONG when perp basis is unusually POSITIVE (leveraged longs piling "
             "in → ride). Same data source as contrarian, opposite reading.",
    ),
    # ── ALTERNATIVE EDGE SOURCE: CROSS-EXCHANGE SPREAD (NOT a price pattern) ──
    # X-spread = (OKX_spot_close − Binance_spot_close)/Binance_spot_close — a
    # same-asset price gap between two reachable venues. A lead-lag/convergence
    # dislocation read on the venue the bot trades (Binance). Both readings
    # tested; block_regimes=() so it is judged on its own. Continuous series →
    # swept at 5m/1h/4h (5m kept for the canonical coverage invariant). Same OKX
    # history cap → single-period probe, not a multi-year proof.
    StrategySpec(
        key="xspread_contrarian", name="X-Spread Contrarian (OKX−Binance)",
        signal_name="X-Spread Contrarian", timeframes=["5m", "1h", "4h"],
        use_atr=True, arm_be=False, max_red=0,
        qualify_mode="signal", block_regimes=(), warmup_bars=60,
        needs_xspread=True, periods=[90],
        note="LONG when OKX trades unusually BELOW Binance (z≤−T). OKX spot close "
             "vs Binance spot close; ATR exits.",
    ),
    StrategySpec(
        key="xspread_momentum", name="X-Spread Momentum (OKX−Binance)",
        signal_name="X-Spread Momentum", timeframes=["5m", "1h", "4h"],
        use_atr=True, arm_be=False, max_red=0,
        qualify_mode="signal", block_regimes=(), warmup_bars=60,
        needs_xspread=True, periods=[90],
        note="LONG when OKX leads ABOVE Binance (z≥+T) → Binance the cheap leg, "
             "expect convergence up. Same data source as contrarian, opposite read.",
    ),
]

SUITE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
SUITE_PERIODS = [90, 180]   # HTF needs longer windows to gather enough trades


# ─────────────────────────────────────────────────────────────────────────────
# Trade attribution
# ─────────────────────────────────────────────────────────────────────────────
def attribution(trades: List[Trade]) -> Dict:
    """Group closed trades by exit reason / entry regime / symbol.

    For each bucket we report count, win-rate, and the SUMMED net contribution
    (in %) so it is obvious which exits/regimes/symbols make or lose money.
    """
    def _bucket(key_fn) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        groups: Dict[str, List[Trade]] = defaultdict(list)
        for t in trades:
            groups[str(key_fn(t) or "?")].append(t)
        for k, ts in groups.items():
            rets = [t.ret_pct for t in ts]
            wins = [r for r in rets if r > 0]
            out[k] = {
                "trades":      len(ts),
                "win_rate":    round(len(wins) / len(ts) * 100, 1) if ts else 0.0,
                "sum_net_pct": round(sum(rets) * 100, 3),
                "avg_net_pct": round((sum(rets) / len(ts)) * 100, 4) if ts else 0.0,
            }
        return dict(sorted(out.items(),
                           key=lambda kv: kv[1]["sum_net_pct"], reverse=True))

    return {
        "by_exit_reason": _bucket(lambda t: t.reason),
        "by_regime":      _bucket(lambda t: t.regime),
        "by_symbol":      _bucket(lambda t: t.symbol),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Run one (spec × symbol × interval × period) sub-cell
# ─────────────────────────────────────────────────────────────────────────────
# Opt-in sub-cell result cache — makes a full sweep RESUMABLE across separate
# process invocations (each run computes a few more sub-cells and persists them;
# a later run reuses them). OFF by default so the dashboard / tests are
# unaffected; enable with env RESEARCH_SUBCELL_CACHE=1. Bump _SUBCELL_CACHE_VER
# whenever the backtest math or sub-cell payload shape changes.
_SUBCELL_CACHE_DIR = os.path.join(RESEARCH_DIR, "subcells")
# v2: cache key now includes a fingerprint of the actual dataframe used (length +
# first/last candle time), so a refetch of fresh klines invalidates stale results.
_SUBCELL_CACHE_VER = 2


def _subcell_cache_on() -> bool:
    return os.environ.get("RESEARCH_SUBCELL_CACHE") == "1"


def _df_fingerprint(df) -> str:
    """Stable fingerprint of the candle data a sub-cell was computed on. Changing
    market data (new candles, a refetch) changes this → the cache key changes →
    the stale sub-cell is never reused."""
    try:
        col = "open_time" if "open_time" in df.columns else df.columns[0]
        return f"{len(df)}:{df[col].iloc[0]}:{df[col].iloc[-1]}"
    except Exception:
        return f"len{len(df)}"


def _subcell_cache_key(spec: StrategySpec, symbol: str, interval: str, days: int,
                       fee: float, slip: float, data_fp: str) -> str:
    sig = json.dumps({
        "v": _SUBCELL_CACHE_VER, "key": spec.key, "sym": symbol,
        "interval": interval, "days": days, "fee": fee, "slip": slip,
        "signal": spec.signal_name, "sl": spec.sl_pct, "tp": spec.tp_pct,
        "use_atr": spec.use_atr, "atr_sl": spec.atr_sl_mult,
        "atr_tp": spec.atr_tp_mult, "arm_be": spec.arm_be, "max_red": spec.max_red,
        "qualify": spec.qualify_mode, "score_thr": spec.score_threshold,
        "conf": spec.conf_floor, "shorts": spec.allow_shorts,
        "warmup": spec.warmup_bars, "wf": WF_FOLDS, "data": data_fp,
    }, sort_keys=True)
    return hashlib.sha256(sig.encode()).hexdigest()[:24]


def _subcell_cache_load(key: str) -> Optional[Dict]:
    path = os.path.join(_SUBCELL_CACHE_DIR, key + ".json")
    try:
        with open(path) as f:
            payload = json.load(f)
        m = payload["metrics"]
        m["_trades"] = [Trade(**t) for t in payload.get("trades", [])]
        return m
    except Exception:
        return None


def _subcell_cache_save(key: str, m: Dict) -> None:
    os.makedirs(_SUBCELL_CACHE_DIR, exist_ok=True)
    trades = [asdict(t) for t in m.get("_trades", [])]
    metrics_only = {k: v for k, v in m.items() if k != "_trades"}
    path = os.path.join(_SUBCELL_CACHE_DIR, key + ".json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"metrics": metrics_only, "trades": trades}, f, default=str)
        os.replace(tmp, path)
    except Exception:
        pass


def run_subcell(spec: StrategySpec, symbol: str, interval: str, days: int, *,
                fee: float, slip: float, use_cache: bool = True) -> Dict:
    """Backtest a single sub-cell. Returns metrics + raw trades (for attribution)."""
    df = fetch_klines(symbol, interval, days, use_cache=use_cache)
    min_bars = spec.warmup_bars + 30
    if len(df) < min_bars:
        return {"trades": 0, "error": f"insufficient candles ({len(df)}<{min_bars})",
                "_trades": []}
    # Alternative edge sources merge their extra series onto the candles BEFORE
    # the replay; the signal then reads it as a column (no engine changes).
    # This happens before the cache key is built so the fingerprint binds to the
    # actual data used (candles + funding), not the candles alone.
    if spec.needs_funding:
        funding = backtest.fetch_funding_rates(symbol, days, use_cache=use_cache)
        df = backtest.merge_funding(df, funding)
    if spec.needs_basis:
        perp = backtest.fetch_okx_candles(symbol, interval, days, "SWAP",
                                          use_cache=use_cache)
        df = backtest.merge_basis(df, perp)
    if spec.needs_xspread:
        other = backtest.fetch_okx_candles(symbol, interval, days, "SPOT",
                                           use_cache=use_cache)
        df = backtest.merge_xspread(df, other)
    # Build the cache key AFTER fetching/merging so it is bound to the actual data
    # used — a refetch of fresh candles changes the fingerprint and invalidates
    # the cell.
    ck = None
    if _subcell_cache_on() and use_cache:
        ck = _subcell_cache_key(spec, symbol, interval, days, fee, slip,
                                _df_fingerprint(df))
        cached = _subcell_cache_load(ck)
        if cached is not None:
            return cached
    tr = run_symbol(
        df, symbol,
        sl_pct=spec.sl_pct, tp_pct=spec.tp_pct,
        fee_pct=fee, slip_pct=slip,
        score_threshold=spec.score_threshold, conf_floor=spec.conf_floor,
        strategy_name=spec.signal_name, allow_shorts=spec.allow_shorts,
        use_atr=spec.use_atr, atr_sl_mult=spec.atr_sl_mult,
        atr_tp_mult=spec.atr_tp_mult,
        arm_be=spec.arm_be, max_red=spec.max_red,
        qualify_mode=spec.qualify_mode, block_regimes=spec.block_regimes,
        warmup_bars=spec.warmup_bars,
    )
    m = metrics(tr)
    m["_trades"] = tr
    if ck is not None:
        _subcell_cache_save(ck, m)
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Run one (spec × interval) cell across all symbols × periods → verdict
# ─────────────────────────────────────────────────────────────────────────────
def run_cell(spec: StrategySpec, interval: str, *,
             symbols=SUITE_SYMBOLS, periods=SUITE_PERIODS,
             fee=DEFAULT_FEE, slip=DEFAULT_SLIP,
             use_cache: bool = True, emit=print) -> Dict:
    all_trades: List[Trade] = []
    subcells: Dict[str, Dict] = {}
    errors: List[str] = []

    for sym in symbols:
        for days in periods:
            tag = f"{sym}/{days}d"
            try:
                m = run_subcell(spec, sym, interval, days,
                                fee=fee, slip=slip, use_cache=use_cache)
            except Exception as e:
                errors.append(f"{tag}: {e}")
                emit(f"      [skip] {tag}: {e}")
                continue
            tr = m.pop("_trades", [])
            all_trades.extend(tr)
            subcells[tag] = m
            if m.get("trades", 0):
                emit(f"      {tag}: {m['trades']} trades  "
                     f"net_exp={m['expectancy_pct']:+.3f}%  PF={_pf(m)}")
            else:
                emit(f"      {tag}: 0 trades")

    agg = metrics(all_trades)
    wf  = walk_forward(all_trades, WF_FOLDS)
    attr = attribution(all_trades)
    verdict, reasons = _verdict(subcells, agg, wf)

    return {
        "strategy_key": spec.key,
        "strategy":     spec.name,
        "signal_name":  spec.signal_name,
        "interval":     interval,
        "exit_policy": {
            "use_atr": spec.use_atr, "sl_pct": spec.sl_pct, "tp_pct": spec.tp_pct,
            "atr_sl_mult": spec.atr_sl_mult, "atr_tp_mult": spec.atr_tp_mult,
            "arm_be": spec.arm_be, "max_red": spec.max_red,
        },
        "qualify_mode": spec.qualify_mode,
        "subcells":     subcells,
        "aggregate":    {k: v for k, v in agg.items() if not k.startswith("_")},
        "walk_forward": wf,
        "attribution":  attr,
        "verdict":      verdict,
        "verdict_reasons": reasons,
        "errors":       errors,
        "note":         spec.note,
    }


def _pf(m: Dict) -> str:
    pf = m.get("profit_factor", 0)
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def _verdict(subcells: Dict, agg: Dict, wf: List[Dict]) -> Tuple[str, List[str]]:
    """Strict ACCEPT/REJECT. Returns (verdict, human reasons)."""
    reasons: List[str] = []
    attempted = len(subcells)
    traded = [(tag, m) for tag, m in subcells.items() if m.get("trades", 0) >= MIN_TRADES]

    if agg.get("trades", 0) < MIN_TOTAL_TRADES:
        reasons.append(f"only {agg.get('trades', 0)} total trades "
                       f"(need ≥{MIN_TOTAL_TRADES}) — not enough to trust")
        return "REJECT", reasons
    if not traded:
        reasons.append(f"no sub-cell reached ≥{MIN_TRADES} trades")
        return "REJECT", reasons

    # Aggregate must itself be positive after fees — never accept on the strength
    # of one cell while the blended result is flat/negative.
    if agg.get("expectancy_pct", 0) <= 0 or _num_pf(agg) < 1.0:
        reasons.append(f"aggregate not profitable after fees "
                       f"(net_exp={agg.get('expectancy_pct', 0):+.3f}%, "
                       f"PF={_pf(agg)})")
        return "REJECT", reasons

    # BREADTH GUARD #1: an edge must hold across multiple symbols, not one lucky
    # coin. A single strong subcell carrying the aggregate is curve-fit, not edge.
    sym_traded = {tag.split("/")[0] for tag, _ in traded}
    if len(sym_traded) < MIN_SYMBOLS:
        reasons.append(f"edge only on {len(sym_traded)} symbol(s) "
                       f"({', '.join(sorted(sym_traded))}) — need ≥{MIN_SYMBOLS} "
                       f"to be robust, not one lucky coin")
        return "REJECT", reasons

    # BREADTH GUARD #2: enough of the ATTEMPTED subcells must have produced
    # tradable samples — otherwise sparse/excluded cells (< MIN_TRADES) hide
    # losers behind the "every traded cell passes" condition.
    if attempted and (len(traded) / attempted) < MIN_TRADED_FRAC:
        reasons.append(f"only {len(traded)}/{attempted} sub-cells reached "
                       f"≥{MIN_TRADES} trades (need ≥{int(MIN_TRADED_FRAC*100)}%) "
                       f"— too sparse to trust")
        return "REJECT", reasons

    bad = [tag for tag, m in traded
           if m.get("expectancy_pct", 0) <= 0 or _num_pf(m) < 1.0]
    if bad:
        reasons.append(f"{len(bad)}/{len(traded)} cells negative after fees: "
                       + ", ".join(bad[:6]))
        return "REJECT", reasons

    pos_folds = sum(1 for f in wf if f.get("expectancy_pct", 0) > 0)
    if wf and pos_folds <= len(wf) // 2:
        reasons.append(f"walk-forward weak: only {pos_folds}/{len(wf)} "
                       f"out-of-sample folds positive")
        return "REJECT", reasons

    reasons.append(f"positive net expectancy AND PF≥1 on every cell "
                   f"({len(traded)}/{attempted} cells across {len(sym_traded)} "
                   f"symbols, {agg['trades']} trades, "
                   f"net_exp={agg.get('expectancy_pct', 0):+.3f}%, "
                   f"WF {pos_folds}/{len(wf)} folds positive)")
    return "ACCEPT", reasons


def _num_pf(m: Dict) -> float:
    pf = m.get("profit_factor", 0)
    return 1e9 if pf == float("inf") else float(pf)


# ─────────────────────────────────────────────────────────────────────────────
# Full sweep + leaderboard + persistence
# ─────────────────────────────────────────────────────────────────────────────
def run_research(specs: Optional[List[StrategySpec]] = None, *,
                 symbols=SUITE_SYMBOLS, periods=SUITE_PERIODS,
                 fee=DEFAULT_FEE, slip=DEFAULT_SLIP,
                 use_cache: bool = True, persist: bool = True,
                 merge_latest: bool = False) -> Dict:
    """Sweep every (spec × timeframe) cell, rank by net expectancy, persist.

    When `merge_latest` is set, cells from the existing latest.json that are NOT
    being re-run here are carried forward so the canonical report stays COMPLETE
    even when only a subset of strategies is run (`--only`). This matters because
    the full sweep can exceed the sandbox's per-command time budget, so an
    alternative-edge probe (e.g. funding) is run on its own then merged into the
    full technical sweep rather than clobbering it.
    """
    specs = specs or CANDIDATES
    os.makedirs(RESEARCH_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    carried: List[Dict] = []
    if merge_latest:
        run_keys = {s.key for s in specs}
        try:
            with open(os.path.join(RESEARCH_DIR, "latest.json")) as f:
                prev = json.load(f)
            carried = [c for c in prev.get("cells", [])
                       if c.get("strategy_key") not in run_keys]
        except Exception:
            carried = []

    lines: List[str] = []
    def emit(s: str = ""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 76)
    emit("AlphaTrade STRATEGY RESEARCH — honest after-fee edge proof")
    emit("=" * 76)
    emit(f"fee={fee}%/side  slippage={slip}%/side  "
         f"round-trip cost≈{2 * (fee + slip):.2f}%")
    emit(f"symbols={','.join(symbols)}  periods={periods}d  "
         f"walk-forward folds={WF_FOLDS}")
    emit(f"ACCEPT rule: net expectancy>0 AND PF≥1 on EVERY cell (≥{MIN_TRADES} "
         f"trades), ≥{MIN_TOTAL_TRADES} total trades, majority WF folds positive")
    emit("-" * 76)

    cells: List[Dict] = []
    for spec in specs:
        for interval in spec.timeframes:
            _periods = spec.periods or periods
            _symbols = spec.symbols or symbols
            emit(f"\n▶ {spec.name}  @ {interval}  "
                 f"(symbols={','.join(_symbols)}  periods={_periods}d)")
            cell = run_cell(spec, interval, symbols=_symbols, periods=_periods,
                            fee=fee, slip=slip, use_cache=use_cache, emit=emit)
            agg = cell["aggregate"]
            mark = "🟢 ACCEPT" if cell["verdict"] == "ACCEPT" else "🔴 REJECT"
            if agg.get("trades", 0):
                emit(f"    → {mark}  net_exp={agg.get('expectancy_pct', 0):+.3f}%  "
                     f"PF={_pf(agg)}  trades={agg['trades']}  "
                     f"({cell['verdict_reasons'][0]})")
            else:
                emit(f"    → {mark}  (no trades — {cell['verdict_reasons'][0]})")
            cells.append(cell)

    # Carry forward cells from a prior full sweep that were NOT re-run here, so
    # the canonical report stays complete (see merge_latest docstring).
    if carried:
        emit(f"\n(merge) carried forward {len(carried)} cell(s) from prior "
             f"latest.json: {', '.join(sorted({c['strategy_key'] for c in carried}))}")
    cells = carried + cells

    # ── Leaderboard — rank by NET expectancy/trade (primary metric) ──────────
    ranked = sorted(
        cells,
        key=lambda c: (c["aggregate"].get("expectancy_pct", -1e9)
                       if c["aggregate"].get("trades", 0) else -1e9),
        reverse=True)

    emit("\n" + "=" * 76)
    emit("LEADERBOARD — ranked by NET expectancy/trade (after fees)")
    emit("=" * 76)
    emit(f"  {'#':<3}{'strategy @ tf':<34}{'trades':>7}{'net_exp%':>10}"
         f"{'PF':>7}{'win%':>7}  verdict")
    emit("  " + "-" * 72)
    for i, c in enumerate(ranked, 1):
        a = c["aggregate"]
        label = f"{c['strategy']} @ {c['interval']}"
        if a.get("trades", 0):
            emit(f"  {i:<3}{label:<34}{a['trades']:>7}"
                 f"{a.get('expectancy_pct', 0):>+10.3f}{_pf(a):>7}"
                 f"{a.get('win_rate', 0):>7.1f}  {c['verdict']}")
        else:
            emit(f"  {i:<3}{label:<34}{'0':>7}{'—':>10}{'—':>7}{'—':>7}  "
                 f"{c['verdict']}")

    accepted = [c for c in ranked if c["verdict"] == "ACCEPT"]
    best = accepted[0] if accepted else None

    emit("\n" + "=" * 76)
    if best:
        a = best["aggregate"]
        emit(f"VERDICT: 🟢 EDGE FOUND — {best['strategy']} @ {best['interval']}  "
             f"net_exp={a.get('expectancy_pct', 0):+.3f}%/trade  PF={_pf(a)}")
        emit("Best-candidate attribution (where the money comes from):")
        _emit_attr(best["attribution"], emit)
    else:
        emit("VERDICT: 🔴 NO EDGE — no strategy×timeframe clears the fee hurdle "
             "under the strict acceptance rule.")
        emit("Honest outcome: the live bot stays AUTO-DISABLED (allowlist empty). "
             "Manual trading remains available.")
    emit("=" * 76)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fee_pct_per_side": fee, "slippage_pct_per_side": slip,
        "round_trip_cost_pct": 2 * (fee + slip),
        "symbols": symbols, "periods": periods, "wf_folds": WF_FOLDS,
        "acceptance_rule": {
            "min_trades_per_cell": MIN_TRADES,
            "min_total_trades": MIN_TOTAL_TRADES,
            "rule": "net expectancy>0 AND PF>=1 on every traded cell; "
                    "majority WF folds positive",
        },
        "cells": cells,
        "leaderboard": [
            {"strategy": c["strategy"], "interval": c["interval"],
             "strategy_key": c["strategy_key"],
             "trades": c["aggregate"].get("trades", 0),
             "net_expectancy_pct": c["aggregate"].get("expectancy_pct", 0),
             "profit_factor": _num_pf(c["aggregate"]),
             "verdict": c["verdict"]}
            for c in ranked],
        "edge_found": bool(best),
        "best": ({"strategy": best["strategy"], "interval": best["interval"],
                  "strategy_key": best["strategy_key"]} if best else None),
    }

    if persist:
        base = os.path.join(RESEARCH_DIR, f"research_{stamp}")
        try:
            with open(base + ".json", "w") as f:
                json.dump(result, f, indent=2, default=str)
            with open(base + ".txt", "w") as f:
                f.write("\n".join(lines) + "\n")
            with open(os.path.join(RESEARCH_DIR, "latest.json"), "w") as f:
                json.dump(result, f, indent=2, default=str)
            emit(f"\n💾 Saved → {base}.json / .txt")
        except Exception as e:
            emit(f"[warn] failed to save research report: {e}")
        # Update the live allowlist from the ACCEPTED cells.
        save_validated(accepted, fee=fee, slip=slip)
        emit(f"💾 Allowlist updated → {VALIDATED_PATH} "
             f"({len(accepted)} validated strategy/timeframe pair(s))")

    return result


def _emit_attr(attr: Dict, emit):
    for dim, label in (("by_exit_reason", "exit reason"),
                       ("by_regime", "entry regime"),
                       ("by_symbol", "symbol")):
        emit(f"  by {label}:")
        for k, v in attr.get(dim, {}).items():
            emit(f"    {k:<16} trades={v['trades']:<4} win%={v['win_rate']:<5} "
                 f"net_sum={v['sum_net_pct']:+.2f}%  avg={v['avg_net_pct']:+.4f}%")


# ─────────────────────────────────────────────────────────────────────────────
# Validated allowlist — the AUTO-DISABLE gate's source of truth
# ─────────────────────────────────────────────────────────────────────────────
def save_validated(accepted_cells: List[Dict], *, fee: float, slip: float) -> None:
    """Persist the ACCEPTED (strategy, timeframe) pairs the live bot may trade."""
    os.makedirs(RESEARCH_DIR, exist_ok=True)
    entries = []
    for c in accepted_cells:
        a = c["aggregate"]
        entries.append({
            "strategy":           c["signal_name"],   # matches worker.strategy
            "strategy_display":   c["strategy"],
            "interval":           c["interval"],
            "exit_policy":        c["exit_policy"],
            "net_expectancy_pct": a.get("expectancy_pct", 0),
            "profit_factor":      _num_pf(a),
            "trades":             a.get("trades", 0),
        })
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "fee_pct_per_side": fee, "slippage_pct_per_side": slip,
        "validated": entries,
    }
    tmp = VALIDATED_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    os.replace(tmp, VALIDATED_PATH)


def load_validated() -> Dict:
    try:
        with open(VALIDATED_PATH) as f:
            return json.load(f)
    except Exception:
        return {"validated": []}


def is_strategy_validated(strategy_name: str, interval: str) -> Tuple[bool, Optional[Dict]]:
    """The live AUTO-DISABLE gate. Returns (allowed, entry).

    Default-safe: missing/empty allowlist → (False, None) → the bot must NOT
    auto-trade. A strategy/timeframe is only allowed if a research run ACCEPTED
    it (positive net expectancy after fees).
    """
    data = load_validated()
    for e in data.get("validated", []):
        if e.get("strategy") == strategy_name and e.get("interval") == interval:
            return True, e
    return False, None


def validation_status() -> Dict:
    """Convenience for the dashboard: full allowlist + freshness."""
    data = load_validated()
    return {
        "validated":  data.get("validated", []),
        "updated_at": data.get("updated_at"),
        "count":      len(data.get("validated", [])),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="AlphaTrade strategy research sweep")
    ap.add_argument("--fee", type=float, default=DEFAULT_FEE)
    ap.add_argument("--slippage", type=float, default=DEFAULT_SLIP)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-persist", action="store_true")
    ap.add_argument("--only", default="",
                    help="comma-separated strategy keys to run (default: all)")
    ap.add_argument("--merge", action="store_true",
                    help="carry forward cells from latest.json that aren't re-run "
                         "(keeps the canonical report complete when using --only)")
    args = ap.parse_args()

    specs = CANDIDATES
    if args.only:
        keys = {k.strip() for k in args.only.split(",") if k.strip()}
        specs = [s for s in CANDIDATES if s.key in keys]

    run_research(specs, fee=args.fee, slip=args.slippage,
                 use_cache=not args.no_cache, persist=not args.no_persist,
                 merge_latest=args.merge)
