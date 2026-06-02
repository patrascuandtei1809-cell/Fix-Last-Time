"""LOCK-IN tests for the strategy-approval rules.

These pin down the deterministic verdict logic so a future change to thresholds,
data, or code can never silently let a negative-/marginal-edge strategy through
to the live bot. They cover both halves of the pipeline:

  • `validate_candidates.classify_candidate` — the deep-validation ROBUST/WEAK/
    REJECTED engine for the hand-picked 4h candidates.
  • the persisted research artifacts (`latest.json`, `validated_strategies.json`)
    that gate what the live bot is allowed to auto-trade.

Required guarantees (from the task spec):
  1. Only V2 ETH @ 4h is ROBUST.
  2. Both SOL candidates stay WEAK.
  3. No 1m / 5m / 15m / 1h strategy is approved.
  4. A strategy FAILS approval if ANY of: Monte-Carlo CI dips negative,
     walk-forward folds not consistently positive, a sensitivity test fails, OR
     max drawdown exceeds the allowed limit.
  5. The live allowlist (`validated_strategies.json`) stays EMPTY unless a
     strategy is explicitly approved.
"""
import copy
import json
import os

import numpy as np
import pytest

import research as R
import validate_candidates as vc

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "research")
VALIDATION = os.path.join(DATA, "validation.json")
LATEST = os.path.join(DATA, "latest.json")
ALLOWLIST = os.path.join(DATA, "validated_strategies.json")

SUB_4H_INTERVALS = {"1m", "5m", "15m", "1h"}


# ───────────────────────── helpers ──────────────────────────────────────────
def _classify_from_validation(cid):
    """Rebuild the exact classifier inputs for a candidate from validation.json
    and return its verdict dict. Uses the (deterministic, seeded) bootstrap for
    the Monte-Carlo CI so the test is fast and reproducible."""
    doc = json.load(open(VALIDATION))
    sc = doc["scenarios"]
    base = sc["base"][cid]
    rets = np.array(base["rets"], dtype=float)
    gross = np.array(base["gross"], dtype=float)
    ts = np.array(base["ts"], dtype=float)
    base = {**base, "trades": len(rets)}
    sens_rows = vc.build_sens_rows(sc, cid, gross)
    wf = vc._walk_forward(rets, ts, vc.WF_FOLDS)
    mc, _ = vc.bootstrap_expectancy(rets)
    return vc.classify_candidate(base, sens_rows, wf, mc)


def _robust_inputs():
    """A synthetic candidate that passes EVERY gate → ROBUST. Each per-gate test
    starts from this and breaks exactly one gate."""
    base = {"expectancy_pct": 0.84, "profit_factor": 1.40, "sharpe": 2.43,
            "max_drawdown_pct": -31.5, "trades": 295}
    # 8 sensitivity rows (matches EXPECTED_SENS), all positive, sharpe well above
    # the 0.5×base floor (1.215).
    sens_rows = [(f"scen{i}", 0.5, 1.3, 2.0, 200) for i in range(vc.EXPECTED_SENS)]
    wf = [{"fold": i, "trades": 50, "exp_pct": 0.4, "pf": 1.3, "sharpe": 1.8}
          for i in range(vc.WF_FOLDS)]
    mc = {"p_exp_pos": 0.99, "exp_p05": 0.20}
    return base, sens_rows, wf, mc


# ───────────────── 1 & 2: real-data verdicts are locked ──────────────────────
def test_eth_v2_4h_is_robust():
    res = _classify_from_validation("ema_macd_rsi_vol_v2|ETHUSDT")
    assert res["verdict"] == "ROBUST", res["reasons"]
    assert vc.is_approved(res["verdict"])


@pytest.mark.parametrize("cid", [
    "ema_macd_rsi_vol_v2|SOLUSDT",
    "trend_pullback|SOLUSDT",
])
def test_sol_candidates_stay_weak(cid):
    res = _classify_from_validation(cid)
    assert res["verdict"] == "WEAK", res["reasons"]
    assert not vc.is_approved(res["verdict"])


def test_only_eth_is_robust_across_all_validated():
    doc = json.load(open(VALIDATION))
    robust = [cid for cid in doc["scenarios"]["base"]
              if _classify_from_validation(cid)["verdict"] == "ROBUST"]
    assert robust == ["ema_macd_rsi_vol_v2|ETHUSDT"]


# ───────────────── baseline + 4: each failure mode blocks approval ───────────
def test_synthetic_baseline_is_robust():
    res = vc.classify_candidate(*_robust_inputs())
    assert res["verdict"] == "ROBUST", res["reasons"]


def test_monte_carlo_ci_negative_blocks_approval():
    base, sens, wf, mc = _robust_inputs()
    mc = {**mc, "exp_p05": -0.01}   # CI lower bound dips below zero
    res = vc.classify_candidate(base, sens, wf, mc)
    assert res["verdict"] != "ROBUST"
    assert not res["gates"]["mc_ok"]


def test_walk_forward_inconsistent_blocks_approval():
    base, sens, wf, mc = _robust_inputs()
    # 3/5 folds positive — not "consistently positive" (needs ≥ folds-1) but not
    # a majority-negative outright reject either → fails approval as WEAK.
    wf = copy.deepcopy(wf)
    wf[0]["exp_pct"] = -0.4
    wf[1]["exp_pct"] = -0.4
    res = vc.classify_candidate(base, sens, wf, mc)
    assert res["verdict"] != "ROBUST"
    assert not res["gates"]["wf_ok"]


def test_sensitivity_failure_blocks_approval():
    base, sens, wf, mc = _robust_inputs()
    sens = list(sens)
    sens[3] = ("scen3", -0.05, 0.9, 2.0, 200)   # one scenario flips negative
    res = vc.classify_candidate(base, sens, wf, mc)
    assert res["verdict"] != "ROBUST"
    assert not res["gates"]["cost_param_ok"]


def test_max_drawdown_over_limit_blocks_approval():
    base, sens, wf, mc = _robust_inputs()
    base = {**base, "max_drawdown_pct": -(vc.MAX_DD_LIMIT_PCT + 5)}  # past the ceiling
    res = vc.classify_candidate(base, sens, wf, mc)
    assert not res["gates"]["dd_ok"]
    # DD is a ROBUST-blocker, NOT a hard reject: a DD-only breach must downgrade
    # to WEAK (everything else still passes), never REJECTED. Lock that exactly.
    assert res["verdict"] == "WEAK", res["reasons"]
    assert res["gates"]["rejected"] is False


def test_drawdown_exactly_at_limit_still_allows_approval():
    """Boundary: drawdown exactly equal to the limit must NOT block approval."""
    base, sens, wf, mc = _robust_inputs()
    base = {**base, "max_drawdown_pct": -vc.MAX_DD_LIMIT_PCT}
    res = vc.classify_candidate(base, sens, wf, mc)
    assert res["gates"]["dd_ok"]
    assert res["verdict"] == "ROBUST"


# ───────────────── 3: no sub-4h strategy is ever approved ────────────────────
def test_no_sub_4h_cell_is_approved():
    doc = json.load(open(LATEST))
    cells = doc["cells"]
    sub_4h = [c for c in cells if c["interval"] in SUB_4H_INTERVALS]
    assert sub_4h, "expected some 1m/15m/1h cells in the leaderboard"
    for c in sub_4h:
        assert c["verdict"] == "REJECT", (c["interval"], c.get("verdict_reasons"))


def test_no_cell_in_leaderboard_is_accepted():
    doc = json.load(open(LATEST))
    accepted = [c for c in doc["cells"] if c["verdict"] == "ACCEPT"]
    assert accepted == []
    assert doc["edge_found"] is False


# ───────────────── 5: live allowlist stays empty until approved ──────────────
def test_live_allowlist_snapshot_is_empty():
    """The committed allowlist (what the live bot reads on boot) is empty."""
    doc = json.load(open(ALLOWLIST))
    assert doc["validated"] == []


def _accepted_cell():
    return {
        "signal_name": "EMA/MACD/RSI/Vol v2", "strategy": "V2 @ 4h",
        "interval": "4h", "exit_policy": {"use_atr": True},
        "aggregate": {"expectancy_pct": 0.84, "profit_factor": 1.40, "trades": 295},
    }


def test_allowlist_default_safe_with_no_approval(tmp_path, monkeypatch):
    """Code-path lock (not just file snapshot): persisting ZERO accepted cells
    leaves the allowlist empty AND the live auto-disable gate refuses to trade."""
    monkeypatch.setattr(R, "VALIDATED_PATH", str(tmp_path / "validated.json"))
    R.save_validated([], fee=0.1, slip=0.02)
    assert R.load_validated()["validated"] == []
    allowed, entry = R.is_strategy_validated("EMA/MACD/RSI/Vol v2", "4h")
    assert allowed is False and entry is None


def test_allowlist_only_populated_by_explicit_approval(tmp_path, monkeypatch):
    """Counter-test proving the gate is meaningful: ONLY an explicitly approved
    cell appears, and ONLY that exact strategy/interval is allowed to auto-trade."""
    monkeypatch.setattr(R, "VALIDATED_PATH", str(tmp_path / "validated.json"))
    R.save_validated([_accepted_cell()], fee=0.1, slip=0.02)
    assert len(R.load_validated()["validated"]) == 1
    assert R.is_strategy_validated("EMA/MACD/RSI/Vol v2", "4h")[0] is True
    # a different timeframe / strategy is still blocked
    assert R.is_strategy_validated("EMA/MACD/RSI/Vol v2", "1h")[0] is False
    assert R.is_strategy_validated("Reversal Scalper", "4h")[0] is False
