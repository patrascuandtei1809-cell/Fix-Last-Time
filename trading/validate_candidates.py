"""Rigorous out-of-sample validation of the 4h edge candidates.

This is ANALYSIS ONLY — a thin runner over the existing backtest engine
(`backtest.run_symbol` / `metrics`). It does NOT touch the live bot, the
dashboard, the validated allowlist, or any infrastructure. It answers ONE
question: does the 4h edge survive rigorous validation?

Candidates (from the edge search):
  - EMA_MACD_RSI_VOLUME_V2 @ 4h on ETH
  - EMA_MACD_RSI_VOLUME_V2 @ 4h on SOL
  - Trend Pullback        @ 4h on SOL

Tests:
  1. Longer history  — maximum available 4h history (not the 360d edge-search window).
  2. Walk-forward    — K sequential OOS folds (params are FIXED, so every fold is
                       out-of-sample; this measures stability across time, not curve-fit).
  3. Monte Carlo     — bootstrap resample of trade returns (expectancy CI, P(exp>0))
                       + order reshuffle (max-drawdown distribution).
  4. Sensitivity     — fees ±50% (analytic: ret = gross − 2·fee), slippage ±50%
                       (re-run, slip changes fills→SL/TP timing), and ATR SL/TP
                       parameter perturbation ±25% (re-run).
  5. Verdict         — Robust / Weak / Rejected per candidate.

Sandbox-friendly: re-run scenarios are chunked into an accumulator JSON because
commands are capped at 120s and detached jobs don't persist. Fee scenarios need
no re-run. Deterministic: identical history → identical numbers.

    python validate_candidates.py --prewarm
    python validate_candidates.py --scenario base
    python validate_candidates.py --scenario slip_lo
    ... (one re-run scenario per call) ...
    python validate_candidates.py --report
"""
from __future__ import annotations
import argparse, json, os, time
from datetime import datetime, timezone
from typing import Dict, List

import numpy as np

import research
from research import CANDIDATES, DEFAULT_FEE, DEFAULT_SLIP
from backtest import run_symbol, metrics, fetch_klines

DATA_DIR = "data/research"
VAL_PATH = os.path.join(DATA_DIR, "validation.json")

TF = "4h"
MAX_DAYS = 4000  # fetch_klines returns whatever is actually available (paginates back)

# (strategy_key, symbol, short label)
CANDS = [
    ("ema_macd_rsi_vol_v2", "ETHUSDT", "V2 @ 4h ETH"),
    ("ema_macd_rsi_vol_v2", "SOLUSDT", "V2 @ 4h SOL"),
    ("trend_pullback",      "SOLUSDT", "TrendPullback @ 4h SOL"),
]

# Re-run scenarios (fee scenarios are analytic — handled in the report).
# sl/tp = multipliers ON the spec's ATR SL/TP multiples.
SCENARIOS = {
    "base":    dict(fee=0.10, slip=0.02, sl=1.00, tp=1.00),
    "slip_lo": dict(fee=0.10, slip=0.01, sl=1.00, tp=1.00),   # slippage −50%
    "slip_hi": dict(fee=0.10, slip=0.03, sl=1.00, tp=1.00),   # slippage +50%
    "sl_lo":   dict(fee=0.10, slip=0.02, sl=0.75, tp=1.00),   # SL distance −25%
    "sl_hi":   dict(fee=0.10, slip=0.02, sl=1.25, tp=1.00),   # SL distance +25%
    "tp_lo":   dict(fee=0.10, slip=0.02, sl=1.00, tp=0.75),   # TP distance −25%
    "tp_hi":   dict(fee=0.10, slip=0.02, sl=1.00, tp=1.25),   # TP distance +25%
}

MIN_TRADES = 15          # below this a cell is too thin to trust
MC_ITERS = 5000
WF_FOLDS = 5
RNG_SEED = 20260602      # deterministic Monte Carlo

# Approval (ROBUST) gates — locked in by trading/tests/test_approval_rules.py.
MAX_DD_LIMIT_PCT = 40.0  # base max-drawdown ceiling; deeper than this can't be ROBUST
EXPECTED_SENS    = 8     # 2 analytic fee + 6 re-run scenarios = a COMPLETE sweep
MC_P_POS_ROBUST  = 0.95  # P(expectancy>0) required for ROBUST
MC_P_POS_REJECT  = 0.50  # below this the candidate is outright REJECTED

VERDICT_ICON = {"ROBUST": "🟢 ROBUST", "WEAK": "🟡 WEAK", "REJECTED": "🔴 REJECTED"}


def is_approved(verdict: str) -> bool:
    """A candidate is eligible for the live allowlist ONLY if ROBUST. Even then it
    is not auto-added — the operator must explicitly approve (see
    research.save_validated / validated_strategies.json)."""
    return verdict == "ROBUST"

_SPEC = {s.key: s for s in CANDIDATES}


def _cid(key: str, sym: str) -> str:
    return f"{key}|{sym}"


def _load() -> Dict:
    try:
        with open(VAL_PATH) as f:
            return json.load(f)
    except Exception:
        return {"scenarios": {}}


def _save(doc: Dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = VAL_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, VAL_PATH)


def _run_one(spec, sym: str, fee: float, slip: float, sl_f: float, tp_f: float):
    df = fetch_klines(sym, TF, MAX_DAYS)
    tr = run_symbol(
        df, sym,
        sl_pct=spec.sl_pct, tp_pct=spec.tp_pct,
        fee_pct=fee, slip_pct=slip,
        score_threshold=spec.score_threshold, conf_floor=spec.conf_floor,
        strategy_name=spec.signal_name, allow_shorts=spec.allow_shorts,
        use_atr=spec.use_atr,
        atr_sl_mult=spec.atr_sl_mult * sl_f, atr_tp_mult=spec.atr_tp_mult * tp_f,
        arm_be=spec.arm_be, max_red=spec.max_red,
        qualify_mode=spec.qualify_mode, block_regimes=spec.block_regimes,
        warmup_bars=spec.warmup_bars,
    )
    return df, tr


def run_scenario(name: str) -> None:
    if name not in SCENARIOS:
        print(f"unknown scenario {name}; choose from {list(SCENARIOS)}"); return
    cfg = SCENARIOS[name]
    doc = _load()
    doc.setdefault("scenarios", {}).setdefault(name, {})
    for key, sym, label in CANDS:
        spec = _SPEC[key]
        t0 = time.time()
        df, tr = _run_one(spec, sym, cfg["fee"], cfg["slip"], cfg["sl"], cfg["tp"])
        m = metrics(tr)
        m.pop("_trades", None)
        rec = {k: v for k, v in m.items()
               if k != "profit_factor"}
        pf = m.get("profit_factor", 0.0)
        rec["profit_factor"] = None if pf == float("inf") else pf
        if name == "base":
            rec["rets"]  = [float(t.ret_pct) for t in tr]
            rec["gross"] = [float(t.gross_pct) for t in tr]
            rec["ts"]    = [int(t.entry_ts) for t in tr]
            rec["n_candles"] = int(len(df))
            rec["first_ts"]  = int(df["open_time"].iloc[0])
            rec["last_ts"]   = int(df["open_time"].iloc[-1])
        doc["scenarios"][name][_cid(key, sym)] = rec
        _save(doc)
        dt = time.time() - t0
        pf_s = "inf" if rec["profit_factor"] is None else f"{rec['profit_factor']:.2f}"
        print(f"  {label:<26} trades={m.get('trades',0):<4} "
              f"exp={m.get('expectancy_pct',0):+.3f}% PF={pf_s:<5} "
              f"sharpe={m.get('sharpe',0):+.2f} ({dt:.0f}s)", flush=True)
    print(f"[saved scenario '{name}']")


# ── analytics ────────────────────────────────────────────────────────────────
def _pf_from_rets(rets: np.ndarray) -> float:
    gw = rets[rets > 0].sum(); gl = -rets[rets <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def _sharpe(rets: np.ndarray) -> float:
    if len(rets) < 2:
        return 0.0
    std = rets.std(ddof=1)
    return float(rets.mean() / std * np.sqrt(len(rets))) if std > 0 else 0.0


def _max_dd(rets: np.ndarray) -> float:
    eq = np.cumprod(1 + rets); peak = np.maximum.accumulate(eq)
    return float(((eq - peak) / peak).min() * 100) if len(eq) else 0.0


def _fee_adjust(gross: np.ndarray, fee_side: float) -> np.ndarray:
    return gross - 2 * (fee_side / 100.0)


def _walk_forward(rets: np.ndarray, ts: np.ndarray, folds: int) -> List[Dict]:
    order = np.argsort(ts)
    r = rets[order]
    out = []
    splits = np.array_split(r, folds)
    for i, seg in enumerate(splits, 1):
        if len(seg) == 0:
            out.append({"fold": i, "trades": 0}); continue
        out.append({
            "fold": i, "trades": int(len(seg)),
            "exp_pct": float(seg.mean() * 100),
            "pf": (None if _pf_from_rets(seg) == float("inf") else round(_pf_from_rets(seg), 2)),
            "sharpe": round(_sharpe(seg), 2),
        })
    return out


def bootstrap_expectancy(rets: np.ndarray, iters: int = MC_ITERS, seed: int = RNG_SEED):
    """Bootstrap (resample WITH replacement) → expectancy / PF / total-return CIs.
    Deterministic for a given seed. Returns (stats, rng) so callers can keep
    drawing from the same generator (the drawdown reshuffle continues the stream
    so the report's numbers stay byte-identical to a single combined draw)."""
    rng = np.random.default_rng(seed)
    n = len(rets)
    idx = rng.integers(0, n, size=(iters, n))
    samp = rets[idx]                                   # (iters, n)
    exp = samp.mean(axis=1) * 100
    totret = (np.prod(1 + samp, axis=1) - 1) * 100
    gw = np.where(samp > 0, samp, 0).sum(axis=1)
    gl = -np.where(samp <= 0, samp, 0).sum(axis=1)
    pf = np.divide(gw, gl, out=np.full_like(gw, np.inf), where=gl > 0)
    stats = {
        "exp_mean": float(exp.mean()),
        "exp_p05": float(np.percentile(exp, 5)),
        "exp_p95": float(np.percentile(exp, 95)),
        "p_exp_pos": float((exp > 0).mean()),
        "pf_p05": float(np.percentile(pf[np.isfinite(pf)], 5)) if np.isfinite(pf).any() else float("inf"),
        "totret_p05": float(np.percentile(totret, 5)),
        "totret_p95": float(np.percentile(totret, 95)),
    }
    return stats, rng


def _monte_carlo(rets: np.ndarray) -> Dict:
    stats, rng = bootstrap_expectancy(rets)
    # Order reshuffle (permutation, same trades) → max-drawdown distribution.
    dd = np.empty(MC_ITERS)
    for i in range(MC_ITERS):
        perm = rng.permutation(rets)
        eq = np.cumprod(1 + perm); peak = np.maximum.accumulate(eq)
        dd[i] = ((eq - peak) / peak).min() * 100
    return {
        **stats,
        "dd_median": float(np.median(dd)),
        "dd_p95_worst": float(np.percentile(dd, 5)),  # 5th pct = worst tail
    }


_SENS_LABEL = {"slip_lo": "slip −50% (0.01)", "slip_hi": "slip +50% (0.03)",
               "sl_lo": "ATR-SL −25%", "sl_hi": "ATR-SL +25%",
               "tp_lo": "ATR-TP −25%", "tp_hi": "ATR-TP +25%"}


def build_sens_rows(sc: Dict, cid: str, gross: np.ndarray) -> List[tuple]:
    """One sensitivity table for a candidate: 2 analytic fee rows (ret=gross−2·fee)
    + 6 re-run rows (slippage / ATR SL / ATR TP). Each row is
    (label, exp_pct|None, pf|None, sharpe|None, n)."""
    rows: List[tuple] = []
    for fname, fside in (("fee −50% (0.05)", 0.05), ("fee +50% (0.15)", 0.15)):
        r2 = _fee_adjust(gross, fside)
        rows.append((fname, float(r2.mean() * 100), _pf_from_rets(r2), _sharpe(r2), len(r2)))
    for scn in ("slip_lo", "slip_hi", "sl_lo", "sl_hi", "tp_lo", "tp_hi"):
        rec = sc.get(scn, {}).get(cid)
        if not rec:
            rows.append((_SENS_LABEL[scn] + " [MISSING]", None, None, None, 0)); continue
        pf = rec["profit_factor"]
        rows.append((_SENS_LABEL[scn], rec["expectancy_pct"],
                     (float("inf") if pf is None else pf),
                     rec["sharpe"], rec.get("trades", 0)))
    return rows


def classify_candidate(base: Dict, sens_rows: List[tuple], wf: List[Dict], mc: Dict,
                       *, expected_sens: int = EXPECTED_SENS,
                       min_trades: int = MIN_TRADES,
                       max_dd_limit: float = MAX_DD_LIMIT_PCT) -> Dict:
    """Pure approval-rule engine → {"verdict", "reasons", "gates"}.

    A candidate is APPROVED (ROBUST) only if it passes EVERY gate. It FAILS
    approval (drops to WEAK) if any of these hold, and is REJECTED on hard failures:
      • base expectancy ≤0 / PF<1 / too few trades
      • a fee/slip/parameter sensitivity scenario flips exp≤0 or PF<1
      • walk-forward folds are not consistently positive
      • the Monte-Carlo confidence interval dips negative (or P(exp>0) too low)
      • Sharpe deteriorates materially under stress
      • max drawdown exceeds the allowed limit
      • the sensitivity sweep is incomplete
    """
    b_exp = base["expectancy_pct"]; b_pf = base["profit_factor"]
    b_sh = base["sharpe"]; b_dd = base.get("max_drawdown_pct", 0.0)
    n = base.get("trades", 0)

    scen = [(e, pf) for (_, e, pf, _, _) in sens_rows if e is not None]
    n_present = len(scen)
    cost_param_ok = n_present > 0 and all(e > 0 and pf >= 1 for e, pf in scen)
    worst_sharpe = min([sh for (_, e, _, sh, _) in sens_rows if e is not None] + [b_sh])
    wf_valid = sum(1 for f in wf if f.get("trades", 0) > 0)
    wf_pos = sum(1 for f in wf if f.get("trades", 0) > 0 and f.get("exp_pct", 0) > 0)

    base_ok = b_exp > 0 and (b_pf is not None and b_pf >= 1) and n >= min_trades
    wf_ok = wf_valid >= 3 and wf_pos >= wf_valid - 1          # at most one negative fold
    mc_ok = mc["p_exp_pos"] >= MC_P_POS_ROBUST and mc["exp_p05"] > 0
    sharpe_ok = b_sh > 0 and worst_sharpe > 0 and worst_sharpe >= 0.5 * b_sh
    dd_ok = abs(b_dd) <= max_dd_limit
    complete = (n_present == expected_sens)

    reasons: List[str] = []
    if not base_ok:
        reasons.append("base exp≤0 / PF<1 / too few trades")
    if not cost_param_ok:
        reasons.append("sensitivity fail: a fee/slip/param scenario flips exp≤0 or PF<1")
    if not wf_ok:
        reasons.append(f"walk-forward unstable ({wf_pos}/{wf_valid} folds positive)")
    if not mc_ok:
        reasons.append(f"MC weak / CI dips negative "
                       f"(P(exp>0)={mc['p_exp_pos']*100:.0f}%, 5th-pct exp={mc['exp_p05']:+.3f}%)")
    if not sharpe_ok:
        reasons.append(f"Sharpe deteriorates (worst={worst_sharpe:+.2f} vs base {b_sh:+.2f})")
    if not dd_ok:
        reasons.append(f"max drawdown {b_dd:.1f}% exceeds limit ±{max_dd_limit:.0f}%")
    if not complete:
        reasons.append(f"INCOMPLETE: {n_present}/{expected_sens} sensitivity scenarios present")

    rejected = ((not base_ok) or (not cost_param_ok)
                or (mc["p_exp_pos"] < MC_P_POS_REJECT)
                or (wf_valid >= 3 and wf_pos < wf_valid / 2))
    if rejected:
        verdict = "REJECTED"
    elif base_ok and cost_param_ok and wf_ok and mc_ok and sharpe_ok and dd_ok and complete:
        verdict = "ROBUST"
    else:
        verdict = "WEAK"

    return {
        "verdict": verdict,
        "reasons": reasons,
        "gates": {
            "base_ok": base_ok, "cost_param_ok": cost_param_ok, "wf_ok": wf_ok,
            "mc_ok": mc_ok, "sharpe_ok": sharpe_ok, "dd_ok": dd_ok,
            "complete": complete, "rejected": rejected,
            "wf_pos": wf_pos, "wf_valid": wf_valid, "worst_sharpe": worst_sharpe,
        },
    }


def _ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def report() -> None:
    doc = _load()
    sc = doc.get("scenarios", {})
    if "base" not in sc:
        print("No base scenario yet. Run `--scenario base` first."); return

    print("=" * 100)
    print("4h EDGE — RIGOROUS VALIDATION")
    print(f"max history fetched per symbol · fee0={DEFAULT_FEE}%/side "
          f"slip0={DEFAULT_SLIP}%/side · MC iters={MC_ITERS} · WF folds={WF_FOLDS}")
    have = [s for s in SCENARIOS if s in sc]
    missing = [s for s in SCENARIOS if s not in sc]
    print(f"re-run scenarios present: {have}")
    if missing:
        print(f"⚠️  missing re-run scenarios (verdict will treat as INCOMPLETE): {missing}")
    print("=" * 100)

    final = {}
    for key, sym, label in CANDS:
        cid = _cid(key, sym)
        base = sc["base"].get(cid)
        if not base or not base.get("rets"):
            print(f"\n### {label}: no base trades — SKIP"); continue
        rets = np.array(base["rets"], dtype=float)
        gross = np.array(base["gross"], dtype=float)
        ts = np.array(base["ts"], dtype=float)
        n = len(rets)

        print(f"\n{'='*100}\n### {label}   [{key} / {sym} / {TF}]")
        print(f"History: {base['n_candles']} candles  "
              f"{_ts(base['first_ts'])} → {_ts(base['last_ts'])}   "
              f"trades={n}")
        b_exp = base["expectancy_pct"]; b_pf = base["profit_factor"]
        b_sh = base["sharpe"]
        b_pf_s = "inf" if b_pf is None else f"{b_pf:.2f}"
        print(f"  Base (full history): exp={b_exp:+.3f}%/trade  PF={b_pf_s}  "
              f"win%={base['win_rate']:.1f}  Sharpe={b_sh:+.2f}  "
              f"maxDD={base['max_drawdown_pct']:.1f}%  totRet={base.get('total_return_pct',0):+.1f}%")

        # 1) Walk-forward OOS folds
        print("  Walk-forward (sequential OOS folds):")
        wf = _walk_forward(rets, ts, WF_FOLDS)
        wf_pos = 0; wf_valid = 0
        for f in wf:
            if f["trades"] == 0:
                print(f"    fold {f['fold']}: (empty)"); continue
            wf_valid += 1
            if f["exp_pct"] > 0:
                wf_pos += 1
            pf_s = "inf" if f["pf"] is None else f"{f['pf']:.2f}"
            print(f"    fold {f['fold']}: trades={f['trades']:<3} "
                  f"exp={f['exp_pct']:+.3f}% PF={pf_s:<5} sharpe={f['sharpe']:+.2f}")
        print(f"    → {wf_pos}/{wf_valid} folds positive expectancy")

        # 2) Monte Carlo
        mc = _monte_carlo(rets)
        print("  Monte Carlo:")
        print(f"    bootstrap expectancy: mean={mc['exp_mean']:+.3f}%  "
              f"90% CI=[{mc['exp_p05']:+.3f}%, {mc['exp_p95']:+.3f}%]  "
              f"P(exp>0)={mc['p_exp_pos']*100:.1f}%")
        print(f"    bootstrap PF 5th pct={mc['pf_p05']:.2f}  "
              f"total-return 90% CI=[{mc['totret_p05']:+.1f}%, {mc['totret_p95']:+.1f}%]")
        print(f"    reshuffle maxDD: median={mc['dd_median']:.1f}%  "
              f"worst-5%={mc['dd_p95_worst']:.1f}%")

        # 3) Sensitivity — fees (analytic), slippage + params (re-run scenarios)
        print("  Sensitivity:")
        sens_rows = build_sens_rows(sc, cid, gross)
        for nm, e, pf, sh, tn in sens_rows:
            if e is None:
                print(f"    {nm:<20} —"); continue
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            flag = "" if (e > 0 and pf >= 1) else "  ✗"
            print(f"    {nm:<20} exp={e:+.3f}% PF={pf_s:<5} sharpe={sh:+.2f} (n={tn}){flag}")

        # ── verdict (pure, testable approval engine) ───────────────────────────
        res = classify_candidate(base, sens_rows, wf, mc)
        verdict = VERDICT_ICON[res["verdict"]]
        final[label] = (verdict, res["reasons"])
        print(f"  VERDICT: {verdict}")
        for r in res["reasons"]:
            print(f"     · {r}")

    print("\n" + "=" * 100)
    print("FINAL")
    print("=" * 100)
    for label, (verdict, reasons) in final.items():
        print(f"  {verdict:<14} {label}")
        for r in reasons:
            print(f"        - {r}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", help=f"one of {list(SCENARIOS)}")
    ap.add_argument("--prewarm", action="store_true", help="fetch max history only")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if args.reset:
        _save({"scenarios": {}}); print("cleared validation.json")
    if args.prewarm:
        for _, sym, label in CANDS:
            t0 = time.time()
            df = fetch_klines(sym, TF, MAX_DAYS)
            print(f"  {sym} {TF}: {len(df)} candles "
                  f"{_ts(int(df['open_time'].iloc[0]))}→{_ts(int(df['open_time'].iloc[-1]))} "
                  f"({time.time()-t0:.0f}s)")
    if args.scenario:
        run_scenario(args.scenario)
    if args.report:
        report()
