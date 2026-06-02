"""Lock-in tests for Aggressive Mode.

Two guarantees the task cares about most:
  1. Aggressive modes create MORE opportunities and LARGER allowed size
     (monotonic knob ordering + exact confidence thresholds).
  2. No mode EVER bypasses the safety layer — strategy validation / allowlist,
     global risk caps, emergency stops, or safe mode.
"""
import json
import uuid

import pytest

import aggressive_mode as am
import research
from risk import (SymbolRiskSettings, GlobalRiskSettings, RiskManager,
                  GlobalRiskManager)


# ── Fakes ────────────────────────────────────────────────────────────────────
class _FakeWorker:
    def __init__(self):
        self.risk = RiskManager(SymbolRiskSettings())


class _FakeBot:
    """Mirrors the TradingBot attributes apply_profile_to_bot touches, plus the
    safety attributes it must NEVER touch."""
    def __init__(self):
        # knobs
        self.score_threshold_base = 50
        self.score_threshold = 50
        self.score_threshold_floor = 40
        self.confidence_floor = 30
        self.gpt_prob_floor = 50
        self.global_throttle_sec = 5
        self.check_every = 2
        self.workers = {"binance:BTCUSDT": _FakeWorker()}
        # SAFETY layer — must be invariant across every mode
        self.require_validation = True
        self.global_risk = type("G", (), {"settings": GlobalRiskSettings()})()


# ── Profiles / thresholds ────────────────────────────────────────────────────
def test_confidence_thresholds_match_spec():
    assert am.get_profile(am.CONSERVATIVE)["confidence_floor"] == 85
    assert am.get_profile(am.BALANCED)["confidence_floor"] == 75
    assert am.get_profile(am.AGGRESSIVE)["confidence_floor"] == 65
    assert am.get_profile(am.VERY_AGGRESSIVE)["confidence_floor"] == 55


def test_modes_ordered_least_to_most_aggressive():
    assert am.MODES == [am.CONSERVATIVE, am.BALANCED,
                        am.AGGRESSIVE, am.VERY_AGGRESSIVE]


def test_more_aggressive_lowers_acceptance_thresholds():
    """More aggressive ⇒ easier to qualify (lower floors) ⇒ more opportunities."""
    keys = ("confidence_floor", "score_threshold_base",
            "score_threshold_floor", "gpt_prob_floor")
    profs = [am.get_profile(m) for m in am.MODES]
    for k in keys:
        seq = [p[k] for p in profs]
        assert seq == sorted(seq, reverse=True), f"{k} not monotonically lowering: {seq}"
        assert seq[0] > seq[-1], f"{k} should drop from Conservative→Very Aggressive"


def test_more_aggressive_increases_size_and_cadence():
    """More aggressive ⇒ larger requested size and faster cadence."""
    profs = [am.get_profile(m) for m in am.MODES]
    sizes = [p["dynamic_size_pct"] for p in profs]
    assert sizes == sorted(sizes), f"size should increase: {sizes}"
    for k in ("global_throttle_sec", "cooldown_seconds", "check_every"):
        seq = [p[k] for p in profs]
        assert seq == sorted(seq, reverse=True), f"{k} should shrink: {seq}"


def test_normalize_and_default_safe():
    assert am.normalize_mode("aggressive") == am.AGGRESSIVE
    assert am.normalize_mode("VERY aggressive") == am.VERY_AGGRESSIVE
    assert am.normalize_mode("nonsense") == am.DEFAULT_MODE
    assert am.normalize_mode(None) == am.DEFAULT_MODE
    assert am.DEFAULT_MODE == am.BALANCED


# ── apply_profile_to_bot — knobs change, safety does NOT ──────────────────────
@pytest.mark.parametrize("mode", am.MODES)
def test_apply_profile_sets_knobs(mode):
    bot = _FakeBot()
    am.apply_profile_to_bot(bot, mode)
    p = am.get_profile(mode)
    assert bot.confidence_floor == p["confidence_floor"]
    assert bot.score_threshold_base == p["score_threshold_base"]
    assert bot.global_throttle_sec == p["global_throttle_sec"]
    assert bot.check_every == p["check_every"]
    w = next(iter(bot.workers.values()))
    assert w.risk.settings.dynamic_size_pct == p["dynamic_size_pct"]
    assert w.risk.settings.cooldown_seconds == p["cooldown_seconds"]


@pytest.mark.parametrize("mode", am.MODES)
def test_apply_profile_never_touches_safety(mode):
    """No mode — including Very Aggressive — may disable validation or relax
    any global risk cap / emergency stop."""
    bot = _FakeBot()
    g_before = vars(bot.global_risk.settings).copy()
    w = next(iter(bot.workers.values()))
    sym_emergency_before = w.risk.settings.emergency_stop
    sym_maxopen_before = w.risk.settings.max_open_trades

    am.apply_profile_to_bot(bot, mode)

    # allowlist gate stays armed
    assert bot.require_validation is True
    # global caps untouched
    assert vars(bot.global_risk.settings) == g_before
    # per-symbol safety untouched (only size + cooldown may change)
    assert w.risk.settings.emergency_stop == sym_emergency_before
    assert w.risk.settings.max_open_trades == sym_maxopen_before


@pytest.mark.parametrize("mode", am.MODES)
def test_global_risk_gate_still_blocks_after_mode_switch(mode):
    """Emergency stop and spending/open caps must keep blocking entries no
    matter how aggressive the mode is."""
    bot = _FakeBot()
    am.apply_profile_to_bot(bot, mode)

    # Emergency stop still halts everything.
    grm_stop = GlobalRiskManager(GlobalRiskSettings(emergency_stop=True))
    ok, _ = grm_stop.check_global([], 10.0, "BTCUSDT")
    assert ok is False

    # Spending limit still blocks an oversized entry even at Very Aggressive size.
    grm_cap = GlobalRiskManager(GlobalRiskSettings(max_total_exposure_usdt=50.0))
    ok, _ = grm_cap.check_global([], 1000.0, "BTCUSDT")
    assert ok is False

    # Global open-trade cap still blocks once full.
    grm_open = GlobalRiskManager(GlobalRiskSettings(max_open_trades_total=1))
    ok, _ = grm_open.check_global([{"invested": 10, "coin": "BTCUSDT"}], 10.0, "ETHUSDT")
    assert ok is False


# ── Validation/allowlist gate holds in EVERY mode ────────────────────────────
def _write_allowlist(path, entries):
    with open(path, "w") as f:
        json.dump({"validated": entries}, f)


@pytest.fixture
def _allowlist(tmp_path, monkeypatch):
    path = tmp_path / "validated_strategies.json"
    monkeypatch.setattr(research, "VALIDATED_PATH", str(path))
    _write_allowlist(path, [])          # default-safe: empty
    return path


@pytest.mark.parametrize("mode", am.MODES)
def test_non_approved_strategy_blocked_in_all_modes(_allowlist, mode):
    """Empty allowlist ⇒ a non-approved strategy is blocked regardless of how
    aggressive the mode is. Applying the profile must not flip the gate."""
    bot = _FakeBot()
    am.apply_profile_to_bot(bot, mode)
    assert bot.require_validation is True
    assert research.is_strategy_validated("EMA_MACD_RSI_VOLUME_V2", "4h")[0] is False
    assert research.is_strategy_validated("Reversal Scalper", "1m")[0] is False


def test_approved_strategy_allowed_but_only_exact_cell(_allowlist):
    """Sanity counter-test: an explicitly approved cell is allowed; a different
    timeframe/strategy is still blocked — independent of mode."""
    _write_allowlist(_allowlist, [
        {"strategy": "EMA_MACD_RSI_VOLUME_V2", "interval": "4h"}
    ])
    assert research.is_strategy_validated("EMA_MACD_RSI_VOLUME_V2", "4h")[0] is True
    assert research.is_strategy_validated("EMA_MACD_RSI_VOLUME_V2", "1m")[0] is False
    assert research.is_strategy_validated("Reversal Scalper", "4h")[0] is False


# ── PostgreSQL persistence + audit (scratch tables, skip if no DB) ────────────
@pytest.fixture
def _scratch_tables(monkeypatch):
    if not am.db_available():
        pytest.skip("DATABASE_URL / psycopg2 not available")
    sfx = uuid.uuid4().hex[:8]
    monkeypatch.setattr(am, "TABLE_MODE", f"test_aggro_mode_{sfx}")
    monkeypatch.setattr(am, "TABLE_AUDIT", f"test_aggro_audit_{sfx}")
    assert am.ensure_tables()
    yield
    conn = am._conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {am.TABLE_MODE}")
            cur.execute(f"DROP TABLE IF EXISTS {am.TABLE_AUDIT}")
    finally:
        conn.close()


def test_db_default_when_unset(_scratch_tables):
    assert am.get_mode() == am.DEFAULT_MODE


def test_db_roundtrip_and_audit(_scratch_tables):
    assert am.set_mode(am.AGGRESSIVE, actor="pytest", note="first")
    assert am.get_mode() == am.AGGRESSIVE
    assert am.set_mode(am.VERY_AGGRESSIVE, actor="pytest", note="second")
    assert am.get_mode() == am.VERY_AGGRESSIVE

    log = am.get_audit_log()
    assert len(log) == 2
    assert log[0]["new_mode"] == am.VERY_AGGRESSIVE
    assert log[0]["old_mode"] == am.AGGRESSIVE
    assert log[1]["new_mode"] == am.AGGRESSIVE
    assert log[1]["old_mode"] is None


def test_db_rejects_unknown_mode(_scratch_tables):
    assert am.set_mode("Reckless") is False
    assert am.get_mode() == am.DEFAULT_MODE
