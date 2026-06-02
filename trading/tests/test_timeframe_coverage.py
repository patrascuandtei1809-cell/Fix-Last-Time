"""Lock-in: the strict canonical research pipeline must cover every required
timeframe (1m/5m/15m/1h/4h) under ONE acceptance rule, and the persisted
leaderboard (latest.json) must reflect that coverage. Guards against a
timeframe silently dropping out of the canonical report.
"""
import json
import os

import research

REQUIRED_TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h"}


def test_candidate_specs_cover_required_timeframes():
    covered = set()
    for spec in research.CANDIDATES:
        covered.update(spec.timeframes)
    missing = REQUIRED_TIMEFRAMES - covered
    assert not missing, f"strict pipeline missing timeframe(s): {sorted(missing)}"


def test_each_htf_strategy_includes_5m():
    """5m must be swept by every higher-timeframe candidate, not only an
    exploratory side-script — it belongs in the canonical strict pipeline."""
    htf = [s for s in research.CANDIDATES if s.key != "reversal_scalper_1m"]
    assert htf, "expected at least one HTF candidate"
    for spec in htf:
        assert "5m" in spec.timeframes, f"{spec.key} does not sweep 5m"


def test_subcell_cache_off_by_default(monkeypatch):
    monkeypatch.delenv("RESEARCH_SUBCELL_CACHE", raising=False)
    assert research._subcell_cache_on() is False
    monkeypatch.setenv("RESEARCH_SUBCELL_CACHE", "1")
    assert research._subcell_cache_on() is True


def test_subcell_cache_resumes_and_invalidates_on_data_change(monkeypatch, tmp_path):
    """With the cache ON: an identical sub-cell is reused (resumable), but if the
    underlying candle data changes the fingerprint changes → recompute, never a
    stale result."""
    import pandas as pd

    monkeypatch.setenv("RESEARCH_SUBCELL_CACHE", "1")
    monkeypatch.setattr(research, "_SUBCELL_CACHE_DIR", str(tmp_path))

    spec = next(s for s in research.CANDIDATES if s.key != "reversal_scalper_1m")

    state = {"calls": 0, "n": 500}

    def fake_fetch(symbol, interval, days, use_cache=True):
        return pd.DataFrame({
            "open_time": list(range(state["n"])),
            "close": [100.0] * state["n"],
        })

    def fake_run_symbol(df, symbol, **kw):
        state["calls"] += 1
        return []

    monkeypatch.setattr(research, "fetch_klines", fake_fetch)
    monkeypatch.setattr(research, "run_symbol", fake_run_symbol)
    monkeypatch.setattr(research, "metrics", lambda tr: {"trades": 0})

    # First run computes; second run with identical data hits the cache (resumable).
    research.run_subcell(spec, "BTCUSDT", "5m", 90, fee=0.001, slip=0.0002)
    research.run_subcell(spec, "BTCUSDT", "5m", 90, fee=0.001, slip=0.0002)
    assert state["calls"] == 1, "identical sub-cell should reuse the cache"

    # Change the candle data → fingerprint changes → must recompute, not reuse.
    state["n"] = 501
    research.run_subcell(spec, "BTCUSDT", "5m", 90, fee=0.001, slip=0.0002)
    assert state["calls"] == 2, "changed data must invalidate the stale sub-cell"


def test_persisted_leaderboard_covers_all_timeframes():
    """If a canonical report has been generated, it must include every required
    timeframe so operators see one ranking across 1m/5m/15m/1h/4h."""
    path = os.path.join(research.RESEARCH_DIR, "latest.json")
    if not os.path.exists(path):
        import pytest
        pytest.skip("no latest.json yet — run research.py to generate it")
    with open(path) as f:
        report = json.load(f)
    tfs = {c["interval"] for c in report.get("cells", [])}
    missing = REQUIRED_TIMEFRAMES - tfs
    assert not missing, f"latest.json missing timeframe(s): {sorted(missing)}"
