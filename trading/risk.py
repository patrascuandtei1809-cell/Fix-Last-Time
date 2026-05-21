from dataclasses import dataclass
from typing import Tuple


@dataclass
class RiskSettings:
    # ── Stop / Take-profit ────────────────────────────────────────────────────
    stop_loss_pct:    float = 2.0
    take_profit_pct:  float = 4.0
    max_daily_loss_pct: float = 5.0
    max_open_trades:  int   = 3
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

    def can_open_trade(self, open_count: int, session_count: int = 0) -> Tuple[bool, str]:
        if self.settings.emergency_stop:
            return False, "🚨 Emergency stop active — all trading halted"
        if open_count >= self.settings.max_open_trades:
            return False, f"Max open trades reached ({open_count}/{self.settings.max_open_trades})"
        max_sess = self.settings.max_trades_per_session
        if max_sess > 0 and session_count >= max_sess:
            return False, f"Session trade limit reached ({session_count}/{max_sess})"
        return True, ""
