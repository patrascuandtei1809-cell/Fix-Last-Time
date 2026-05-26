"""Risk management — two tiers.

  • SymbolRiskSettings / RiskManager   — applied independently per (symbol, exchange).
  • GlobalRiskSettings  / GlobalRiskManager — applied ACROSS all symbols.

The legacy names `RiskSettings` / `RiskManager` are kept as aliases for backward
compatibility with the old single-symbol bot and the dashboard.
"""
from dataclasses import dataclass, field
from typing import Tuple, List, Optional, Dict
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# Per-symbol risk
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SymbolRiskSettings:
    # ── Stop / Take-profit ────────────────────────────────────────────────────
    # Defaults tuned for 1m scalping on BTC/ETH/SOL: tight SL, modest TP that
    # still clears Binance Spot round-trip fees (~0.2%) with margin.
    stop_loss_pct:    float = 0.5
    take_profit_pct:  float = 1.5
    max_open_trades:  int   = 2          # max open trades for THIS symbol
    max_per_symbol:   int   = 1          # 1 trade per symbol at a time (no stacking)
    cooldown_seconds: int   = 180        # min seconds between bot trades on this symbol
    emergency_stop:   bool  = False      # per-symbol kill switch

    # ── Position sizing (FIXED USDT) ──────────────────────────────────────────
    # Small fixed size for scalping — many small trades, controlled risk.
    invest_per_trade:      float = 12.0
    max_trade_usdt:        float = 15.0
    max_trades_per_session: int  = 0     # 0 = unlimited

    # ── Legacy (kept for backwards compat with old settings.json) ─────────────
    max_daily_loss_pct: float = 5.0      # superseded by GlobalRiskSettings
    risk_per_trade_pct: float = 2.0      # not used


# Back-compat alias — old code imports `RiskSettings`
RiskSettings = SymbolRiskSettings


class RiskManager:
    """Per-symbol risk gate. One instance per (exchange, symbol) worker."""

    def __init__(self, settings: SymbolRiskSettings = None):
        self.settings = settings or SymbolRiskSettings()

    def update(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self.settings, k):
                setattr(self.settings, k, v)

    def get_invest_amount(self) -> float:
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
            return True, (f"Stop loss hit: {pct:.2f}% (limit −{self.settings.stop_loss_pct}%) "
                          f"| Price {current:.4f} ≤ SL {sl:.4f}")
        if side == "SELL" and current >= sl:
            pct = (current - entry) / entry * 100
            return True, (f"Stop loss hit: +{pct:.2f}% against short "
                          f"| Price {current:.4f} ≥ SL {sl:.4f}")
        return False, ""

    def check_take_profit(self, entry: float, current: float, side: str) -> Tuple[bool, str]:
        tp = self.take_profit_price(entry, side)
        if side == "BUY" and current >= tp:
            pct = (current - entry) / entry * 100
            return True, (f"Take profit hit: +{pct:.2f}% (target +{self.settings.take_profit_pct}%) "
                          f"| Price {current:.4f} ≥ TP {tp:.4f}")
        if side == "SELL" and current <= tp:
            pct = (entry - current) / entry * 100
            return True, (f"Take profit hit: +{pct:.2f}% short gain "
                          f"| Price {current:.4f} ≤ TP {tp:.4f}")
        return False, ""

    def can_open_trade(
        self,
        open_trades_for_symbol: Optional[List[dict]] = None,
        symbol:          Optional[str]      = None,
        new_signal:      Optional[str]      = None,
        last_trade_at:   Optional[datetime] = None,
        last_trade_dir:  Optional[str]      = None,
        session_count:   int                = 0,
    ) -> Tuple[bool, str]:
        """Per-symbol gate. Global gate (daily loss, total exposure) is handled
        separately by GlobalRiskManager.check_global() — call that first."""
        if self.settings.emergency_stop:
            return False, "🚨 Per-symbol emergency stop active"

        ot = open_trades_for_symbol or []

        if len(ot) >= self.settings.max_per_symbol:
            return False, (f"Symbol cap reached for {symbol} "
                           f"({len(ot)}/{self.settings.max_per_symbol}) — no stacking")

        # Per-symbol absolute max (legacy "Max open trades" slider in dashboard)
        if self.settings.max_open_trades > 0 and len(ot) >= self.settings.max_open_trades:
            return False, (f"Max open trades reached for {symbol} "
                           f"({len(ot)}/{self.settings.max_open_trades})")

        if last_trade_at is not None and self.settings.cooldown_seconds > 0:
            elapsed = (datetime.now() - last_trade_at).total_seconds()
            if elapsed < self.settings.cooldown_seconds:
                wait = int(self.settings.cooldown_seconds - elapsed)
                return False, f"Cooldown active on {symbol} — {wait}s until next entry"

        if last_trade_dir is not None and new_signal is not None and last_trade_dir == new_signal:
            return False, (f"Direction lock on {symbol} — last trade was {last_trade_dir}, "
                           f"need opposite signal")

        max_sess = self.settings.max_trades_per_session
        if max_sess > 0 and session_count >= max_sess:
            return False, f"Session trade limit reached for {symbol} ({session_count}/{max_sess})"

        return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# Global risk (applied across ALL symbols)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class GlobalRiskSettings:
    max_total_exposure_usdt:    float = 300.0   # sum of all open invested USDT
    max_exposure_per_symbol_pct: float = 50.0   # no single symbol > X% of total exposure
    max_daily_loss_pct:         float = 5.0     # auto-halt if today P&L ≤ -X% of initial balance
    max_open_trades_total:      int   = 5       # hard cap across all symbols
    emergency_stop:             bool  = False   # GLOBAL kill switch


class GlobalRiskManager:
    """Cross-symbol gate. Called by orchestrator BEFORE each per-symbol tick."""

    def __init__(self, settings: GlobalRiskSettings = None):
        self.settings = settings or GlobalRiskSettings()

    def update(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self.settings, k):
                setattr(self.settings, k, v)

    def check_global(
        self,
        all_open_trades: List[dict],
        new_invest_usdt: float,
        new_symbol:      str,
        daily_loss_pct:  float = 0.0,
    ) -> Tuple[bool, str]:
        s = self.settings

        if s.emergency_stop:
            return False, "🚨 GLOBAL emergency stop active — all symbols halted"

        if daily_loss_pct <= -abs(s.max_daily_loss_pct):
            return False, (f"🛑 Daily loss limit hit ({daily_loss_pct:+.2f}% ≤ "
                           f"−{s.max_daily_loss_pct}%) — bot auto-stops")

        n_open = len(all_open_trades)
        if n_open >= s.max_open_trades_total:
            return False, (f"Global open-trade cap reached "
                           f"({n_open}/{s.max_open_trades_total})")

        current_exposure = sum((t.get("invested") or 0) for t in all_open_trades)
        if current_exposure + new_invest_usdt > s.max_total_exposure_usdt:
            return False, (f"Total exposure cap — ${current_exposure:.2f} + "
                           f"${new_invest_usdt:.2f} > ${s.max_total_exposure_usdt:.2f}")

        # Per-symbol concentration cap
        sym_exposure = sum((t.get("invested") or 0) for t in all_open_trades
                           if t.get("coin") == new_symbol)
        projected_total = current_exposure + new_invest_usdt
        if projected_total > 0:
            projected_sym = sym_exposure + new_invest_usdt
            pct = projected_sym / projected_total * 100
            if pct > s.max_exposure_per_symbol_pct:
                return False, (f"{new_symbol} concentration cap — would be {pct:.0f}% "
                               f"of total exposure (max {s.max_exposure_per_symbol_pct:.0f}%)")

        return True, ""

    def snapshot(self, all_open_trades: List[dict]) -> Dict:
        """Read-only summary for the dashboard."""
        total_exp = sum((t.get("invested") or 0) for t in all_open_trades)
        per_sym: Dict[str, float] = {}
        for t in all_open_trades:
            per_sym[t.get("coin", "?")] = per_sym.get(t.get("coin", "?"), 0) + (t.get("invested") or 0)
        return {
            "total_exposure":         total_exp,
            "max_total_exposure":     self.settings.max_total_exposure_usdt,
            "exposure_pct":           (total_exp / self.settings.max_total_exposure_usdt * 100)
                                       if self.settings.max_total_exposure_usdt else 0,
            "per_symbol_exposure":    per_sym,
            "open_trades_count":      len(all_open_trades),
            "max_open_trades_total":  self.settings.max_open_trades_total,
            "emergency_stop":         self.settings.emergency_stop,
        }
