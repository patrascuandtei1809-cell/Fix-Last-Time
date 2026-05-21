from dataclasses import dataclass
from typing import Tuple


@dataclass
class RiskSettings:
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0
    risk_per_trade_pct: float = 2.0
    max_daily_loss_pct: float = 5.0
    max_open_trades: int = 3
    emergency_stop: bool = False


class RiskManager:
    def __init__(self, settings: RiskSettings = None):
        self.settings = settings or RiskSettings()

    def update(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self.settings, k):
                setattr(self.settings, k, v)

    def calculate_quantity(self, balance: float, price: float) -> float:
        invested = balance * (self.settings.risk_per_trade_pct / 100)
        return invested / price if price > 0 else 0.0

    def calculate_invested(self, balance: float) -> float:
        return balance * (self.settings.risk_per_trade_pct / 100)

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
            return True, f"Stop loss hit: +{pct:.2f}% against short (limit −{self.settings.stop_loss_pct}%) | Price {current:.4f} ≥ SL {sl:.4f}"
        return False, ""

    def check_take_profit(self, entry: float, current: float, side: str) -> Tuple[bool, str]:
        tp = self.take_profit_price(entry, side)
        if side == "BUY" and current >= tp:
            pct = (current - entry) / entry * 100
            return True, f"Take profit hit: +{pct:.2f}% (target +{self.settings.take_profit_pct}%) | Price {current:.4f} ≥ TP {tp:.4f}"
        if side == "SELL" and current <= tp:
            pct = (entry - current) / entry * 100
            return True, f"Take profit hit: +{pct:.2f}% short gain (target +{self.settings.take_profit_pct}%) | Price {current:.4f} ≤ TP {tp:.4f}"
        return False, ""

    def can_open_trade(self, open_count: int) -> Tuple[bool, str]:
        if self.settings.emergency_stop:
            return False, "🚨 Emergency stop active — all trading halted"
        if open_count >= self.settings.max_open_trades:
            return False, f"Max open trades reached ({open_count}/{self.settings.max_open_trades})"
        return True, ""
