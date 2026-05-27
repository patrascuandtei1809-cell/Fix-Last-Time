"""AI Decision Engine — ACTIVE SCALPER MODE (single hard-coded profile).

Design choice — DETERMINISTIC, NOT LLM:
  Deterministic multi-factor scoring engine. AI is ADVISORY ONLY — it never
  blocks a strategy BUY/SELL signal. The only HOLD this engine ever returns
  is when the market is truly motionless (no movement at all) or there is
  no data. Everything else gets a directional verdict (BUY or SELL) with
  a confidence score the dashboard surfaces.

Per operator spec (FULL RESET):
  - No aggressiveness profiles
  - AI is NOT allowed to block trades completely
  - HOLD only when absolutely no movement
  - No EMA hard veto, no "wait for perfect trend", no green-candle requirement
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


# ── ACTIVE SCALPER hard-coded constants ─────────────────────────────────────
ACTIVE_SCALPER = {
    "max_open":             3,    # global hard cap — 3 total (1/sym × 3 syms)
    "min_notional_usdt":   10.0,  # Binance Spot min notional
    "flat_pct":            0.005, # last-candle change% below this = "no movement"
}


@dataclass
class AIDecision:
    decision:        str                       # "BUY" | "SELL" | "HOLD"
    confidence:      int                       # 0..100
    reason:          str
    factors:         Dict[str, float] = field(default_factory=dict)
    trend:           str = "SIDEWAYS"          # UP | DOWN | SIDEWAYS
    signal_strength: int = 0                   # 0..100
    why_bullets:     List[str] = field(default_factory=list)
    blocker:         str = ""                  # empty unless data/balance/max_open

    def __str__(self) -> str:
        return f"{self.decision} conf={self.confidence} | {self.reason}"


def _detect_trend(df: pd.DataFrame) -> str:
    """UP / DOWN / SIDEWAYS based on EMA21 slope + price vs EMA21."""
    try:
        if "ema21" not in df.columns or len(df) < 12:
            return "SIDEWAYS"
        last_p = float(df["close"].iloc[-1])
        ema_n  = float(df["ema21"].iloc[-1])
        ema_p  = float(df["ema21"].iloc[-10])
        if not ema_p or not ema_n:
            return "SIDEWAYS"
        slope_pct = (ema_n - ema_p) / ema_p * 100
        if slope_pct >  0.03 and last_p > ema_n: return "UP"
        if slope_pct < -0.03 and last_p < ema_n: return "DOWN"
        return "SIDEWAYS"
    except Exception:
        return "SIDEWAYS"


def _safe_last(series, default=None):
    try:
        v = series.iloc[-1]
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _pct_change(df: pd.DataFrame, n: int = 1) -> float:
    """% move over the last n closes."""
    try:
        if len(df) < n + 1:
            return 0.0
        return (float(df["close"].iloc[-1]) - float(df["close"].iloc[-1 - n])) \
               / float(df["close"].iloc[-1 - n]) * 100
    except Exception:
        return 0.0


def ai_decide(
    df:                      pd.DataFrame,
    strategy_signal:         str,
    strategy_reason:         str,
    open_positions_for_sym:  List[Dict],
    free_usdt:               float,
    aggressiveness:          str = "Active Scalper",   # kept for back-compat; ignored
    minutes_since_last_trade: Optional[float] = None,
) -> AIDecision:
    """ACTIVE SCALPER — confirm or amplify the strategy signal.

    AI never vetoes BUY/SELL from strategy. It only returns HOLD when:
      • dataframe is too short for indicators
      • free USDT below $10 minimum AND strategy wants BUY
      • max_open positions already reached AND strategy wants BUY
      • market is absolutely flat (last candle move < flat_pct AND strategy is HOLD)
    Otherwise it echoes the strategy signal with a confidence score and
    optional reinforcement when its own multi-factor read agrees.
    """
    # ── Hard gate: not enough data ─────────────────────────────────────────
    if df is None or len(df) < 25:
        return AIDecision("HOLD", 0,
            f"Not enough candles ({0 if df is None else len(df)}/25) for AI scan",
            {"data": 0.0}, blocker="data",
            why_bullets=[f"✗ Only {0 if df is None else len(df)}/25 candles"])

    trend = _detect_trend(df)
    factors: Dict[str, float] = {"trend": {"UP":1.0,"DOWN":-1.0,"SIDEWAYS":0.0}.get(trend, 0.0)}

    # ── Hard gate: minimum notional for BUY ────────────────────────────────
    if free_usdt < ACTIVE_SCALPER["min_notional_usdt"] and strategy_signal == "BUY":
        return AIDecision("HOLD", 0,
            f"Insufficient USDT for BUY (free=${free_usdt:.2f} < "
            f"${ACTIVE_SCALPER['min_notional_usdt']:.0f} min)",
            {"balance": 0.0}, trend=trend, blocker="balance",
            why_bullets=[f"✗ Free USDT ${free_usdt:.2f} < $10 minimum"])

    # ── Hard gate: max open positions for symbol ───────────────────────────
    if len(open_positions_for_sym) >= ACTIVE_SCALPER["max_open"] and strategy_signal == "BUY":
        return AIDecision("HOLD", 0,
            f"Symbol position cap reached ({len(open_positions_for_sym)}/"
            f"{ACTIVE_SCALPER['max_open']})",
            {"max_open": 0.0}, trend=trend, blocker="max_open",
            why_bullets=[f"✗ {len(open_positions_for_sym)}/"
                         f"{ACTIVE_SCALPER['max_open']} positions open"])

    # ── Multi-factor scoring (BUY vs SELL) ─────────────────────────────────
    pct1 = _pct_change(df, 1)
    factors["pct1"] = round(pct1, 4)

    rsi_v = _safe_last(df["rsi"]) if "rsi" in df.columns else None
    if rsi_v is not None: factors["rsi"] = round(rsi_v, 2)

    macd_hist = _safe_last(df["macd_hist"]) if "macd_hist" in df.columns else None
    macd      = _safe_last(df["macd"])      if "macd"      in df.columns else None
    macd_sig  = _safe_last(df["macd_signal"]) if "macd_signal" in df.columns else None

    ema9  = _safe_last(df["ema9"])  if "ema9"  in df.columns else None
    ema21 = _safe_last(df["ema21"]) if "ema21" in df.columns else None
    ema9_prev = None
    try: ema9_prev = float(df["ema9"].iloc[-3]) if "ema9" in df.columns and len(df) > 3 else None
    except Exception: pass

    buy_score, sell_score = 0, 0
    bullets: List[str] = []

    # 1) Strategy prior — strongest signal
    if strategy_signal == "BUY":
        buy_score  += 35
        bullets.append(f"✓ Strategy says BUY ({strategy_reason})")
    elif strategy_signal == "SELL":
        sell_score += 35
        bullets.append(f"✓ Strategy says SELL ({strategy_reason})")

    # 2) Last-candle momentum
    if pct1 >= 0.01:
        buy_score  += 15
        bullets.append(f"✓ Last candle +{pct1:.3f}%")
    elif pct1 <= -0.01:
        sell_score += 15
        bullets.append(f"✓ Last candle {pct1:.3f}%")

    # 3) RSI bias (loose — no hard veto)
    if rsi_v is not None:
        if rsi_v <= 35:    buy_score  += 12; bullets.append(f"✓ RSI {rsi_v:.1f} oversold")
        elif rsi_v >= 65:  sell_score += 12; bullets.append(f"✓ RSI {rsi_v:.1f} overbought")
        elif rsi_v < 50:   buy_score  += 4
        else:              sell_score += 4

    # 4) MACD bias
    if macd_hist is not None:
        factors["macd_hist"] = round(macd_hist, 6)
        if macd_hist > 0:   buy_score  += 8
        if macd_hist < 0:   sell_score += 8
    elif macd is not None and macd_sig is not None:
        if macd > macd_sig: buy_score  += 6
        if macd < macd_sig: sell_score += 6

    # 5) EMA9 slope (advisory only — never blocks)
    if ema9 is not None and ema9_prev:
        slope = (ema9 - ema9_prev) / ema9_prev * 100
        factors["ema9_slope_pct"] = round(slope, 4)
        if slope > 0.01:   buy_score  += 8;  bullets.append(f"✓ EMA9 slope +{slope:.3f}%")
        elif slope < -0.01: sell_score += 8; bullets.append(f"✓ EMA9 slope {slope:.3f}%")

    # 6) Volume confirmation (bonus to whichever side already leads)
    try:
        if "volume" in df.columns:
            vol_now = float(df["volume"].iloc[-1])
            vol_ma  = float(df["volume"].tail(10).mean())
            factors["vol_ratio"] = round((vol_now / vol_ma) if vol_ma else 1.0, 2)
            if vol_ma > 0 and vol_now >= 1.3 * vol_ma:
                if buy_score >= sell_score: buy_score  += 6
                else:                       sell_score += 6
                bullets.append(f"✓ Volume {vol_now/vol_ma:.2f}× 10-bar avg")
    except Exception:
        pass

    factors["buy_score"]  = buy_score
    factors["sell_score"] = sell_score

    # ── Resolve decision ──────────────────────────────────────────────────
    # AI confirms or amplifies. The ONLY HOLD path here is "no movement and
    # nothing pointing either way" — otherwise we always emit a direction.
    if strategy_signal in ("BUY", "SELL"):
        # Strategy already chose a side — confirm and surface confidence.
        decision   = strategy_signal
        confidence = min(100, (buy_score if decision == "BUY" else sell_score))
        return AIDecision(decision, max(40, confidence),
            f"AI confirms {decision} (score BUY={buy_score} / SELL={sell_score})",
            factors, trend=trend, signal_strength=confidence,
            why_bullets=bullets)

    # Strategy said HOLD → AI may take over IF its own read is clearly one-sided.
    # "Clearly one-sided" = winning side ≥ 20 AND winning side ≥ 1.5× losing side.
    winning = max(buy_score, sell_score)
    losing  = min(buy_score, sell_score)
    one_sided = (winning >= 20) and (losing == 0 or winning >= losing * 1.5)
    if one_sided:
        decision = "BUY" if buy_score >= sell_score else "SELL"
        return AIDecision(decision, min(100, winning),
            f"AI override — strategy=HOLD but AI {decision} "
            f"(BUY={buy_score} / SELL={sell_score})",
            factors, trend=trend, signal_strength=winning,
            why_bullets=bullets)

    # Truly motionless — last candle barely moved, no scoring edge.
    if abs(pct1) < ACTIVE_SCALPER["flat_pct"]:
        return AIDecision("HOLD", winning,
            f"No movement — last candle {pct1:+.4f}% < "
            f"±{ACTIVE_SCALPER['flat_pct']}% (BUY={buy_score} / SELL={sell_score})",
            factors, trend=trend, signal_strength=winning, blocker="",
            why_bullets=[f"✗ Last candle {pct1:+.4f}% — no movement"] + bullets)

    # Marginal but moving — spec requires HOLD only when truly flat. If we
    # got here, the candle moved ≥ flat_pct, so we MUST return a direction.
    # If both scores are zero, fall back to the sign of the last candle.
    if buy_score == 0 and sell_score == 0:
        decision = "BUY" if pct1 >= 0 else "SELL"
        return AIDecision(decision, 40,
            f"AI directional (sign of move) — pct1={pct1:+.4f}%, scores neutral",
            factors, trend=trend, signal_strength=40,
            why_bullets=[f"✓ Candle moving {pct1:+.4f}% — taking direction"])
    decision = "BUY" if buy_score >= sell_score else "SELL"
    return AIDecision(decision, min(100, winning),
        f"AI marginal {decision} (BUY={buy_score} / SELL={sell_score}, "
        f"move {pct1:+.4f}%)",
        factors, trend=trend, signal_strength=winning,
        why_bullets=bullets)
