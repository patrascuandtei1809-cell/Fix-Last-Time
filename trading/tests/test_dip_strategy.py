"""Lock-in tests for the 20-Minute Dip strategy (Task #11).

Guarantees the task cares about most (FINAL TRADING RULE):
  1. The pure rule fires EXACTLY on spec: BUY at ≤ −2.00% 20m change,
     SELL at ≥ +1.50% profit, STOP-LOSS at ≤ −0.01%, HOLD otherwise.
  1b. Entry-quality filters: BUY also needs a volume spike (≥1.5× avg) AND a
     short-term trend upturn; both fail OPEN on missing data and gate ENTRY
     only (never an open position's exit).
  2. The live engine reaches a LIVE order on a dip with NONE of the old gates
     (research/allowlist, confidence floor, weighted score, ranking, GPT veto,
     anti-idle) in the path.
  3. The safety layer STILL blocks: emergency stop, safe mode, exchange not
     connected, spending limit, global risk gate, and the 1-min stop-loss /
     re-entry cooldowns.
  4. Settings persist and aggressive defaults ON.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import dip_strategy as dip
import live_settings as ls
import live_engine as le


@pytest.fixture(autouse=True)
def _scratch_tables(monkeypatch):
    """Isolate every test from the production PG tables. When a DB is available
    we point settings/audit/cooldown at uuid-suffixed scratch tables and drop
    them afterwards, so tests never pollute (or read stale) live state."""
    if not ls.db_available():
        yield
        return
    suffix = uuid.uuid4().hex[:8]
    tabs = {
        "TABLE_SETTINGS": f"trading_dip_settings_test_{suffix}",
        "TABLE_AUDIT":    f"trading_dip_audit_test_{suffix}",
        "TABLE_COOLDOWN": f"trading_dip_cooldown_test_{suffix}",
    }
    for attr, name in tabs.items():
        monkeypatch.setattr(ls, attr, name)
    ls.ensure_tables()
    yield
    conn = ls._conn()
    if conn:
        try:
            with conn, conn.cursor() as cur:
                for name in tabs.values():
                    cur.execute(f"DROP TABLE IF EXISTS {name}")
        finally:
            conn.close()


# ── Fakes ────────────────────────────────────────────────────────────────────
def _closes_for_change(change_pct: float, ref: float = 100.0, lookback: int = 20):
    """Build a closes list (oldest→newest) whose 20m change == change_pct."""
    cur = ref * (1.0 + change_pct / 100.0)
    return [ref] * (lookback) + [cur]   # len == lookback+1, closes[-(21)]=ref


class _FakeExchange:
    name = "binance"

    def __init__(self, price=100.0, change_pct=-0.20, free=1000.0,
                 connected=True):
        self.client = object() if connected else None
        self._price = price
        self._closes = _closes_for_change(change_pct, ref=price /
                                          (1.0 + change_pct / 100.0))
        self._free = free
        self.buy_calls = []

    def get_price(self, symbol):
        return self._price

    def get_klines(self, symbol, interval, limit=25):
        return list(self._closes)

    def get_balance(self, asset):
        return {"free": self._free, "total": self._free}

    def place_buy_order(self, symbol, quote):
        self.buy_calls.append((symbol, quote))
        qty = quote / self._price
        return {"ok": True, "price": self._price, "qty": qty, "fee": 0.0}


def _settings(**kw):
    s = ls.LiveSettings()
    # The legacy engine tests below pre-date the volume/trend entry filters and
    # exercise the gate-removal / safety layer with a synthetic single-drop
    # candle series. Keep those filters OFF here so their intent is preserved;
    # the filters have their own dedicated tests. Production defaults stay ON.
    s.volume_filter_on = False
    s.trend_filter_on = False
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _pass_gate(amount, symbol):
    return True, ""


# ── 1. Pure rule: BUY on dip ─────────────────────────────────────────────────
def test_buy_when_change_at_or_below_threshold():
    d = dip.decide_entry(-2.00)                 # exactly at threshold
    assert d.action == dip.BUY
    d2 = dip.decide_entry(-2.50)               # well below
    assert d2.action == dip.BUY
    d3 = dip.decide_entry(-1.99)               # just above ⇒ HOLD
    assert d3.action == dip.HOLD


# ── 2. Pure rule: SELL at take-profit ────────────────────────────────────────
def test_sell_when_profit_at_or_above_take_profit():
    # entry 100 → +1.60% at 101.60 (clearly ≥ +1.50% target)
    d = dip.decide_exit(100.0, 101.60)
    assert d.action == dip.SELL
    d_below = dip.decide_exit(100.0, 101.40)   # +1.40% ⇒ HOLD
    assert d_below.action == dip.HOLD


# ── 3. Pure rule: STOP-LOSS ──────────────────────────────────────────────────
def test_stop_loss_when_loss_at_or_below_limit():
    d = dip.decide_exit(100.0, 99.99)          # −0.01% exactly
    assert d.action == dip.STOP_LOSS
    d_above = dip.decide_exit(100.0, 99.995)   # −0.005% ⇒ HOLD
    assert d_above.action == dip.HOLD


# ── 4. Pure rule: HOLD in the dead zone ──────────────────────────────────────
def test_hold_between_thresholds():
    assert dip.decide_entry(0.05).action == dip.HOLD
    assert dip.decide_exit(100.0, 100.20).action == dip.HOLD


# ── 5. Live engine reaches a LIVE BUY with NONE of the old gates ─────────────
def test_engine_buys_on_dip_without_legacy_gates():
    ex = _FakeExchange(price=100.0, change_pct=-2.50, free=1000.0)
    # The live dip path is NOT research-gated (Task #11): no validation /
    # allowlist / verdict check exists in live_engine.py. A sufficient dip plus
    # the money-safety gates is all that's required to place a LIVE order.
    eng = le.DipLiveEngine(exchange=ex, cooldown=ls.CooldownStore())
    rec = eng.evaluate(symbol="BTCUSDT", settings=_settings(),
                       open_trades=[], current_exposure=0.0,
                       global_gate_fn=_pass_gate)
    assert rec.decision == "BUY"
    assert rec.traded is True
    assert ex.buy_calls, "a LIVE buy order should have been placed"
    # The dip core imports nothing from the legacy decision stack.
    import inspect
    src = inspect.getsource(dip)
    for banned in ("import strategy", "import scoring", "gpt_advisor",
                   "ai_engine", "market_regime", "validate_candidates"):
        assert banned not in src
    # The DIP engine itself must carry NO research gate (no allowlist, no
    # require_validation param). This locks the disconnection so a merge can't
    # silently re-introduce it. NOTE: scope to the DipLiveEngine class — the
    # module also hosts StrategyLiveEngine (Task #19), a SEPARATE live path that
    # IS research-gated on purpose, so a whole-module scan would false-positive.
    le_src = inspect.getsource(le.DipLiveEngine)
    for banned in ("require_validation", "is_strategy_validated",
                   "validated_strategies", "AUTO-DISABLE"):
        assert banned not in le_src, f"research gate leaked back: {banned}"
    import inspect as _sig_inspect
    assert "require_validation" not in _sig_inspect.signature(
        le.DipLiveEngine.__init__).parameters


# ── 6a. Safety: emergency stop blocks ────────────────────────────────────────
def test_emergency_stop_blocks_entry():
    ex = _FakeExchange(change_pct=-2.50)
    eng = le.DipLiveEngine(exchange=ex, cooldown=ls.CooldownStore())
    rec = eng.evaluate(symbol="BTCUSDT", settings=_settings(),
                       open_trades=[], current_exposure=0.0,
                       global_gate_fn=_pass_gate, emergency_stop=True)
    assert rec.traded is False
    assert not ex.buy_calls


# ── 6a-bis. Emergency stop DEFAULTS OFF (must not auto-arm on a fresh load) ───
def test_emergency_stop_defaults_off():
    from risk import RiskSettings, GlobalRiskSettings
    # A freshly-constructed risk config must have emergency stop OFF — the
    # dashboard relies on this so a page refresh never silently halts trading.
    assert RiskSettings().emergency_stop is False
    assert GlobalRiskSettings().emergency_stop is False


# ── 6b. Safety: safe mode + not-connected block ──────────────────────────────
def test_safe_mode_and_disconnected_block_entry():
    ex = _FakeExchange(change_pct=-2.50)
    eng = le.DipLiveEngine(exchange=ex, cooldown=ls.CooldownStore())
    rec = eng.evaluate(symbol="BTCUSDT", settings=_settings(safe_mode=True),
                       open_trades=[], current_exposure=0.0,
                       global_gate_fn=_pass_gate)
    assert rec.traded is False and not ex.buy_calls

    ex2 = _FakeExchange(change_pct=-2.50, connected=False)
    eng2 = le.DipLiveEngine(exchange=ex2, cooldown=ls.CooldownStore())
    rec2 = eng2.evaluate(symbol="BTCUSDT", settings=_settings(),
                         open_trades=[], current_exposure=0.0,
                         global_gate_fn=_pass_gate)
    assert rec2.traded is False and not ex2.buy_calls


# ── 7. Safety: spending limit + global risk gate block ───────────────────────
def test_spending_limit_and_global_gate_block():
    ex = _FakeExchange(change_pct=-2.50, free=1000.0)
    eng = le.DipLiveEngine(exchange=ex, cooldown=ls.CooldownStore())
    # exposure already at the spending limit
    rec = eng.evaluate(symbol="BTCUSDT",
                       settings=_settings(bot_spending_limit_usdt=50.0),
                       open_trades=[], current_exposure=50.0,
                       global_gate_fn=_pass_gate)
    assert rec.traded is False and not ex.buy_calls

    ex2 = _FakeExchange(change_pct=-2.50, free=1000.0)
    eng2 = le.DipLiveEngine(exchange=ex2, cooldown=ls.CooldownStore())
    rec2 = eng2.evaluate(symbol="BTCUSDT", settings=_settings(),
                         open_trades=[], current_exposure=0.0,
                         global_gate_fn=lambda a, s: (False, "global cap"))
    assert rec2.traded is False and not ex2.buy_calls


# ── 8. Live engine manages its OWN open trade: TP and SL ─────────────────────
def test_engine_takes_profit_and_stops_loss_on_open_trade():
    closed = {}

    def _close(trade, price, reason):
        closed["trade"] = trade
        closed["price"] = price
        closed["reason"] = reason

    # take-profit: entry 100, price 101.30 ⇒ +1.30% ≥ 1.20%
    ex = _FakeExchange(price=101.60, change_pct=0.0)
    eng = le.DipLiveEngine(exchange=ex, cooldown=ls.CooldownStore(),
                           close_fn=_close)
    open_trade = {"id": "t1", "coin": "BTCUSDT", "type": "bot",
                  "manual": False, "status": "open", "side": "BUY",
                  "entry_price": 100.0, "invested": 50.0}
    rec = eng.evaluate(symbol="BTCUSDT", settings=_settings(),
                       open_trades=[open_trade], current_exposure=50.0,
                       global_gate_fn=_pass_gate)
    assert rec.decision == "SELL" and rec.traded is True
    assert closed.get("trade") is open_trade

    # stop-loss: entry 100, price 98 ⇒ −2.0% ≤ −0.20%
    closed.clear()
    ex2 = _FakeExchange(price=98.0, change_pct=0.0)
    eng2 = le.DipLiveEngine(exchange=ex2, cooldown=ls.CooldownStore(),
                            close_fn=_close)
    rec2 = eng2.evaluate(symbol="BTCUSDT", settings=_settings(),
                         open_trades=[dict(open_trade)], current_exposure=50.0,
                         global_gate_fn=_pass_gate)
    assert rec2.decision == "STOP_LOSS" and rec2.traded is True


# ── 8b. Safe mode still manages exits; exits survive klines/balance outages ──
def test_safe_mode_still_allows_exit_of_open_bot_position():
    closed = {}

    def _close(t, p, r):
        closed.update(trade=t, price=p, reason=r)

    ex = _FakeExchange(price=101.60, change_pct=0.0)  # +1.3% vs entry 100 ⇒ TP
    eng = le.DipLiveEngine(exchange=ex, cooldown=ls.CooldownStore(),
                           close_fn=_close)
    open_trade = {"id": "t1", "coin": "BTCUSDT", "type": "bot",
                  "manual": False, "status": "open", "side": "BUY",
                  "entry_price": 100.0, "invested": 50.0}
    rec = eng.evaluate(symbol="BTCUSDT", settings=_settings(safe_mode=True),
                       open_trades=[open_trade], current_exposure=50.0,
                       global_gate_fn=_pass_gate)
    assert rec.decision == "SELL" and rec.traded is True
    assert closed.get("trade") is open_trade


def test_stop_loss_fires_even_if_klines_and_balance_fail():
    closed = {}

    class _BrokenEx(_FakeExchange):
        def get_klines(self, *a, **k):
            raise RuntimeError("klines API down")

        def get_balance(self, *a, **k):
            raise RuntimeError("balance API down")

    ex = _BrokenEx(price=98.0, change_pct=0.0)        # −2% vs entry 100 ⇒ SL
    eng = le.DipLiveEngine(exchange=ex, cooldown=ls.CooldownStore(),
                           close_fn=lambda t, p, r: closed.update(trade=t))
    open_trade = {"id": "t1", "coin": "BTCUSDT", "type": "bot",
                  "manual": False, "status": "open", "side": "BUY",
                  "entry_price": 100.0, "invested": 50.0}
    rec = eng.evaluate(symbol="BTCUSDT", settings=_settings(),
                       open_trades=[open_trade], current_exposure=50.0,
                       global_gate_fn=_pass_gate)
    assert rec.decision == "STOP_LOSS" and rec.traded is True


# ── 8c. bot.py: dip mode is the ONLY live path (legacy worker.tick unreached) ─
def test_dip_mode_does_not_invoke_legacy_worker_tick():
    import bot as botmod
    from risk import GlobalRiskManager, GlobalRiskSettings

    class _RiskSettings:
        emergency_stop = False

    class _Risk:
        settings = _RiskSettings()

    class _Worker:
        def __init__(self):
            self.exchange = _FakeExchange(change_pct=+0.50)   # no dip ⇒ HOLD
            self.symbol = "BTCUSDT"
            self.manage_manual_trades = False
            self._session_trades = 0
            self.risk = _Risk()

        def tick(self, *a, **k):
            raise AssertionError("legacy worker.tick must not run in dip mode")

        def _close_position(self, *a, **k):
            pass

    w = _Worker()
    b = botmod.TradingBot(workers={"binance:BTCUSDT": w},
                          global_risk=GlobalRiskManager(GlobalRiskSettings()),
                          check_every=2, initial_balance=1000.0)
    assert b.dip_mode is True
    traded = b._run_dip_cycle(lambda: ([], 0.0))   # would raise if tick ran
    assert traded is False                          # positive change ⇒ HOLD


# ── 9. Safety: 30-minute stop-loss cooldown ──────────────────────────────────
def test_stop_loss_cooldown_blocks_for_one_minute():
    now = datetime.now(timezone.utc)
    state = {"last_stop_loss_at": now}
    s = _settings()
    assert s.stop_loss_cooldown_sec == 60        # FINAL RULE: 1 minute
    blocked, why = le.cooldown_block(s, state, now=now + timedelta(seconds=30))
    assert blocked and "cooldown" in why.lower()
    # after 1 minute it clears
    ok, _ = le.cooldown_block(s, state, now=now + timedelta(minutes=2))
    assert ok is False


def test_reentry_cooldown_blocks_for_one_minute_after_a_sell():
    now = datetime.now(timezone.utc)
    state = {"last_sell_at": now}
    s = _settings()
    assert s.reentry_cooldown_sec == 60          # FINAL RULE: 1 minute
    blocked, why = le.cooldown_block(s, state, now=now + timedelta(seconds=30))
    assert blocked and "cooldown" in why.lower()
    ok, _ = le.cooldown_block(s, state, now=now + timedelta(minutes=2))
    assert ok is False


# ── 8d. Filters gate ENTRY only — an open position's exit still fires ─────────
def test_exit_fires_even_with_entry_filters_on():
    closed = {}

    def _close(t, p, r):
        closed.update(trade=t, price=p, reason=r)

    # +1.6% vs entry 100 ⇒ TP. Filters ON; the plain-close fake exposes no
    # volume column, but the exit branch runs BEFORE any entry filter, so TP
    # must still close the position.
    ex = _FakeExchange(price=101.60, change_pct=0.0)
    eng = le.DipLiveEngine(exchange=ex, cooldown=ls.CooldownStore(),
                           close_fn=_close)
    s = _settings()
    s.volume_filter_on = True
    s.trend_filter_on = True
    open_trade = {"id": "t1", "coin": "BTCUSDT", "type": "bot",
                  "manual": False, "status": "open", "side": "BUY",
                  "entry_price": 100.0, "invested": 50.0}
    rec = eng.evaluate(symbol="BTCUSDT", settings=s,
                       open_trades=[open_trade], current_exposure=50.0,
                       global_gate_fn=_pass_gate)
    assert rec.decision == "SELL" and rec.traded is True
    assert closed.get("trade") is open_trade


# ── 8e. Volume gate is fully DISABLED when min_volume_multiple <= 0 ───────────
def test_volume_gate_disabled_when_multiple_zero():
    # Clear dip BUY, volume filter ON, but min_volume_multiple = 0 ⇒ the gate
    # must NOT block even though the fake exposes no volume (ratio 0.0×).
    ex = _FakeExchange(change_pct=-2.50)
    eng = le.DipLiveEngine(exchange=ex, cooldown=ls.CooldownStore())
    s = _settings(buy_threshold_pct=-0.20, volume_filter_on=True,
                  min_volume_multiple=0.0)
    rec = eng.evaluate(symbol="BTCUSDT", settings=s,
                       open_trades=[], current_exposure=0.0,
                       global_gate_fn=_pass_gate)
    assert rec.traded is True and ex.buy_calls

    # A positive required multiple re-enables the gate; with no volume data the
    # BUY is blocked for "volume too low".
    ex2 = _FakeExchange(change_pct=-2.50)
    eng2 = le.DipLiveEngine(exchange=ex2, cooldown=ls.CooldownStore())
    s2 = _settings(buy_threshold_pct=-0.20, volume_filter_on=True,
                   min_volume_multiple=1.5)
    rec2 = eng2.evaluate(symbol="BTCUSDT", settings=s2,
                         open_trades=[], current_exposure=0.0,
                         global_gate_fn=_pass_gate)
    assert rec2.traded is False and not ex2.buy_calls
    assert "volume" in (rec2.reason or "").lower()


# ── 9b. Pure entry-quality filters: volume spike + trend upturn ──────────────
def test_volume_ok_requires_a_spike():
    # last bar 2.0 vs prior avg 1.0 ⇒ 2.0× ≥ 1.5× ⇒ pass
    ok, ratio = dip.volume_ok([1.0] * 20 + [2.0], multiple=1.5)
    assert ok is True and ratio >= 1.5
    # last bar 1.2 vs prior avg 1.0 ⇒ 1.2× < 1.5× ⇒ block
    ok2, ratio2 = dip.volume_ok([1.0] * 20 + [1.2], multiple=1.5)
    assert ok2 is False and ratio2 < 1.5
    # missing / too-little data ⇒ fail OPEN (never blocks)
    assert dip.volume_ok([])[0] is True
    assert dip.volume_ok([5.0])[0] is True


def test_trend_ok_requires_an_upturn():
    assert dip.trend_ok([100.0, 99.0, 99.5]) is True     # last close rising
    assert dip.trend_ok([100.0, 99.5, 99.0]) is False    # last close falling
    assert dip.trend_ok([100.0]) is True                 # too little data ⇒ open


# ── 10. Settings round-trip + aggressive default ON ──────────────────────────
def test_settings_defaults_and_persistence():
    s = ls.LiveSettings()
    assert s.aggressive_on is True                  # aggressive default ON
    assert s.size_mode in ls.SIZE_MODES
    assert s.buy_threshold_pct == -0.05
    assert s.take_profit_pct == 0.60
    assert s.stop_loss_pct == -0.30
    # FINAL RULE knobs — volume + trend filters ON (Scanner Market-Low spec)
    assert s.volume_filter_on is True
    assert s.min_volume_multiple == 1.0     # volume gate active (≥ 1× avg)
    assert s.trend_filter_on is True
    assert s.max_position_pct == 50.0
    assert s.stop_loss_cooldown_sec == 60
    assert s.reentry_cooldown_sec == 60
    # from_dict ignores unknown keys and preserves known ones
    s2 = ls.LiveSettings.from_dict({"size_mode": "FIXED_USDT",
                                    "aggressive_on": False, "bogus": 1})
    assert s2.size_mode == "FIXED_USDT" and s2.aggressive_on is False

    if not ls.db_available():
        pytest.skip("Postgres unavailable — skipping DB round-trip")
    saved = ls.save_settings(_settings(fixed_usdt_amount=33.0,
                                       size_mode="FIXED_USDT"),
                             actor="pytest")
    assert saved is True
    got = ls.get_settings()
    assert got.size_mode == "FIXED_USDT"
    assert got.fixed_usdt_amount == 33.0
