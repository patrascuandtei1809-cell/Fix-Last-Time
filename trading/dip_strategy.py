"""20-Minute Dip-Buy / Profit-Take strategy — PURE decision core.

This module is intentionally self-contained: it imports NOTHING from the old
strategy / scoring / AI / research code. It implements exactly one rule set:

  • BUY        when the 20-minute price change ≤  BUY_THRESHOLD_PCT   (−2.00%)
               AND volume ≥ MIN_VOLUME_MULTIPLE × avg  AND trend filter OK
  • SELL (TP)  when open-position profit       ≥  TAKE_PROFIT_PCT     (+1.50%)
  • STOP-LOSS  when open-position loss          ≤  STOP_LOSS_PCT       (−0.01%)
  • otherwise HOLD with a human-readable reason.

percent_change = ((current_price − price_20m_ago) / price_20m_ago) * 100

Every function here is pure (no I/O, no global state) so the rule can be unit
tested deterministically. The live engine (`live_engine.py`) wires these pure
functions to the exchange, balance, limits, cooldown and order placement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

# ── Hard-coded rule constants (defaults; operator may override thresholds) ────
BUY_THRESHOLD_PCT: float = -2.00     # BUY when 20m change ≤ this
TAKE_PROFIT_PCT: float = 1.50        # SELL when profit ≥ this
STOP_LOSS_PCT: float = -0.01         # STOP-LOSS when loss ≤ this (negative)
LOOKBACK_MINUTES: int = 20           # window for the % change
MIN_VOLUME_MULTIPLE: float = 1.5     # last-candle volume ≥ this × avg to BUY

# Decision verbs
BUY = "BUY"
SELL = "SELL"
STOP_LOSS = "STOP_LOSS"
HOLD = "HOLD"


@dataclass
class DipThresholds:
    """Operator-tunable thresholds. Defaults match the spec exactly."""
    buy_pct: float = BUY_THRESHOLD_PCT
    take_profit_pct: float = TAKE_PROFIT_PCT
    stop_loss_pct: float = STOP_LOSS_PCT
    lookback_minutes: int = LOOKBACK_MINUTES


@dataclass
class DipDecision:
    """Result of a pure decision. `action` is one of BUY/SELL/STOP_LOSS/HOLD."""
    action: str
    reason: str
    change_pct: Optional[float] = None     # 20m % change (entry context)
    profit_pct: Optional[float] = None     # open-position P&L % (exit context)


def percent_change(current_price: float, reference_price: float) -> float:
    """((current − reference) / reference) * 100. Raises on a non-positive ref."""
    if reference_price is None or reference_price <= 0:
        raise ValueError("reference_price must be > 0")
    return (float(current_price) - float(reference_price)) / float(reference_price) * 100.0


def closes_from_klines(df) -> List[float]:
    """Extract a list of close prices (oldest→newest) from a klines DataFrame.

    Accepts a pandas DataFrame with a 'close' column, or any sequence already
    holding closes. Returns a plain list of floats.
    """
    if df is None:
        return []
    # pandas DataFrame
    close_col = getattr(df, "get", None)
    if hasattr(df, "columns") and "close" in getattr(df, "columns", []):
        return [float(x) for x in df["close"].tolist()]
    # Already a sequence of numbers
    try:
        return [float(x) for x in df]
    except (TypeError, ValueError):
        return []


def volumes_from_klines(df) -> List[float]:
    """Extract a list of bar volumes (oldest→newest) from a klines DataFrame.

    Returns [] when no volume column / data exists (callers fail-OPEN on this so
    a missing-volume feed never blocks trading — the volume rule is a quality
    filter, not a money-safety gate).
    """
    if df is None:
        return []
    if hasattr(df, "columns") and "volume" in getattr(df, "columns", []):
        return [float(x) for x in df["volume"].tolist()]
    return []


def volume_ok(volumes: Sequence[float],
              multiple: float = MIN_VOLUME_MULTIPLE,
              lookback: int = LOOKBACK_MINUTES):
    """Return (ok, ratio). ok=True when the latest bar's volume ≥ `multiple` ×
    the average of the prior `lookback` bars. Fails OPEN (ok=True, ratio=0.0)
    when there isn't enough volume data."""
    if volumes is None or len(volumes) < 2:
        return True, 0.0
    recent = float(volumes[-1])
    prior = list(volumes[-(lookback + 1):-1]) or list(volumes[:-1])
    if not prior:
        return True, 0.0
    avg = sum(float(x) for x in prior) / len(prior)
    if avg <= 0:
        return True, 0.0
    ratio = recent / avg
    return (ratio >= float(multiple)), ratio


def trend_ok(closes: Sequence[float], lookback: int = LOOKBACK_MINUTES) -> bool:
    """Dip-buy trend filter: confirm a short-term upturn so the bot buys a
    bouncing dip, not a free-falling knife. True when the latest close ≥ the
    previous close. Fails OPEN when there isn't enough history."""
    if closes is None or len(closes) < 2:
        return True
    return float(closes[-1]) >= float(closes[-2])


def price_lookback(closes: Sequence[float], lookback: int = LOOKBACK_MINUTES) -> Optional[float]:
    """Return the close `lookback` bars before the latest one (1m candles ⇒ the
    price `lookback` minutes ago). Returns None if there isn't enough history.
    """
    if closes is None:
        return None
    n = len(closes)
    if n < lookback + 1:
        return None
    return float(closes[-(lookback + 1)])


def compute_change(closes: Sequence[float],
                   lookback: int = LOOKBACK_MINUTES):
    """Return (current_price, price_lookback_ago, change_pct).

    Raises ValueError when there is not enough candle history.
    """
    if closes is None or len(closes) < lookback + 1:
        raise ValueError(
            f"need ≥ {lookback + 1} candles, got {0 if closes is None else len(closes)}")
    current = float(closes[-1])
    ref = float(closes[-(lookback + 1)])
    return current, ref, percent_change(current, ref)


def position_profit_pct(entry_price: float, current_price: float,
                        side: str = BUY) -> float:
    """Open-position P&L % for a spot BUY (long). SELL (short) symmetric."""
    if entry_price is None or entry_price <= 0:
        raise ValueError("entry_price must be > 0")
    if side == SELL:
        return (float(entry_price) - float(current_price)) / float(entry_price) * 100.0
    return (float(current_price) - float(entry_price)) / float(entry_price) * 100.0


def decide_entry(change_pct: float,
                 thresholds: DipThresholds = None) -> DipDecision:
    """No position open → BUY on a sufficient dip, else HOLD."""
    t = thresholds or DipThresholds()
    if change_pct <= t.buy_pct:
        return DipDecision(
            action=BUY,
            reason=(f"Dip detected — 20m change {change_pct:+.3f}% ≤ "
                    f"{t.buy_pct:+.2f}% buy threshold"),
            change_pct=change_pct,
        )
    return DipDecision(
        action=HOLD,
        reason=(f"Waiting for dip — 20m change {change_pct:+.3f}% not low "
                f"enough (need ≤ {t.buy_pct:+.2f}%)"),
        change_pct=change_pct,
    )


def decide_exit(entry_price: float, current_price: float,
                side: str = BUY,
                thresholds: DipThresholds = None) -> DipDecision:
    """Position open → SELL at take-profit, STOP_LOSS at the stop, else HOLD."""
    t = thresholds or DipThresholds()
    profit = position_profit_pct(entry_price, current_price, side)
    if profit >= t.take_profit_pct:
        return DipDecision(
            action=SELL,
            reason=(f"Take profit — {profit:+.3f}% ≥ +{t.take_profit_pct:.2f}% "
                    f"target"),
            profit_pct=profit,
        )
    if profit <= t.stop_loss_pct:
        return DipDecision(
            action=STOP_LOSS,
            reason=(f"Stop loss — {profit:+.3f}% ≤ {t.stop_loss_pct:+.2f}% "
                    f"limit"),
            profit_pct=profit,
        )
    return DipDecision(
        action=HOLD,
        reason=(f"Holding — {profit:+.3f}% (TP +{t.take_profit_pct:.2f}% / "
                f"SL {t.stop_loss_pct:+.2f}%)"),
        profit_pct=profit,
    )
