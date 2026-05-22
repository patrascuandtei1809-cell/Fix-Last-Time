from dataclasses import dataclass
from typing import Tuple, List, Optional
from datetime import datetime, timedelta


@dataclass
class RiskSettings:
    # ── Stop / Take-profit ────────────────────────────────────────────────────
    stop_loss_pct:    float = 2.0
    take_profit_pct:  float = 4.0
    max_daily_loss_pct: float = 5.0   # auto-stop bot if today's loss ≥ this % of initial balance
    max_open_trades:  int   = 2        # ↓ from 3 — production-safe default
    max_per_symbol:   int   = 1        # 1 trade per symbol at a time (no stacking)
    cooldown_seconds: int   = 180      # min seconds between bot trades (≈2-3 candles on 1m)
    emergency_stop:   bool  = False

    # ── Position sizing (FIXED USDT — the only way trade size is calculated) ──
    invest_per_trade:      float = 50.0   # USDT to invest per trade (bot + manual)
    max_trade_usdt:        float = 100.0  # Hard cap — trade can NEVER exceed this
    max_trades_per_session: int  = 0      # 0 = unlimited

    # ── Legacy (kept for backwards compat but NOT used for sizing) ───────────
    risk_per_trade_pct: float = 2.0


class RiskManager:
    def __init__(self, settings: RiskSettings = None):
        self.settings = settings or RiskSettings()

    def update(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self.settings, k):
                setattr(self.settings, k, v)

    def get_invest_amount(self) -> float:
        """Return the capped USDT invest amount for this trade."""
        invest = self.settings.invest_per_trade
        if self.settings.max_trade_usdt > 0:
            invest = min(invest, self.settings.max_trade_usdt)
        return max(0.0, invest)

    def stop_loss_price(self, entry: float, side: str) -> float:
        if side == "BUY":
            return entry * (1 - self.settings.stop_loss_pct / 100)
        return entry * (1 + self.settings.stop_loss_pct / 100)

    def take_profit_price(self, entry: float, side: str) -> float:
        if side == "BUY":
            return entry * (1 + self.settings.take_profit_pct / 100)
        return entry * (1 - self.settings.take_profit_pct / 100)

    def check_stop_loss(self, entry: float, current: float, side: str) -> Tuple[bool, str]:
        sl = self.stop_loss_price(entry, side)
        if side == "BUY" and current <= sl:
            pct = (current - entry) / entry * 100
            return True, f"Stop loss hit: {pct:.2f}% (limit −{self.settings.stop_loss_pct}%) | Price {current:.4f} ≤ SL {sl:.4f}"
        if side == "SELL" and current >= sl:
            pct = (current - entry) / entry * 100
            return True, f"Stop loss hit: +{pct:.2f}% against short | Price {current:.4f} ≥ SL {sl:.4f}"
        return False, ""

    def check_take_profit(self, entry: float, current: float, side: str) -> Tuple[bool, str]:
        tp = self.take_profit_price(entry, side)
        if side == "BUY" and current >= tp:
            pct = (current - entry) / entry * 100
            return True, f"Take profit hit: +{pct:.2f}% (target +{self.settings.take_profit_pct}%) | Price {current:.4f} ≥ TP {tp:.4f}"
        if side == "SELL" and current <= tp:
            pct = (entry - current) / entry * 100
            return True, f"Take profit hit: +{pct:.2f}% short gain | Price {current:.4f} ≤ TP {tp:.4f}"
        return False, ""

    def can_open_trade(
        self,
        open_trades:     Optional[List[dict]] = None,
        symbol:          Optional[str]        = None,
        new_signal:      Optional[str]        = None,
        last_trade_at:   Optional[datetime]   = None,
        last_trade_dir:  Optional[str]        = None,
        daily_loss_pct:  float                = 0.0,
        session_count:   int                  = 0,
    ) -> Tuple[bool, str]:
        """All-in-one gate. Pass everything; this is the single decision point."""
        if self.settings.emergency_stop:
            return False, "🚨 Emergency stop active — all trading halted"

        # ── Daily-loss circuit breaker ────────────────────────────────────────
        if daily_loss_pct <= -abs(self.settings.max_daily_loss_pct):
            return False, (f"🛑 Daily loss limit hit ({daily_loss_pct:+.2f}% ≤ "
                           f"−{self.settings.max_daily_loss_pct}%) — bot will auto-stop")

        ot = open_trades or []
        n_open = len(ot)

        # ── Max open trades total ────────────────────────────────────────────
        if n_open >= self.settings.max_open_trades:
            return False, f"Max open trades reached ({n_open}/{self.settings.max_open_trades})"

        # ── Max per symbol (no stacking on same coin) ─────────────────────────
        if symbol is not None:
            n_sym = sum(1 for t in ot if t.get("coin") == symbol)
            if n_sym >= self.settings.max_per_symbol:
                return False, (f"Symbol cap reached for {symbol} "
                               f"({n_sym}/{self.settings.max_per_symbol}) — no stacking")

        # ── Cooldown between bot trades ───────────────────────────────────────
        if last_trade_at is not None and self.settings.cooldown_seconds > 0:
            elapsed = (datetime.now() - last_trade_at).total_seconds()
            if elapsed < self.settings.cooldown_seconds:
                wait = int(self.settings.cooldown_seconds - elapsed)
                return False, f"Cooldown active — {wait}s until next entry allowed"

        # ── No repeat in same direction (prevent stacking long/short streaks) ─
        if last_trade_dir is not None and new_signal is not None and last_trade_dir == new_signal:
            return False, (f"Direction lock — last trade was {last_trade_dir}, "
                           f"need opposite signal before another {new_signal}")

        # ── Session cap ───────────────────────────────────────────────────────
        max_sess = self.settings.max_trades_per_session
        if max_sess > 0 and session_count >= max_sess:
            return False, f"Session trade limit reached ({session_count}/{max_sess})"

        return True, ""
