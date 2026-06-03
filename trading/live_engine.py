"""LIVE 20-Minute Dip engine — the ONLY path that places real orders.

This wires the pure rules in `dip_strategy.py` to the exchange, balance, limits,
cooldown and order placement. It runs ONE fixed ordered sequence per symbol and
ignores every legacy gate (research/allowlist, confidence floors, weighted
scoring, score thresholds, ranking, GPT/AI veto, anti-idle, EMA/regime). Those
modules stay importable but are never called from here.

Fixed order (Task #11):
  1. emergency stop
  2. safe mode
  3. exchange / provider status (client + price + klines)
  4. balance
  5. spending limit
  6. max position size
  7. position already open
  8. cooldown
  9. compute 20-minute % change
 10. decision — BUY (≤ −0.10%) / SELL (≥ +0.80%) / STOP-LOSS (≤ −1.50%)
 11. position sizing (AUTO / FIXED_USDT / PORTFOLIO_PERCENT)
 12. place the LIVE order, record the trade, emit the activity record

Every decision is recorded as a per-symbol `ActivityRecord` (UTC-aware) for the
dashboard activity panel. The engine never raises out of `evaluate()` — any
unexpected error degrades to a SKIP record so the loop stays alive.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import dip_strategy as dip
from live_settings import (
    LiveSettings, CooldownStore,
    SIZE_AUTO, SIZE_FIXED, SIZE_PERCENT,
)

BINANCE_MIN_NOTIONAL = 10.0
RESERVE_FRACTION = 0.75          # never deploy more than 75% of free USDT


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ActivityRecord:
    """One per-symbol evaluation snapshot for the dashboard activity panel."""
    symbol: str
    at: datetime = field(default_factory=_utcnow)
    price: Optional[float] = None
    price_20m_ago: Optional[float] = None
    change_pct: Optional[float] = None
    buy_threshold: float = dip.BUY_THRESHOLD_PCT
    take_profit: float = dip.TAKE_PROFIT_PCT
    stop_loss: float = dip.STOP_LOSS_PCT
    aggressive: bool = True
    size_mode: str = SIZE_AUTO
    amount: float = 0.0              # the order amount used / previewed (USDT)
    free_usdt: Optional[float] = None
    decision: str = "HOLD"          # BUY / SELL / STOP_LOSS / HOLD / SKIP
    reason: str = ""                # no-trade reason or action explanation
    traded: bool = False            # a LIVE order was placed this evaluation

    def to_dict(self) -> dict:
        d = asdict(self)
        d["at"] = self.at.isoformat()
        return d


# ── Module-level activity store (read by the dashboard) ──────────────────────
_ACTIVITY: Dict[str, ActivityRecord] = {}
_ACT_LOCK = threading.Lock()


def _publish(rec: ActivityRecord) -> ActivityRecord:
    with _ACT_LOCK:
        _ACTIVITY[rec.symbol] = rec
    return rec


def get_activity(symbol: str) -> Optional[ActivityRecord]:
    with _ACT_LOCK:
        return _ACTIVITY.get(symbol)


def get_all_activity() -> List[ActivityRecord]:
    with _ACT_LOCK:
        return list(_ACTIVITY.values())


# ── Position sizing ──────────────────────────────────────────────────────────
def compute_order_amount(settings: LiveSettings, free_usdt: float,
                         current_exposure: float):
    """Return (amount, ok, reason).

    Applies the selected size mode, then the operator's max-position cap, the
    spending-limit remaining budget, the 25% free-balance reserve, and finally
    floors UP to the Binance $10 min-notional. ok=False ⇒ cannot place a trade.
    """
    free_usdt = max(0.0, float(free_usdt or 0.0))
    mode = settings.size_mode

    if mode == SIZE_FIXED:
        amount = float(settings.fixed_usdt_amount)
    elif mode == SIZE_PERCENT:
        amount = free_usdt * float(settings.portfolio_percent) / 100.0
    else:  # SIZE_AUTO — aggressive uses a larger default slice
        pct = float(settings.auto_percent)
        if settings.aggressive_on:
            pct = max(pct, 40.0)
        amount = free_usdt * pct / 100.0

    # Operator max-position cap (0 = disabled)
    if settings.max_position_size_usdt and settings.max_position_size_usdt > 0:
        amount = min(amount, float(settings.max_position_size_usdt))

    # Bot spending limit — remaining budget (0 = unlimited)
    if settings.bot_spending_limit_usdt and settings.bot_spending_limit_usdt > 0:
        remaining = float(settings.bot_spending_limit_usdt) - float(current_exposure or 0.0)
        if remaining <= 0:
            return 0.0, False, (
                f"Spending limit reached — ${current_exposure:.2f} / "
                f"${settings.bot_spending_limit_usdt:.2f} deployed")
        amount = min(amount, remaining)

    # Never deploy more than 75% of free balance (always leave a buffer).
    ceiling = free_usdt * RESERVE_FRACTION
    amount = min(amount, ceiling)

    # Floor UP to the larger of the operator min-trade size and Binance min-notional.
    floor = max(float(settings.min_trade_size_usdt or 0.0), BINANCE_MIN_NOTIONAL)
    if amount < floor:
        # Only float up if the reserve ceiling can actually fund the floor.
        if ceiling >= floor:
            amount = floor
        else:
            return 0.0, False, (
                f"Insufficient balance — need ≥ ${floor:.2f} (75% of "
                f"${free_usdt:.2f} free = ${ceiling:.2f})")

    return round(amount, 2), True, ""


def cooldown_block(settings: LiveSettings, state: Dict, now: datetime = None):
    """Return (blocked, reason). Enforces the 30-min stop-loss cooldown and a
    faster re-entry window after a (profitable) sell when aggressive."""
    now = now or _utcnow()

    # 1) Hard 30-minute stop-loss cooldown.
    sl_at = state.get("last_stop_loss_at")
    if sl_at is not None:
        if sl_at.tzinfo is None:
            sl_at = sl_at.replace(tzinfo=timezone.utc)
        elapsed = (now - sl_at).total_seconds()
        if elapsed < settings.stop_loss_cooldown_sec:
            left = int(settings.stop_loss_cooldown_sec - elapsed)
            return True, (f"Stop-loss cooldown — {left // 60}m {left % 60}s left "
                          f"(30m after a stop-loss)")

    # 2) Re-entry cadence after a sell. Faster (or instant) when profitable +
    #    aggressive; a normal flat/loss sell waits a little longer.
    sell_at = state.get("last_sell_at")
    if sell_at is not None:
        if sell_at.tzinfo is None:
            sell_at = sell_at.replace(tzinfo=timezone.utc)
        profitable = bool(state.get("last_sell_profit"))
        if settings.aggressive_on:
            reentry = 0 if profitable else 5
        else:
            reentry = 15 if profitable else 60
        elapsed = (now - sell_at).total_seconds()
        if reentry > 0 and elapsed < reentry:
            left = int(reentry - elapsed)
            return True, f"Re-entry cooldown — {left}s left"

    return False, ""


class DipLiveEngine:
    """Per-symbol LIVE evaluator. One instance is reused across ticks for a
    given exchange/symbol pair (collaborators injected for testability)."""

    def __init__(
        self,
        *,
        exchange,
        on_log: Optional[Callable] = None,
        on_state: Optional[Callable] = None,
        on_open_trade: Optional[Callable] = None,
        close_fn: Optional[Callable] = None,
        cooldown: Optional[CooldownStore] = None,
        manage_manual: bool = False,
    ):
        self.exchange = exchange
        self._log = on_log or (lambda *_a, **_kw: None)
        self._state = on_state or (lambda *_a, **_kw: None)
        self._open_trade = on_open_trade or (lambda t: t)
        self._close = close_fn or (lambda *_a, **_kw: None)
        self.cooldown = cooldown
        self.manage_manual = bool(manage_manual)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _skip(self, rec: ActivityRecord, reason: str, level: str = "INFO",
              decision: str = "SKIP") -> ActivityRecord:
        rec.decision = decision
        rec.reason = reason
        try:
            self._state(rec.symbol, block_reason=reason)
        except Exception:
            pass
        self._log(level, f"[{rec.symbol}] {reason}")
        return _publish(rec)

    # ── main evaluation (12-step path) ───────────────────────────────────────
    def evaluate(self, *, symbol: str, settings: LiveSettings,
                 open_trades: List[Dict], current_exposure: float,
                 global_gate_fn: Callable,
                 daily_loss_pct: float = 0.0,
                 emergency_stop: bool = False) -> ActivityRecord:
        thr = dip.DipThresholds(
            buy_pct=settings.buy_threshold_pct,
            take_profit_pct=settings.take_profit_pct,
            stop_loss_pct=settings.stop_loss_pct,
            lookback_minutes=int(settings.lookback_minutes),
        )
        rec = ActivityRecord(
            symbol=symbol,
            buy_threshold=thr.buy_pct,
            take_profit=thr.take_profit_pct,
            stop_loss=thr.stop_loss_pct,
            aggressive=settings.aggressive_on,
            size_mode=settings.size_mode,
        )
        try:
            return self._evaluate(rec, symbol, thr, settings, open_trades,
                                  current_exposure, global_gate_fn,
                                  daily_loss_pct, emergency_stop)
        except Exception as exc:                      # never crash the loop
            return self._skip(rec, f"Dip engine error: {exc}", level="ERROR")

    def _evaluate(self, rec, symbol, thr, settings, open_trades,
                  current_exposure, global_gate_fn, daily_loss_pct,
                  emergency_stop) -> ActivityRecord:
        # Step order follows the spec exactly for the ENTRY pipeline. The ONE
        # deliberate, money-safety deviation: managing an already-open bot
        # position's exit (TAKE-PROFIT / STOP-LOSS) is done as early as possible
        # — right after the price is known — so a -1.50% stop-loss can ALWAYS
        # fire. The entry-only gates (safe mode, balance, spending limit, max
        # position size, cooldown, 20m change) must never be able to strand an
        # open position without its stop-loss. An exit needs only the price.

        # 1. Emergency stop — true halt, blocks EVERYTHING (incl. exits).
        if emergency_stop:
            return self._skip(rec, "🛑 Emergency stop active — no trading",
                              level="WARNING")

        # 3. Exchange / provider status — price is required for both exit & entry.
        if getattr(self.exchange, "client", None) is None:
            return self._skip(rec, "Exchange error — not connected (no API key)",
                              level="WARNING")
        try:
            price = float(self.exchange.get_price(symbol))
            rec.price = price
            self._state(symbol, price=price, tick=True)
        except Exception as e:
            return self._skip(rec, f"Exchange error — price fetch failed: {e}",
                              level="ERROR")

        # 7. Position already open?  (manual protection: only manage own trades.)
        # EXIT FIRST — uses price only, never blocked by safe mode / balance /
        # klines / spending / cooldown.
        sym_open_any = [
            t for t in open_trades
            if t.get("coin") == symbol and t.get("status", "open") == "open"
        ]
        sym_open_bot = [
            t for t in sym_open_any
            if self.manage_manual or (t.get("type") == "bot"
                                      and not t.get("manual", False))
        ]
        if sym_open_any:
            if sym_open_bot:
                return self._manage_exit(rec, symbol, sym_open_bot[0], price, thr)
            return self._skip(
                rec, "Position already open (manual) — bot will not manage it",
                decision="HOLD")

        # ── ENTRY PATH (no open position) ────────────────────────────────────
        # 2. Safe mode — blocks NEW ENTRIES only; open positions stay protected
        #    by the exit branch above.
        if settings.safe_mode:
            return self._skip(rec, "🦺 Safe mode ON — no new entries",
                              level="WARNING")

        # 9. Compute the 20m change (entry-only — drives the BUY decision).
        try:
            df = self.exchange.get_klines(symbol, "1m",
                                          limit=thr.lookback_minutes + 5)
            closes = dip.closes_from_klines(df)
        except Exception as e:
            return self._skip(rec, f"Exchange error — klines fetch failed: {e}",
                              level="ERROR")
        try:
            _cur, ref, change = dip.compute_change(closes, thr.lookback_minutes)
            rec.price_20m_ago = ref
            rec.change_pct = change
        except ValueError as e:
            return self._skip(rec, f"Not enough candle data — {e}")

        # 4. Balance
        try:
            bal = self.exchange.get_balance("USDT")
            free_usdt = float(bal.get("free", 0.0))
            rec.free_usdt = free_usdt
        except Exception as e:
            return self._skip(rec, f"Exchange error — balance fetch failed: {e}",
                              level="ERROR")

        # 5. Spending limit precheck (also enforced again in sizing).
        if settings.bot_spending_limit_usdt and settings.bot_spending_limit_usdt > 0:
            if current_exposure >= settings.bot_spending_limit_usdt:
                rec.amount = 0.0
                return self._skip(
                    rec,
                    f"Spending limit reached — ${current_exposure:.2f} / "
                    f"${settings.bot_spending_limit_usdt:.2f} deployed")

        # 8. Cooldown
        if self.cooldown is not None:
            state = self.cooldown.get(symbol)
            blocked, why = cooldown_block(settings, state)
            if blocked:
                return self._skip(rec, why)

        # 10. Decision (entry)
        decision = dip.decide_entry(rec.change_pct, thr)
        if decision.action != dip.BUY:
            rec.decision = "HOLD"
            rec.reason = decision.reason
            self._state(symbol, signal="HOLD", reason=decision.reason,
                        block_reason="")
            self._log("SIGNAL", f"[{symbol}] {decision.reason}")
            return _publish(rec)

        # 11. Sizing
        amount, ok, why = compute_order_amount(settings, free_usdt, current_exposure)
        rec.amount = amount
        if not ok:
            return self._skip(rec, why)

        # Global risk gate (emergency / exposure / open caps / daily loss).
        try:
            g_ok, g_reason = global_gate_fn(amount, symbol)
        except Exception as e:
            return self._skip(rec, f"Risk gate error: {e}", level="ERROR")
        if not g_ok:
            return self._skip(rec, f"Risk gate blocked — {g_reason}")

        # 12. Place the LIVE BUY order + record.
        return self._open_buy(rec, symbol, price, amount, decision.reason, thr)

    # ── order placement ──────────────────────────────────────────────────────
    def _open_buy(self, rec, symbol, price, amount, reason, thr) -> ActivityRecord:
        self._log("ORDER", f"[{symbol}] 🟢 BUY ${amount:.2f} — {reason}")
        try:
            resp = self.exchange.place_buy_order(symbol, amount)
        except Exception as e:
            return self._skip(rec, f"BUY order failed: {e}", level="ERROR")
        if not resp or not resp.get("ok"):
            err = (resp or {}).get("error", "unknown error")
            return self._skip(rec, f"BUY order rejected: {err}", level="ERROR")

        fill_price = float(resp.get("price") or price)
        qty = float(resp.get("qty") or 0.0)
        fee = float(resp.get("fee") or 0.0)
        invested = fill_price * qty
        sl_price = fill_price * (1.0 + thr.stop_loss_pct / 100.0)
        tp_price = fill_price * (1.0 + thr.take_profit_pct / 100.0)
        trade = {
            "coin": symbol,
            "exchange": getattr(self.exchange, "name", "binance"),
            "type": "bot",
            "manual": False,
            "strategy": "20-Minute Dip",
            "side": "BUY",
            "entry_price": fill_price,
            "exit_price": None,
            "quantity": qty,
            "invested": invested,
            "profit_loss": None,
            "entry_fee": fee,
            "open_time": _utcnow().isoformat(),
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "status": "open",
            "reason": reason,
        }
        try:
            self._open_trade(trade)
        except Exception as e:
            self._log("ERROR", f"[{symbol}] add_trade failed after fill: {e}")
        if self.cooldown is not None:
            self.cooldown.record_buy(symbol)
        rec.decision = "BUY"
        rec.reason = reason
        rec.amount = round(invested, 2)
        rec.price = fill_price
        rec.traded = True
        self._state(symbol, signal="BUY", reason=reason, block_reason="")
        return _publish(rec)

    def _manage_exit(self, rec, symbol, trade, price, thr) -> ActivityRecord:
        entry = float(trade.get("entry_price") or 0.0)
        side = trade.get("side", "BUY")
        decision = dip.decide_exit(entry, price, side, thr)
        rec.change_pct = rec.change_pct  # keep 20m context
        rec.amount = float(trade.get("invested") or 0.0)
        if decision.action == dip.HOLD:
            rec.decision = "HOLD"
            rec.reason = decision.reason
            self._state(symbol, signal="HOLD", reason=decision.reason,
                        block_reason="")
            return _publish(rec)

        is_stop = decision.action == dip.STOP_LOSS
        verb = "🔴 STOP LOSS" if is_stop else "🟢 TAKE PROFIT"
        self._log("ORDER", f"[{symbol}] {verb} | {trade.get('id')} | {decision.reason}")
        try:
            self._close(trade, price, decision.reason)
        except Exception as e:
            return self._skip(rec, f"Exit order failed: {e}", level="ERROR")

        # Record cooldown: 30-min hard stop after a stop-loss; faster re-entry
        # after a (profitable) take-profit sell.
        if self.cooldown is not None:
            if is_stop:
                self.cooldown.record_stop_loss(symbol)
            else:
                self.cooldown.record_sell(symbol, profitable=True)
        rec.decision = decision.action
        rec.reason = decision.reason
        rec.profit_pct = decision.profit_pct
        rec.traded = True
        self._state(symbol, signal="SELL", reason=decision.reason, block_reason="")
        return _publish(rec)
