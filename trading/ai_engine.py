"""AI Decision Engine — extra decision layer on top of the strategy signal.

Design choice — DETERMINISTIC, NOT LLM:
  The bot ticks every 5-10s. Calling an LLM per tick would burn money,
  add 1-3s of latency, and introduce non-determinism into LIVE trading.
  Instead this is a deterministic multi-factor scoring engine that reads
  the same indicators a human trader would (RSI, MACD, volume, EMA trend,
  recent momentum) and returns a confidence-scored decision.

Contract:
  ai_decide(df, strategy_signal, strategy_reason, open_positions_for_sym,
            free_usdt, aggressiveness="Balanced") -> AIDecision

  Returns an AIDecision with:
    .decision     in {"BUY","SELL","HOLD"}
    .confidence   int 0..100
    .reason       human-readable string (printed and logged)
    .factors      dict of sub-signal contributions for debugging

Safety:
  • This engine NEVER places orders — that stays with the worker + risk gates.
  • It returns HOLD whenever:
      - dataframe is too short for indicators
      - "dump" detected (cumulative drop in last N candles)
      - "pump exhaustion" detected (rapid rise + extreme RSI)
      - market is flat (ATR% below floor for the chosen profile)
      - confidence below the profile's minimum
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


# ── Aggressiveness profiles ──────────────────────────────────────────────────
#
# confidence_min  — minimum AI confidence to act (else HOLD)
# dump_pct        — cumulative drop in last 3 candles that vetoes BUY
# pump_rsi        — RSI above which a recent rise is treated as exhaustion
# flat_atr_pct    — ATR%/price below which the market is "flat" → HOLD both sides
# strategy_bonus  — extra confidence points when AI agrees with strategy signal
# allow_override  — if True, AI may initiate a trade when strategy says HOLD
#                   (only when the AI's own multi-factor score is strong)
#
# Numbers chosen so that Aggressive fires often but still refuses obvious dumps
# and dead markets — matches the user's spec: "allow faster BUY signals, lower
# confidence threshold, still enforce SL/TP".

AGGRESSIVENESS_PROFILES: Dict[str, Dict] = {
    "Conservative": {
        "confidence_min": 70,
        "dump_pct":       0.30,   # 0.30% cumulative drop in last 3 vetoes BUY
        "pump_rsi":       72.0,
        "flat_atr_pct":   0.05,   # ATR% < 0.05% → flat market
        "strategy_bonus": 25,
        "allow_override": False,
        "max_open":       1,
    },
    "Balanced": {
        "confidence_min": 55,
        "dump_pct":       0.50,
        "pump_rsi":       78.0,
        "flat_atr_pct":   0.03,
        "strategy_bonus": 20,
        "allow_override": False,
        "max_open":       2,
    },
    "Aggressive": {
        "confidence_min": 20,       # lowered from 40 per operator spec
        "dump_pct":       0.80,
        "pump_rsi":       82.0,
        "flat_atr_pct":   0.005,    # lowered from 0.02 per operator spec
        "strategy_bonus": 15,
        "allow_override": True,     # AI may initiate when strategy says HOLD
        "max_open":       3,
        "force_after_min": 30,      # if no trade for 30 min, force a micro-entry
    },
    # ── ULTRA AGGRESSIVE ──────────────────────────────────────────────────
    # Maximum-fire scalping profile. Use with 3s ticks + 0.01% threshold.
    # Bypasses confidence floor, near-zero ATR floor, allows override,
    # forces a micro-entry every 15 min if nothing else has fired.
    # STILL refuses obvious dumps and pump tops, STILL respects max_open
    # and balance gates. Always pairs with global risk caps.
    "Ultra Aggressive": {
        "confidence_min": 20,
        "dump_pct":       1.50,     # only veto on >1.5% three-bar dump
        "pump_rsi":       88.0,     # only veto on extreme overbought
        "flat_atr_pct":   0.005,    # essentially no flat-market gate
        "strategy_bonus": 10,
        "allow_override": True,
        "max_open":       3,
        "force_after_min": 15,      # force micro-entry every 15 min if idle
    },
    # ── PRO FAST SCALPER ──────────────────────────────────────────────────
    # Frequent trades, fast reactions, STRICT entry filters, strict exits.
    # Pair with 2s tick · 0.01% threshold · 40–60% USDT size · 1m timeframe.
    # The worker enforces post-entry exits (breakeven move at +0.25%,
    # 2-red-candle exit, EMA9-break exit) + per-symbol per-hour cap of 6.
    "Pro Fast Scalper": {
        "confidence_min":      20,
        "dump_pct":            1.00,
        "pump_rsi":            75.0,    # veto BUY when RSI > 75
        "flat_atr_pct":        0.005,
        "strategy_bonus":      10,
        "allow_override":      True,
        "max_open":            3,
        "force_after_min":     20,
        # PRO-specific filters (enforced inside ai_decide):
        "pro_filter":          True,
        "pro_pump_pct":        0.60,    # veto if pumped >0.6% in last 5 candles
        "pro_rsi_lo":          45.0,
        "pro_rsi_hi":          70.0,
        # PRO-specific exit hints (read by symbol_worker post-entry logic):
        "be_arm_pct":          0.25,    # arm breakeven SL at +0.25%
        "max_red_after_entry": 2,       # exit if 2 red candles after entry
        "max_trades_per_hour": 6,       # per-symbol per-hour cap
    },
}


@dataclass
class AIDecision:
    decision:        str                       # "BUY" | "SELL" | "HOLD"
    confidence:      int                       # 0..100
    reason:          str
    factors:         Dict[str, float] = field(default_factory=dict)
    # Learning-mode extras — surfaced in the dashboard "Why AI did this" panel.
    trend:           str = "SIDEWAYS"          # UP | DOWN | SIDEWAYS
    signal_strength: int = 0                   # 0..100
    why_bullets:     List[str] = field(default_factory=list)
    blocker:         str = ""                  # atr|confidence|pro_filter|dump|pump|balance|max_open|trend|''

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
        if slope_pct >  0.05 and last_p > ema_n: return "UP"
        if slope_pct < -0.05 and last_p < ema_n: return "DOWN"
        return "SIDEWAYS"
    except Exception:
        return "SIDEWAYS"


def _pro_buy_filter(df: pd.DataFrame, profile: Dict) -> tuple:
    """Strict PRO-MODE BUY entry filter. Returns (ok, why_bullets, blocker_reason).

    All of these must be TRUE to allow BUY:
      • last candle closed GREEN
      • last close > EMA9   (price crossed/holds above EMA9)
      • last close > EMA21  (above structural trend)
      • EMA9 > EMA21        (short-term trend up)
      • RSI in [pro_rsi_lo .. pro_rsi_hi]   (healthy, not overbought)
      • MACD histogram improving (or MACD > MACD signal AND rising)
      • current volume > average of last 3 candles
      • NO pump > pro_pump_pct in the last 5 candles
    """
    why: List[str] = []
    try:
        if len(df) < 6: return False, ["Not enough candles"], "data"
        last  = df.iloc[-1]
        prev  = df.iloc[-2]

        # 1. Green candle
        if not (float(last["close"]) > float(last["open"])):
            return False, ["Last candle is RED — wait for green"], "pro_filter"
        why.append("✓ Green candle confirmed")

        # 2. Price above EMA9 (cross)
        ema9_now  = float(last.get("ema9",  0) or 0)
        ema9_prev = float(prev.get("ema9",  0) or 0)
        close_now = float(last["close"])
        if ema9_now <= 0 or close_now <= ema9_now:
            return False, [f"Price ${close_now:.4f} ≤ EMA9 ${ema9_now:.4f}"], "pro_filter"
        crossed = float(prev["close"]) <= ema9_prev and close_now > ema9_now
        why.append("✓ Price crossed above EMA9" if crossed else "✓ Price holds above EMA9")

        # 3. Above EMA21 (structural)
        ema21_now = float(last.get("ema21", 0) or 0)
        if ema21_now <= 0 or close_now <= ema21_now:
            return False, [f"Price ${close_now:.4f} below EMA21 ${ema21_now:.4f}"], "trend"
        why.append("✓ Above EMA21 (structural trend up)")

        # 4. EMA9 > EMA21 (short-term trend up)
        if ema9_now <= ema21_now:
            return False, [f"EMA9 ${ema9_now:.4f} ≤ EMA21 ${ema21_now:.4f} (no short-trend)"], "trend"
        why.append("✓ EMA9 > EMA21 (short-term up)")

        # 5. RSI band
        rsi_v = float(last.get("rsi", 0) or 0)
        if rsi_v < profile["pro_rsi_lo"] or rsi_v > profile["pro_rsi_hi"]:
            return False, [f"RSI={rsi_v:.1f} outside [{profile['pro_rsi_lo']:.0f}-"
                           f"{profile['pro_rsi_hi']:.0f}] band"], "pro_filter"
        why.append(f"✓ RSI healthy ({rsi_v:.1f})")

        # 6. MACD improving
        mh_now  = last.get("macd_hist")
        mh_prev = prev.get("macd_hist")
        if mh_now is not None and mh_prev is not None \
                and not pd.isna(mh_now) and not pd.isna(mh_prev):
            if float(mh_now) <= float(mh_prev):
                return False, [f"MACD hist not improving ({float(mh_prev):+.5f}→{float(mh_now):+.5f})"], "pro_filter"
            why.append(f"✓ MACD improving ({float(mh_prev):+.5f}→{float(mh_now):+.5f})")
        else:
            m_now = last.get("macd"); ms_now = last.get("macd_signal")
            if m_now is None or ms_now is None or pd.isna(m_now) or pd.isna(ms_now):
                return False, ["MACD unavailable"], "data"
            if float(m_now) <= float(ms_now):
                return False, [f"MACD {float(m_now):+.5f} ≤ signal {float(ms_now):+.5f}"], "pro_filter"
            why.append("✓ MACD above signal")

        # 7. Volume confirmation
        try:
            v_now = float(last["volume"])
            v_3   = float(df["volume"].iloc[-4:-1].mean())
            if v_3 <= 0 or v_now <= v_3:
                return False, [f"Volume {v_now:.2f} ≤ 3-bar avg {v_3:.2f}"], "pro_filter"
            why.append(f"✓ Volume spike ({v_now/v_3:.2f}× 3-bar avg)")
        except Exception:
            pass

        # 8. No recent pump
        try:
            pump5 = (close_now - float(df["close"].iloc[-6])) / float(df["close"].iloc[-6]) * 100
            if pump5 > profile["pro_pump_pct"]:
                return False, [f"Already pumped {pump5:+.2f}% in last 5 candles "
                               f"(> {profile['pro_pump_pct']}%)"], "pump"
            why.append(f"✓ No recent overextension ({pump5:+.2f}% over 5 candles)")
        except Exception:
            pass

        return True, why, ""
    except Exception as e:
        return False, [f"Filter error: {e}"], "data"


def _safe_last(series, default=None):
    try:
        v = series.iloc[-1]
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _cumulative_pct(df: pd.DataFrame, n: int = 3) -> float:
    """Cumulative % move over the last n closes. + = up, − = down."""
    try:
        if len(df) < n + 1:
            return 0.0
        return (float(df["close"].iloc[-1]) - float(df["close"].iloc[-1 - n])) \
               / float(df["close"].iloc[-1 - n])
    except Exception:
        return 0.0


def _atr_pct(df: pd.DataFrame) -> Optional[float]:
    """ATR(14) as % of current price. None if unavailable."""
    try:
        if "atr" in df.columns:
            atr = _safe_last(df["atr"])
        else:
            tr = (df["high"] - df["low"]).abs()
            atr = float(tr.tail(14).mean())
        last = float(df["close"].iloc[-1])
        if not atr or not last:
            return None
        return atr / last
    except Exception:
        return None


def ai_decide(
    df:                      pd.DataFrame,
    strategy_signal:         str,
    strategy_reason:         str,
    open_positions_for_sym:  List[Dict],
    free_usdt:               float,
    aggressiveness:          str = "Balanced",
    minutes_since_last_trade: Optional[float] = None,
) -> AIDecision:
    """Combine indicators + strategy prior into a single decision with
    confidence 0..100. Never places orders; the worker still owns risk gates.
    """
    profile = AGGRESSIVENESS_PROFILES.get(aggressiveness,
                                          AGGRESSIVENESS_PROFILES["Balanced"])

    # ── Hard gate: not enough data ─────────────────────────────────────────
    if df is None or len(df) < 30:
        return AIDecision("HOLD", 0,
            f"Not enough candles ({0 if df is None else len(df)}/30) for AI scan",
            {"data": 0.0}, blocker="data")

    # Trend detection — surfaced even on HOLD so the dashboard can show it.
    trend = _detect_trend(df)
    factors: Dict[str, float] = {"trend": {"UP":1.0,"DOWN":-1.0,"SIDEWAYS":0.0}.get(trend, 0.0)}

    # ── Hard gate: free USDT below minimum notional ────────────────────────
    if free_usdt < 10.0 and strategy_signal == "BUY":
        return AIDecision("HOLD", 0,
            f"Insufficient USDT for BUY (free=${free_usdt:.2f} < $10 min notional)",
            {"balance": 0.0}, trend=trend, blocker="balance",
            why_bullets=[f"✗ Free USDT ${free_usdt:.2f} < $10 minimum"])

    # ── Hard gate: max open positions for this profile ─────────────────────
    if len(open_positions_for_sym) >= profile["max_open"] and strategy_signal == "BUY":
        return AIDecision("HOLD", 0,
            f"Max open positions reached for {aggressiveness} profile "
            f"({len(open_positions_for_sym)}/{profile['max_open']})",
            {"max_open": 0.0}, trend=trend, blocker="max_open",
            why_bullets=[f"✗ {len(open_positions_for_sym)}/{profile['max_open']} positions open"])

    # ── Hard gate: dump detection (refuse BUY into falling knife) ──────────
    cum3_pct = _cumulative_pct(df, 3) * 100   # %
    factors["cum3_pct"] = round(cum3_pct, 4)
    if strategy_signal == "BUY" and cum3_pct <= -profile["dump_pct"]:
        return AIDecision("HOLD", 0,
            f"Dump detected — last 3 candles fell {cum3_pct:+.3f}% "
            f"(≤ −{profile['dump_pct']:.2f}% veto). Won't buy into a dump.",
            factors, trend=trend, blocker="dump",
            why_bullets=[f"✗ Dump — {cum3_pct:+.3f}% over last 3 candles"])

    # ── Hard gate: pump-exhaustion (refuse BUY at the top) ────────────────
    rsi_v = _safe_last(df["rsi"]) if "rsi" in df.columns else None
    factors["rsi"] = round(rsi_v, 2) if rsi_v is not None else 0.0
    if strategy_signal == "BUY" and rsi_v is not None \
            and rsi_v >= profile["pump_rsi"] and cum3_pct >= 0.30:
        return AIDecision("HOLD", 0,
            f"Pump-exhaustion — RSI={rsi_v:.1f} ≥ {profile['pump_rsi']:.0f} "
            f"after {cum3_pct:+.3f}% rise. Likely top; refusing BUY.",
            factors, trend=trend, blocker="pump",
            why_bullets=[f"✗ RSI {rsi_v:.1f} ≥ {profile['pump_rsi']:.0f} "
                         f"after {cum3_pct:+.2f}% rise — pump exhaustion"])

    # ── Hard gate: flat market ─────────────────────────────────────────────
    # In Aggressive/Ultra, `force_after_min` lets a stale market punch through:
    # if nothing has traded for N minutes the flat gate is skipped so the
    # operator gets at least one micro-entry per period.
    atr_pct = _atr_pct(df)
    _force_now = bool(profile.get("force_after_min")
                      and minutes_since_last_trade is not None
                      and minutes_since_last_trade >= profile["force_after_min"])
    if atr_pct is not None:
        factors["atr_pct"] = round(atr_pct * 100, 4)
        if atr_pct * 100 < profile["flat_atr_pct"] and not _force_now:
            return AIDecision("HOLD", 0,
                f"Flat market — ATR%={atr_pct*100:.4f}% < "
                f"{profile['flat_atr_pct']:.3f}% floor for {aggressiveness}. "
                f"No edge; fees would eat the move.",
                factors, trend=trend, blocker="atr",
                why_bullets=[f"✗ ATR%={atr_pct*100:.4f}% < "
                             f"{profile['flat_atr_pct']:.3f}% floor — flat market"])

    # ── PRO FAST SCALPER strict entry filter ──────────────────────────────
    # Only enforced when profile flags pro_filter=True and the *intended*
    # action is a BUY (either strategy=BUY or override+score will be BUY).
    # On veto we return HOLD with the failing rule as the bullet so the
    # operator sees exactly why we passed.
    if profile.get("pro_filter") and not _force_now:
        # Decide what the *intended* action looks like — we only need PRO
        # filtering on the long side. If strategy says SELL outright, skip.
        if strategy_signal != "SELL":
            ok_pro, why_pro, blk_pro = _pro_buy_filter(df, profile)
            if not ok_pro:
                return AIDecision("HOLD", 0,
                    f"PRO filter veto — {why_pro[-1] if why_pro else 'failed'}",
                    factors, trend=trend, blocker=blk_pro or "pro_filter",
                    why_bullets=why_pro)

    # ── Multi-factor scoring ──────────────────────────────────────────────
    # Each factor adds (or subtracts) confidence points for BUY vs SELL.
    # We build TWO scores (buy_score, sell_score) and pick the winner.
    buy_score, sell_score = 0, 0

    # 1) Strategy prior (the existing strategy signal we were given)
    if strategy_signal == "BUY":
        buy_score  += profile["strategy_bonus"]
    elif strategy_signal == "SELL":
        sell_score += profile["strategy_bonus"]

    # 2) RSI bias
    if rsi_v is not None:
        if 30 < rsi_v < 55:   buy_score  += 12      # room above, not oversold trap
        if 45 < rsi_v < 70:   sell_score += 8       # room below
        if rsi_v <= 30:       buy_score  += 18      # oversold bounce candidate
        if rsi_v >= 70:       sell_score += 18      # overbought reversal candidate

    # 3) MACD bias (use hist if available, else macd vs signal)
    macd_hist = _safe_last(df["macd_hist"]) if "macd_hist" in df.columns else None
    macd      = _safe_last(df["macd"])      if "macd"      in df.columns else None
    macd_sig  = _safe_last(df["macd_signal"]) if "macd_signal" in df.columns else None
    if macd_hist is not None:
        factors["macd_hist"] = round(macd_hist, 6)
        if macd_hist > 0:   buy_score  += 12
        if macd_hist < 0:   sell_score += 12
    elif macd is not None and macd_sig is not None:
        if macd > macd_sig: buy_score  += 10
        if macd < macd_sig: sell_score += 10

    # 4) Volume confirmation — current vs 10-period MA
    try:
        if "volume" in df.columns:
            vol_now = float(df["volume"].iloc[-1])
            vol_ma  = float(df["volume"].tail(10).mean())
            factors["vol_ratio"] = round((vol_now / vol_ma) if vol_ma else 1.0, 2)
            if vol_ma > 0 and vol_now >= 1.5 * vol_ma:
                buy_score  += 8        # volume spike confirms either side
                sell_score += 8
    except Exception:
        pass

    # 5) EMA trend bias
    ema9  = _safe_last(df["ema9"])  if "ema9"  in df.columns else None
    ema21 = _safe_last(df["ema21"]) if "ema21" in df.columns else None
    last  = float(df["close"].iloc[-1])
    if ema9 is not None and ema21 is not None:
        factors["ema_trend"] = 1.0 if ema9 > ema21 else -1.0
        if ema9 > ema21:    buy_score  += 10   # uptrend
        if ema9 < ema21:    sell_score += 10   # downtrend
        if last > ema21:    buy_score  += 5
        if last < ema21:    sell_score += 5

    # 6) Recent momentum (last single candle)
    pct1 = _cumulative_pct(df, 1) * 100
    factors["pct1"] = round(pct1, 4)
    if pct1 >= 0.04:  buy_score  += 6
    if pct1 <= -0.04: sell_score += 6

    # ── Resolve decision ──────────────────────────────────────────────────
    factors["buy_score"]  = buy_score
    factors["sell_score"] = sell_score

    if buy_score >= sell_score:
        decision   = "BUY"
        confidence = min(100, buy_score)
    else:
        decision   = "SELL"
        confidence = min(100, sell_score)

    # If the AI's pick disagrees with the strategy AND override is disabled
    # for this profile, fall back to HOLD (strategy is the ground truth).
    if not profile["allow_override"] and strategy_signal in ("BUY", "SELL") \
            and decision != strategy_signal:
        return AIDecision("HOLD", confidence,
            f"AI disagrees with strategy ({strategy_signal}) — "
            f"AI scored BUY={buy_score} / SELL={sell_score}. "
            f"Override disabled for {aggressiveness}; defaulting to HOLD.",
            factors, trend=trend, signal_strength=confidence, blocker="strategy",
            why_bullets=[f"✗ AI({decision}) ≠ strategy({strategy_signal}), override off"])

    # If strategy said HOLD and override is disabled, mirror HOLD.
    if not profile["allow_override"] and strategy_signal == "HOLD":
        return AIDecision("HOLD", confidence,
            f"Strategy=HOLD ({strategy_reason}); AI scored "
            f"BUY={buy_score}/SELL={sell_score} but override disabled "
            f"for {aggressiveness}.",
            factors, trend=trend, signal_strength=confidence, blocker="strategy",
            why_bullets=[f"✗ Strategy=HOLD, override off for {aggressiveness}"])

    # Confidence floor — bypassed when force_after_min has elapsed (forced
    # micro-entry path) so the operator gets a trade even in a dead market.
    if confidence < profile["confidence_min"] and not _force_now:
        return AIDecision("HOLD", confidence,
            f"AI confidence {confidence} < {profile['confidence_min']} "
            f"floor for {aggressiveness} (BUY={buy_score}/SELL={sell_score})",
            factors, trend=trend, signal_strength=confidence, blocker="confidence",
            why_bullets=[f"✗ Confidence {confidence} < {profile['confidence_min']} floor"])

    if _force_now:
        # Forced micro-entry: tag the reason so the operator can see WHY
        # the bot took an otherwise-marginal trade. Prefer BUY in a dead
        # market (long-bias scalp). Risk gates still enforce size + SL/TP.
        decision = decision if decision in ("BUY", "SELL") else "BUY"
        reason = (f"⚡ FORCED MICRO-ENTRY ({aggressiveness}) — no trade for "
                  f"{minutes_since_last_trade:.0f} min ≥ "
                  f"{profile['force_after_min']} min trigger. "
                  f"AI {decision} BUY={buy_score}/SELL={sell_score}")
        return AIDecision(decision, max(profile["confidence_min"], confidence),
                          reason, factors, trend=trend,
                          signal_strength=max(profile["confidence_min"], confidence),
                          why_bullets=[f"⚡ Forced micro-entry after "
                                       f"{minutes_since_last_trade:.0f} min idle"])

    # Build a compact reason string + why-bullets for the dashboard panel.
    bits = []
    why  = []
    if rsi_v   is not None:
        bits.append(f"RSI={rsi_v:.1f}"); why.append(f"✓ RSI {rsi_v:.1f}")
    if macd_hist is not None:
        bits.append(f"MACDh={macd_hist:+.4f}")
        why.append(f"✓ MACD hist {macd_hist:+.4f}")
    if ema9 is not None and ema21 is not None:
        bits.append("trend↑" if ema9 > ema21 else "trend↓")
        why.append("✓ EMA9 above EMA21" if ema9 > ema21 else "✓ EMA9 below EMA21")
    if atr_pct is not None:
        bits.append(f"ATR%={atr_pct*100:.3f}%")
        why.append(f"✓ ATR% {atr_pct*100:.3f}%")
    bits.append(f"3c={cum3_pct:+.3f}%")
    bits.append(f"strat={strategy_signal}")
    why.append(f"✓ Score {decision} {buy_score}/{sell_score}")
    reason = f"AI {decision} BUY={buy_score}/SELL={sell_score} | " + " | ".join(bits)
    return AIDecision(decision, confidence, reason, factors,
                      trend=trend, signal_strength=confidence, why_bullets=why)
