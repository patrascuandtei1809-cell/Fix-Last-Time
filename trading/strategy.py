"""
AlphaTrade strategies.

All signal functions return (signal: str, reason: str, confidence: int)
where confidence is an integer 0-100 (50 = neutral). HOLD signals always
return confidence 0 unless they explain why a near-miss was rejected.
"""
import pandas as pd
import numpy as np
from typing import Tuple, List


# ── Indicator primitives ──────────────────────────────────────────────────────

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    low_min  = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    denom    = (high_max - low_min).replace(0, np.nan)
    k        = 100 * (df["close"] - low_min) / denom
    d        = k.rolling(d_period).mean()
    return k, d


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — volatility measure."""
    h_l  = df["high"] - df["low"]
    h_pc = (df["high"] - df["close"].shift()).abs()
    l_pc = (df["low"]  - df["close"].shift()).abs()
    tr   = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ── Strategy: EMA Crossover (UPGRADED with filters) ───────────────────────────

# Minimum volatility (ATR / price) required to enter a trade
_MIN_VOL_PCT = 0.0010   # 0.10% — skip dead markets

def ema_crossover_signal(df: pd.DataFrame) -> Tuple[str, str, int]:
    if len(df) < 55:
        return "HOLD", "Not enough candles (need 55+) for filtered EMA strategy", 0

    close = df["close"]
    ema9  = calculate_ema(close, 9)
    ema21 = calculate_ema(close, 21)
    ema50 = calculate_ema(close, 50)     # higher-TF trend proxy
    rsi   = calculate_rsi(close, 14)
    atr   = calculate_atr(df, 14)

    prev9,  curr9  = ema9.iloc[-2],  ema9.iloc[-1]
    prev21, curr21 = ema21.iloc[-2], ema21.iloc[-1]
    curr50 = ema50.iloc[-1]
    rsi_v  = rsi.iloc[-1]
    atr_v  = atr.iloc[-1]
    px     = close.iloc[-1]
    vol_pct = (atr_v / px) if px else 0

    cross_up   = prev9 <= prev21 and curr9 > curr21
    cross_down = prev9 >= prev21 and curr9 < curr21

    # No cross? Report current state and reason no entry.
    if not (cross_up or cross_down):
        trend = "uptrend" if curr9 > curr21 else "downtrend"
        return "HOLD", (
            f"No EMA cross — {trend} | EMA9={curr9:.4f} vs EMA21={curr21:.4f} | "
            f"RSI={rsi_v:.1f} | ATR%={vol_pct*100:.3f}"
        ), 0

    # We have a cross — apply filters.
    side       = "BUY" if cross_up else "SELL"
    rsi_ok     = (rsi_v > 50) if side == "BUY" else (rsi_v < 50)
    trend_ok   = (px > curr50) if side == "BUY" else (px < curr50)
    vol_ok     = vol_pct >= _MIN_VOL_PCT

    filters = {
        "EMA cross":     True,
        "RSI confirms":  rsi_ok,
        "Trend confirms": trend_ok,
        "Volatility OK": vol_ok,
    }
    passed = sum(1 for v in filters.values() if v)
    all_ok = passed == 4

    # Confidence: 25 per filter passed (max 100). Bonus +/- from RSI distance.
    rsi_strength = abs(rsi_v - 50) / 50   # 0..1
    vol_strength = min(vol_pct / (_MIN_VOL_PCT * 4), 1.0)   # cap at 4x min
    confidence   = int(min(100, passed * 22 + rsi_strength * 8 + vol_strength * 4))

    reason_bits = []
    reason_bits.append(f"EMA9 {'↑' if side=='BUY' else '↓'} EMA21 ({curr9:.4f} vs {curr21:.4f})")
    reason_bits.append(f"RSI={rsi_v:.1f} {'✓' if rsi_ok else '✗'}{'>50' if side=='BUY' else '<50'}")
    reason_bits.append(f"Trend {'✓' if trend_ok else '✗'} ({'above' if side=='BUY' else 'below'} EMA50 {curr50:.4f})")
    reason_bits.append(f"Vol {'✓' if vol_ok else '✗'} ATR%={vol_pct*100:.3f}")
    full = " | ".join(reason_bits)

    if not all_ok:
        return "HOLD", f"EMA cross rejected ({passed}/4 filters): {full}", confidence
    return side, f"{side} confirmed ({passed}/4): {full}", confidence


# ── Strategy: Price Movement (scalping, ATR-gated) ────────────────────────────

# Volatility band for the Price Movement scalping strategy. Measured as
# ATR(14) / price. Skip both dead markets (no opportunity, fees eat the move)
# and spike markets (slippage + unreliable signals).
PRICE_MOVE_MIN_VOL_PCT = 0.0003   # 0.03% — below this is a flatline
PRICE_MOVE_MAX_VOL_PCT = 0.0050   # 0.50% — above this is a spike/news event

# Binance Spot round-trip fee (taker BUY + taker SELL). Used to annotate
# whether a signal's expected move clears fees with margin.
ROUNDTRIP_FEE_PCT = 0.0020        # 0.20%

def price_movement_signal(
    df: pd.DataFrame,
    threshold: float = 0.0003,
    min_vol_pct: float = PRICE_MOVE_MIN_VOL_PCT,
    max_vol_pct: float = PRICE_MOVE_MAX_VOL_PCT,
) -> Tuple[str, str, int]:
    if len(df) < 16:
        return "HOLD", "Not enough data for price movement check (need 16+ candles)", 0

    prev = df["close"].iloc[-2]
    curr = df["close"].iloc[-1]
    pct  = (curr - prev) / prev
    mag  = abs(pct)

    # ── Volatility filter (ATR-based) ────────────────────────────────────────
    atr_v   = calculate_atr(df, 14).iloc[-1]
    vol_pct = (atr_v / curr) if (curr and pd.notna(atr_v)) else 0.0
    if pd.notna(atr_v):
        if vol_pct < min_vol_pct:
            return "HOLD", (
                f"Vol too LOW — ATR%={vol_pct*100:.4f}% < {min_vol_pct*100:.3f}% "
                f"(dead market) | move {pct*100:+.4f}%"
            ), 0
        if vol_pct > max_vol_pct:
            return "HOLD", (
                f"Vol too HIGH — ATR%={vol_pct*100:.4f}% > {max_vol_pct*100:.3f}% "
                f"(spike — too risky) | move {pct*100:+.4f}%"
            ), 0

    conf = int(min(100, (mag / threshold) * 30 + 40)) if threshold > 0 else 50

    # Fees-aware tag so the log makes clear whether the move alone clears fees.
    fees_pct = ROUNDTRIP_FEE_PCT * 100
    fee_tag  = "fees✓" if mag * 100 >= fees_pct else f"fees✗(need TP>{fees_pct:.2f}%)"

    if pct >= threshold:
        return "BUY",  (
            f"Price +{pct*100:.4f}% ≥ +{threshold*100:.4f}% | {prev:.4f}→{curr:.4f} | "
            f"ATR%={vol_pct*100:.3f}% | {fee_tag}"
        ), conf
    if pct <= -threshold:
        return "SELL", (
            f"Price {pct*100:.4f}% ≤ -{threshold*100:.4f}% | {prev:.4f}→{curr:.4f} | "
            f"ATR%={vol_pct*100:.3f}% | {fee_tag}"
        ), conf
    return "HOLD", (
        f"Move {pct*100:+.4f}% within ±{threshold*100:.4f}% | {curr:.4f} | "
        f"ATR%={vol_pct*100:.3f}%"
    ), 0


# ── Strategy: Momentum (RSI) ──────────────────────────────────────────────────

def momentum_signal(df: pd.DataFrame, period: int = 14) -> Tuple[str, str, int]:
    if len(df) < period + 5:
        return "HOLD", "Not enough data for RSI", 0

    rsi      = calculate_rsi(df["close"], period)
    rsi_v    = rsi.iloc[-1]
    avg_vol  = df["volume"].rolling(10).mean().iloc[-1]
    curr_vol = df["volume"].iloc[-1]
    vol_conf = curr_vol > avg_vol * 1.2
    vol_note = f" | Vol {curr_vol:.0f} vs avg {avg_vol:.0f} ({'✓' if vol_conf else '✗'})"

    # Confidence: how deep into oversold/overbought + volume confirm
    rsi_dist = max(0, 30 - rsi_v) if rsi_v < 30 else (max(0, rsi_v - 70) if rsi_v > 70 else 0)
    conf     = int(min(100, rsi_dist * 3 + (25 if vol_conf else 0) + 25)) if rsi_dist > 0 else 0

    if rsi_v < 30:
        base = f"RSI={rsi_v:.1f} oversold{vol_note}"
        return ("BUY", base, conf) if vol_conf else ("HOLD", base + " — awaiting volume", conf // 2)
    if rsi_v > 70:
        base = f"RSI={rsi_v:.1f} overbought{vol_note}"
        return ("SELL", base, conf) if vol_conf else ("HOLD", base + " — awaiting volume", conf // 2)
    return "HOLD", f"RSI={rsi_v:.1f} neutral{vol_note}", 0


# ── Strategy: Active Scalper (ACTIVE SCALPER MODE — single hardcoded mode) ───
#
# Fires on ANY of:
#   • |price change| >= threshold_pct (default 0.01%)
#   • EMA9 3-bar slope crosses |ema9_slope_thresh| (default 0.005%)
#   • Bounce: >=2 of last 3 candles red AND current candle green → BUY
#   • Momentum flip: pct_prev<0 & pct_now>0 → BUY (reverse → SELL)
#
# No EMA hard veto, no "perfect trend" requirement, no green-candle requirement.
# Allows trading in sideways markets. HOLD only when NOTHING moves.
#
# The worker dynamically lowers `threshold_pct` after idle periods (anti-idle),
# so this function must remain pure — it only reads what's passed in.
def active_scalper_signal(
    df: pd.DataFrame,
    threshold_pct:     float = 0.01,
    ema9_slope_thresh: float = 0.005,
) -> Tuple[str, str, int]:
    if len(df) < 25:
        return "HOLD", "Need 25+ candles for ACTIVE SCALPER", 0

    close = df["close"]
    opn   = df["open"]
    try:
        curr  = float(close.iloc[-1])
        prev  = float(close.iloc[-2])
        prev2 = float(close.iloc[-3])
    except Exception:
        return "HOLD", "Bad candle frame", 0

    pct      = (curr - prev) / prev * 100 if prev else 0.0
    pct_prev = (prev - prev2) / prev2 * 100 if prev2 else 0.0

    # EMA9 slope over last 3 closes — captures direction change before a full cross.
    ema9 = calculate_ema(close, 9)
    try:
        e_now, e_prev2 = float(ema9.iloc[-1]), float(ema9.iloc[-3])
        slope = (e_now - e_prev2) / e_prev2 * 100 if e_prev2 else 0.0
    except Exception:
        e_now, slope = 0.0, 0.0

    # Bounce detector: >=2 of last 3 candles red AND current is green.
    try:
        reds_before = sum(
            1 for i in (-4, -3, -2)
            if float(close.iloc[i]) < float(opn.iloc[i])
        )
        curr_green = curr > float(opn.iloc[-1])
    except Exception:
        reds_before, curr_green = 0, False
    bounce_up = reds_before >= 2 and curr_green

    momentum_up   = pct_prev < 0 and pct > 0
    momentum_down = pct_prev > 0 and pct < 0

    buy_triggers:  List[str] = []
    sell_triggers: List[str] = []
    if pct >=  threshold_pct: buy_triggers.append(f"price {pct:+.3f}% ≥ +{threshold_pct:.3f}%")
    if pct <= -threshold_pct: sell_triggers.append(f"price {pct:+.3f}% ≤ -{threshold_pct:.3f}%")
    if slope >=  ema9_slope_thresh: buy_triggers.append(f"EMA9 slope {slope:+.3f}%")
    if slope <= -ema9_slope_thresh: sell_triggers.append(f"EMA9 slope {slope:+.3f}%")
    if bounce_up:    buy_triggers.append(f"bounce — {reds_before}/3 reds → green")
    if momentum_up:  buy_triggers.append(f"momentum flip up ({pct_prev:+.3f}%→{pct:+.3f}%)")
    if momentum_down: sell_triggers.append(f"momentum flip down ({pct_prev:+.3f}%→{pct:+.3f}%)")

    # Confidence scales with magnitude of move vs threshold.
    mag = max(abs(pct), abs(slope))
    conf = int(min(100, (mag / threshold_pct) * 30 + 40)) if threshold_pct > 0 else 50

    if buy_triggers and not sell_triggers:
        return "BUY",  "ACTIVE SCALPER BUY: "  + " | ".join(buy_triggers), conf
    if sell_triggers and not buy_triggers:
        return "SELL", "ACTIVE SCALPER SELL: " + " | ".join(sell_triggers), conf
    if buy_triggers and sell_triggers:
        # Mixed — pick stronger by trigger count, tie → side of larger magnitude.
        if len(buy_triggers) > len(sell_triggers) or \
           (len(buy_triggers) == len(sell_triggers) and pct >= 0):
            return "BUY",  "ACTIVE SCALPER BUY (mixed): " + " | ".join(buy_triggers), conf
        return "SELL", "ACTIVE SCALPER SELL (mixed): " + " | ".join(sell_triggers), conf
    return "HOLD", (f"No movement — pct={pct:+.4f}% slope={slope:+.4f}% "
                    f"(threshold ±{threshold_pct:.3f}%)"), 0


# ── Dispatcher ────────────────────────────────────────────────────────────────

MAX_LAST_CANDLE_MOVE_PCT = 0.3   # HARD RULE: never enter if last bar moved >0.3% (move already happened)


def reversal_signal(df: pd.DataFrame) -> Tuple[str, str, int]:
    """EARLY REVERSAL SCALPER v2 — strict gates, enter BEFORE the move.

    HARD RULE: if last candle absolute move >0.3% → HOLD (move already done).

    BUY requires ALL:
      • RSI < 40 AND rising vs previous bar
      • MACD hist flipping positive (prev ≤ 0, now > 0)  OR  momentum turning
        up from below (mh > mh_p, mh_p < 0)
      • Volume spike: current vol ≥ 1.5× 20-bar average
      • Candle body strength: body ≥ 50% of range AND green (no weak/doji)
      • Price near/below EMA9 (price ≤ EMA9 × 1.0005) — not extended

    SELL requires ALL (symmetric):
      • RSI > 60 AND falling
      • MACD hist flipping negative or momentum rolling over from above
      • Volume spike ≥ 1.5×
      • Candle body strength: body ≥ 50% of range AND red (no weak/doji)
      • Price stretched above EMA9 (price ≥ EMA9 × 0.9995)
    """
    d = get_indicators(df) if "ema9" not in df.columns else df
    if len(d) < 22:
        return "HOLD", "insufficient bars for reversal", 0
    try:
        last = d.iloc[-1]; prev = d.iloc[-2]
        rsi    = float(last["rsi"]);       rsi_p  = float(prev["rsi"])
        mh     = float(last["macd_hist"]); mh_p   = float(prev["macd_hist"])
        opn    = float(last["open"]);      cls    = float(last["close"])
        high   = float(last["high"]);      low    = float(last["low"])
        ema9   = float(last["ema9"])
        vol    = float(last["volume"])
        avg_vol = float(d["volume"].rolling(20).mean().iloc[-1] or 0)
    except Exception:
        return "HOLD", "indicator NaN (reversal)", 0

    # ── HARD RULE: reject late entries (move already happened) ────────────
    move_pct = (abs(cls - opn) / opn * 100) if opn else 0.0
    if move_pct > MAX_LAST_CANDLE_MOVE_PCT:
        return "HOLD", f"LATE — last bar moved {move_pct:.2f}% > {MAX_LAST_CANDLE_MOVE_PCT}% (move already happened)", 0

    vol_ratio = (vol / avg_vol) if avg_vol > 0 else 0.0
    # FINAL STABLE MODE (May 29 2026): NO strict volume requirement — only a
    # non-shrinking volume is needed (vol ≥ 1.0× the 20-bar average).
    vol_ok = vol_ratio >= 1.0

    # Candle body strength — used as a CONFIDENCE booster now, no longer a hard
    # gate (FINAL STABLE MODE removed strict momentum stacking).
    rng = max(high - low, 1e-9)
    body_ratio = abs(cls - opn) / rng

    # Momentum turning up: hist flipped sign OR rising from below zero.
    mom_up   = (mh_p <= 0 and mh > 0) or (mh > mh_p and mh_p < 0)
    mom_down = (mh_p >= 0 and mh < 0) or (mh < mh_p and mh_p > 0)

    # ── FINAL STABLE MODE: NO strict stacking ──────────────────────────────
    # Fire on the CORE reversal trigger (RSI extreme/turning OR momentum flip)
    # + a non-shrinking volume + a candle leaning the right way. Body strength,
    # RSI depth and EMA position only ADD confidence — they don't block.
    buy_trigger  = (rsi < 45 and rsi >= rsi_p) or mom_up
    sell_trigger = (rsi > 55 and rsi <= rsi_p) or mom_down

    if buy_trigger and vol_ok and cls >= opn:
        conf = min(95, 40 + int(min(20, max(0, (vol_ratio - 1.0)) * 20))
                   + int(body_ratio * 15)
                   + (10 if mom_up else 0) + (10 if rsi < 35 else 0))
        reason = (f"REVERSAL BUY: RSI {rsi:.1f} (was {rsi_p:.1f}) | "
                  f"MACD hist {mh_p:+.5f}→{mh:+.5f} | vol {vol_ratio:.2f}×avg | "
                  f"body {body_ratio*100:.0f}% | price {cls:.4f} vs EMA9 {ema9:.4f}")
        return "BUY", reason, conf

    if sell_trigger and vol_ok and cls <= opn:
        conf = min(95, 40 + int(min(20, max(0, (vol_ratio - 1.0)) * 20))
                   + int(body_ratio * 15)
                   + (10 if mom_down else 0) + (10 if rsi > 65 else 0))
        reason = (f"REVERSAL SELL: RSI {rsi:.1f} (was {rsi_p:.1f}) | "
                  f"MACD hist {mh_p:+.5f}→{mh:+.5f} | vol {vol_ratio:.2f}×avg | "
                  f"body {body_ratio*100:.0f}% | price {cls:.4f} vs EMA9 {ema9:.4f}")
        return "SELL", reason, conf

    # Diagnostic HOLD — explain why no trigger fired.
    side = "BUY" if rsi <= 50 else "SELL"
    bits = [f"vol {vol_ratio:.2f}× ({'ok' if vol_ok else '<1.0'})",
            f"RSI {rsi:.1f}", f"mom_up={mom_up} mom_down={mom_down}",
            f"candle {'green' if cls >= opn else 'red'}"]
    return "HOLD", f"no {side} trigger ({', '.join(bits)})", 0


def ema_macd_rsi_volume_v2_signal(df: pd.DataFrame) -> Tuple[str, str, int]:
    """EMA_MACD_RSI_VOLUME_V2 — LONG-ONLY trend-following confirmation strategy.

    A deliberate counterpoint to the reactive scalper: it only acts when the
    higher-timeframe trend, momentum, RSI bias, and volume ALL agree, so it
    fires far less often but with stronger confluence. Pairs with ATR-based
    SL/TP (see risk.RiskManager.atr_*) so stops adapt to volatility.

    EXACT entry spec (operator-defined, June 2026):
      LONG (BUY) requires ALL of:
        • EMA50 > EMA200            (only take longs in an established uptrend)
        • RSI   > 55               (momentum bias to the upside)
        • MACD histogram > 0       (momentum turning/expanding up)
        • volume > 1.5× the 20-bar average  (real participation behind the move)
      EMA50 < EMA200  →  NO LONG (HOLD). Spot is long-only, so there is no SHORT
      side: a down-trend simply means "do not open a position".

    Returns (signal, reason, confidence_0_100). HOLD carries a diagnostic of
    which condition failed.
    """
    if df is None or len(df) < 200:
        return "HOLD", "V2: insufficient data (<200 bars for EMA200)", 0
    d = get_indicators(df) if "ema200" not in df.columns else df
    last = d.iloc[-1]
    try:
        ema50  = float(last["ema50"]);  ema200 = float(last["ema200"])
        mhist  = float(last["macd_hist"]); rsi  = float(last["rsi"])
        vol    = float(last["volume"])
        avg_vol = float(d["volume"].rolling(20).mean().iloc[-1])
    except Exception:
        return "HOLD", "V2: indicator NaN", 0
    if not all(np.isfinite(v) for v in (ema50, ema200, mhist, rsi, vol, avg_vol)):
        return "HOLD", "V2: non-finite indicator", 0

    vol_ratio = (vol / avg_vol) if avg_vol > 0 else 0.0
    vol_ok    = vol_ratio > 1.5
    trend_up  = ema50 > ema200

    # LONG-ONLY. EMA50 < EMA200 → never open (spot cannot short).
    if trend_up and rsi > 55 and mhist > 0 and vol_ok:
        conf = 60 + min(35, int((vol_ratio - 1.5) * 25) + int((rsi - 55) / 2))
        return "BUY", (
            f"V2 LONG — EMA50>EMA200, RSI {rsi:.1f}>55, "
            f"MACD hist {mhist:+.5f}>0, vol {vol_ratio:.2f}×>1.5×"), min(95, conf)

    return "HOLD", (
        f"V2 no setup (EMA50{'>' if trend_up else '<'}EMA200, "
        f"RSI {rsi:.1f}, MACD hist {mhist:+.5f}, vol {vol_ratio:.2f}×)"), 0


def donchian_breakout_signal(df: pd.DataFrame, lookback: int = 20) -> Tuple[str, str, int]:
    """Donchian Breakout — LONG-ONLY higher-timeframe trend-breakout strategy.

    Designed for 15m/1h/4h where a real breakout can run far past the ~0.24%
    round-trip fee hurdle (unlike a 1m scalp). Pairs with ATR-based SL/TP and
    NO scalper exits (no breakeven snap, no 2-red exit) so winners can run.

    BUY requires ALL of:
      • EMA50 > EMA200            (only break out WITH the higher-tf trend)
      • close > prior N-bar high  (genuine breakout, current bar excluded)
      • volume ≥ 1.0× 20-bar avg  (participation behind the break)

    Long-only (spot). Returns (signal, reason, confidence_0_100).
    """
    if df is None or len(df) < 210:
        return "HOLD", "Donchian: insufficient data (<210 bars)", 0
    d = get_indicators(df) if "ema200" not in df.columns else df
    last = d.iloc[-1]
    try:
        close  = float(last["close"])
        ema50  = float(last["ema50"]);  ema200 = float(last["ema200"])
        vol    = float(last["volume"])
        avg_vol = float(d["volume"].rolling(20).mean().iloc[-1])
        prior_high = float(d["high"].iloc[-(lookback + 1):-1].max())
    except Exception:
        return "HOLD", "Donchian: indicator NaN", 0
    if not all(np.isfinite(v) for v in (close, ema50, ema200, vol, avg_vol, prior_high)):
        return "HOLD", "Donchian: non-finite indicator", 0

    trend_up  = ema50 > ema200
    vol_ratio = (vol / avg_vol) if avg_vol > 0 else 0.0
    if trend_up and close > prior_high and vol_ratio >= 1.0:
        conf = 60 + min(35, int((vol_ratio - 1.0) * 20)
                        + int((close / prior_high - 1.0) * 4000))
        return "BUY", (
            f"Donchian breakout — close {close:.2f} > {lookback}-bar high "
            f"{prior_high:.2f}, EMA50>EMA200, vol {vol_ratio:.2f}×"), min(95, conf)
    return "HOLD", (
        f"Donchian no breakout (close {close:.2f} vs {lookback}h-high "
        f"{prior_high:.2f}, trend {'up' if trend_up else 'down'}, "
        f"vol {vol_ratio:.2f}×)"), 0


def trend_pullback_signal(df: pd.DataFrame) -> Tuple[str, str, int]:
    """Trend Pullback — LONG-ONLY buy-the-dip in an established uptrend.

    Higher-timeframe companion to Donchian: instead of buying the break, it buys
    the first reclaim of EMA21 after a pullback inside an uptrend, with RSI
    turning up. Targets a continuation leg that clears the fee hurdle. ATR exits,
    no scalper exits.

    BUY requires ALL of:
      • EMA50 > EMA200             (established uptrend)
      • prior close ≤ prior EMA21  (price pulled back to/under the fast MA)
      • current close > EMA21       (reclaiming the MA — bounce confirmed)
      • RSI rising and 40 ≤ RSI ≤ 68 (momentum turning up, not overbought)

    Long-only (spot). Returns (signal, reason, confidence_0_100).
    """
    if df is None or len(df) < 210:
        return "HOLD", "Pullback: insufficient data (<210 bars)", 0
    d = get_indicators(df) if "ema200" not in df.columns else df
    last = d.iloc[-1]; prev = d.iloc[-2]
    try:
        close  = float(last["close"]); pclose = float(prev["close"])
        ema21  = float(last["ema21"]); pema21 = float(prev["ema21"])
        ema50  = float(last["ema50"]); ema200 = float(last["ema200"])
        rsi    = float(last["rsi"]);   rsi_prev = float(prev["rsi"])
    except Exception:
        return "HOLD", "Pullback: indicator NaN", 0
    if not all(np.isfinite(v) for v in
               (close, pclose, ema21, pema21, ema50, ema200, rsi, rsi_prev)):
        return "HOLD", "Pullback: non-finite indicator", 0

    trend_up    = ema50 > ema200
    pulled_back = pclose <= pema21
    reclaim     = close > ema21
    rsi_ok      = (40 <= rsi <= 68) and (rsi > rsi_prev)
    if trend_up and pulled_back and reclaim and rsi_ok:
        conf = 60 + min(30, int(rsi - 40))
        return "BUY", (
            "Trend pullback — uptrend (EMA50>EMA200), bounce off EMA21 "
            f"(prev≤EMA21, now>EMA21), RSI {rsi:.1f} rising"), min(90, conf)
    return "HOLD", (
        f"Pullback no setup (trend {'up' if trend_up else 'down'}, "
        f"prev{'≤' if pulled_back else '>'}EMA21, "
        f"now{'>' if reclaim else '≤'}EMA21, "
        f"RSI {rsi:.1f}{'↑' if rsi > rsi_prev else '↓'})"), 0


def get_signal(df: pd.DataFrame, strategy: str, threshold: float = 0.0003) -> Tuple[str, str, int]:
    """Returns (signal, reason, confidence_0_100)."""
    if strategy == "Reversal Scalper":
        return reversal_signal(df)
    if strategy == "Active Scalper":
        # `threshold` is fractional (0.0001 = 0.01%); convert to percent.
        return active_scalper_signal(df, threshold_pct=max(threshold, 0.00001) * 100)
    if strategy == "EMA_MACD_RSI_VOLUME_V2":
        return ema_macd_rsi_volume_v2_signal(df)
    if strategy == "Donchian Breakout":
        return donchian_breakout_signal(df)
    if strategy == "Trend Pullback":
        return trend_pullback_signal(df)
    if strategy == "EMA Crossover":
        return ema_crossover_signal(df)
    if strategy == "Price Movement":
        return price_movement_signal(df, threshold=threshold)
    if strategy == "Momentum (RSI)":
        return momentum_signal(df)
    return "HOLD", f"Unknown strategy: {strategy}", 0


def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast = series.ewm(span=fast,   adjust=False).mean()
    ema_slow = series.ewm(span=slow,   adjust=False).mean()
    macd     = ema_fast - ema_slow
    sig      = macd.ewm(span=signal, adjust=False).mean()
    hist     = macd - sig
    return macd, sig, hist


def get_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"]    = calculate_ema(df["close"], 9)
    df["ema21"]   = calculate_ema(df["close"], 21)
    df["ema50"]   = calculate_ema(df["close"], 50)
    df["ema200"]  = calculate_ema(df["close"], 200)   # trend filter (V2 strategy)
    df["stoch_k"], df["stoch_d"] = calculate_stochastic(df)
    df["rsi"]     = calculate_rsi(df["close"])
    df["atr"]     = calculate_atr(df)
    macd, sigl, hist = calculate_macd(df["close"])
    df["macd"]        = macd
    df["macd_signal"] = sigl   # canonical name used by dashboard + ai_engine
    df["macd_sig"]    = sigl   # alias kept for backward compat
    df["macd_hist"]   = hist
    return df


# ── EARLY REVERSAL SCALPER — cross-symbol opportunity scoring ─────────────────
#
# score_market() rates a symbol's current REVERSAL setup 0–100 by combining 4
# weighted components. The orchestrator runs this on every active symbol every
# tick and trades ONLY the highest-scoring one (if ≥ score_threshold).
#
# Weights (sum = 100) — REVERSAL-FIRST:
#   • reversal    40  — RSI extreme + wick rejection + MACD hist sign-flip
#                       (rewards being EARLY, not late)
#   • volume      25  — current volume vs 20-bar average (spike confirms turn)
#   • momentum    20  — MACD hist direction matches trade (post-flip alignment)
#   • ema_trend   15  — EMA9 vs EMA21 alignment (minor — we're catching turns,
#                       NOT confirming trend)
#
# Returns (score: int 0–100, breakdown: dict).
# If signal is HOLD, returns 0 (we never trade a HOLD regardless of score).
def score_market(df: pd.DataFrame, signal: str, base_confidence: int,
                 regime: str = "") -> Tuple[int, dict]:
    """Compute a 0–100 opportunity score for the given signal on this df.

    `regime` is one of {"", "DEAD", "RANGE", "TREND", "VOLATILE"} from
    `market_regime.classify_regime`. Applied as bonus/penalty at the end so
    the raw component breakdown still reflects the rule-based picture.
    """
    if signal not in ("BUY", "SELL") or len(df) < 30:
        return 0, {"reason": "no signal or insufficient data",
                   "regime": regime or "UNKNOWN"}

    d = get_indicators(df) if "macd_hist" not in df.columns else df
    last = d.iloc[-1]
    try:
        ema9   = float(last["ema9"])
        ema21  = float(last["ema21"])
        rsi    = float(last["rsi"])
        atr    = float(last["atr"])
        price  = float(last["close"])
        opn    = float(last["open"])
        high   = float(last["high"])
        low    = float(last["low"])
        vol    = float(last["volume"])
        mhist  = float(last["macd_hist"])
        ema9_3 = float(d["ema9"].iloc[-4]) if len(d) >= 4 else ema9
    except Exception:
        return 0, {"reason": "indicator NaN"}

    is_buy = signal == "BUY"
    try:
        mhist_p = float(d["macd_hist"].iloc[-2])
    except Exception:
        mhist_p = mhist
    atr_pct = (atr / price * 100) if price else 0.0
    rng = max(high - low, 1e-9)
    body = abs(price - opn)
    upper_wick = high - max(opn, price)
    lower_wick = min(opn, price) - low

    # ── 1. REVERSAL (40): RSI extreme + wick rejection + MACD sign-flip ─────
    rev_score = 0.0
    if is_buy:
        # RSI oversold (0-15)
        if   rsi < 25: rev_score += 15
        elif rsi < 30: rev_score += 12
        elif rsi < 35: rev_score += 8
        # MACD hist sign-flip up (0-15)
        if mhist_p <= 0 and mhist > 0: rev_score += 15
        elif mhist > mhist_p and mhist_p < 0: rev_score += 8   # turning up still negative
        # Lower wick rejection (0-10)
        if body > 0 and lower_wick >= 2 * body:
            rev_score += min(10, (lower_wick / rng) * 15)
    else:  # SELL
        if   rsi > 75: rev_score += 15
        elif rsi > 70: rev_score += 12
        elif rsi > 65: rev_score += 8
        if mhist_p >= 0 and mhist < 0: rev_score += 15
        elif mhist < mhist_p and mhist_p > 0: rev_score += 8
        if body > 0 and upper_wick >= 2 * body:
            rev_score += min(10, (upper_wick / rng) * 15)
    rev_score = min(40, rev_score)

    # ── 2. VOLUME (25): spike vs 20-bar avg confirms the turn ───────────────
    try:
        avg_vol = float(d["volume"].rolling(20).mean().iloc[-1])
    except Exception:
        avg_vol = 0.0
    vol_ratio = (vol / avg_vol) if avg_vol > 0 else 0.0
    # <1.0× = 0, 1.5× = 15pts, 2.5× = max 25pts
    if vol_ratio >= 1.0:
        vol_score = min(25, (vol_ratio - 1.0) / 1.5 * 25)
    else:
        vol_score = 0

    # ── 3. MOMENTUM (20): MACD hist direction matches the trade ─────────────
    mom_aligned = (mhist > 0) if is_buy else (mhist < 0)
    mom_strength = abs(mhist) / max(price * 0.0005, 1e-9)
    mom_score = min(20, 8 + mom_strength * 12) if mom_aligned else 0

    # ── 4. EMA TREND (15): alignment only — minor, we're catching turns ─────
    ema_aligned = (ema9 > ema21) if is_buy else (ema9 < ema21)
    slope_pct   = ((ema9 - ema9_3) / ema9_3 * 100) if ema9_3 else 0.0
    trend_score = 0
    if ema_aligned: trend_score += 10
    if (slope_pct > 0 and is_buy) or (slope_pct < 0 and not is_buy):
        trend_score += min(5, abs(slope_pct) / 0.05 * 5)

    # Back-compat keys for dashboard (no weight, just informational)
    cdl_score = 0.0
    rsi_score = 0.0
    vol_q     = 0.0

    total = int(round(rev_score + vol_score + mom_score + trend_score))
    total = max(0, min(100, total))

    # Slight nudge from rule confidence (already factored in via signal existing).
    # Cap at 100. Confidence < 40 caps the total at 75 (low-conviction signals
    # cannot win against high-conviction ones from another symbol).
    if base_confidence < 40:
        total = min(total, 75)

    # ── 7. Regime adjustment (TREND +5, RANGE −10, DEAD cap 30, VOLATILE 0) ──
    pre_regime = total
    if regime:
        try:
            from market_regime import apply_regime_to_score
            total = apply_regime_to_score(total, regime)
        except Exception:
            pass

    breakdown = {
        "reversal": round(rev_score, 1),
        "volume":   round(vol_score, 1),
        "momentum": round(mom_score, 1),
        "trend":    round(trend_score, 1),
        "candle":   round(cdl_score, 1),
        "rsi":      round(rsi_score, 1),
        "vol_q":    round(vol_q, 1),
        "atr_pct":  round(atr_pct, 3),
        "vol_ratio": round(vol_ratio, 2),
        "ema_slope_pct": round(slope_pct, 4),
        "macd_hist":     round(mhist, 6),
        "regime":        regime or "UNKNOWN",
        "pre_regime":    int(pre_regime),
    }
    return total, breakdown


# ── WEIGHTED DECISION ENGINE — replaces the hard HOLD veto ────────────────────
#
# Instead of vetoing a trade whenever the rule strategy emits HOLD, we score
# BOTH directions from 6 weighted factors and let the stronger side win — as
# long as it clears the orchestrator threshold downstream. A HARD veto is
# reserved ONLY for genuinely dangerous conditions (insufficient data, a flat /
# dead tape, or a volatility blowout). This keeps the bot reactive and simple
# instead of requiring perfect conditions to act.
#
# Factors & max weights (sum = 100):
#   • AI confidence   20  — directional conviction from the AI engine
#   • RSI             15  — oversold→BUY / overbought→SELL (graded)
#   • EMA trend       20  — EMA9 vs EMA21 alignment + EMA9 slope
#   • MACD momentum   20  — histogram sign + a fresh sign-flip
#   • Volume spike    15  — current vs 20-bar avg, credited to candle direction
#   • Candle struct   10  — body fraction (direction) + wick rejection
#
# Returns (signal, score, breakdown, veto_reason):
#   signal       "BUY" | "SELL" | "HOLD"
#   score        0–100 conviction for the chosen side (regime-adjusted)
#   breakdown    per-factor + bull/bear totals (for logs + dashboard)
#   veto_reason  "" normally; a short string when a HARD risk veto fired
#                (dangerous condition → forced HOLD regardless of score)
DANGER_ATR_PCT = 2.0    # ATR% above this on the last bar = blowout → hard veto
FLAT_PCT       = 0.005  # last-candle move below this on a DEAD tape → hard veto


def weighted_decision(df: pd.DataFrame, ai_signal: str = "HOLD",
                      ai_confidence: int = 0,
                      regime: str = "") -> Tuple[str, int, dict, str]:
    if df is None or len(df) < 30:
        return "HOLD", 0, {"reason": "insufficient data"}, "insufficient data"
    d = get_indicators(df) if "macd_hist" not in df.columns else df
    last = d.iloc[-1]
    try:
        ema9   = float(last["ema9"]);  ema21 = float(last["ema21"])
        rsi    = float(last["rsi"]);   atr   = float(last["atr"])
        price  = float(last["close"]); opn   = float(last["open"])
        high   = float(last["high"]);  low   = float(last["low"])
        vol    = float(last["volume"]); mhist = float(last["macd_hist"])
        ema9_3  = float(d["ema9"].iloc[-4])      if len(d) >= 4 else ema9
        mhist_p = float(d["macd_hist"].iloc[-2])
        close_p = float(d["close"].iloc[-2])
    except Exception:
        return "HOLD", 0, {"reason": "indicator NaN"}, "indicator NaN"

    # SAFETY: float(np.nan) does NOT raise, so explicitly reject any non-finite
    # indicator — a NaN that slips into bull/bear could be clamped into a fake
    # high score and fire a LIVE order. Non-finite → hard veto (HOLD).
    if not all(np.isfinite(v) for v in
               (ema9, ema21, rsi, atr, price, opn, high, low, vol,
                mhist, ema9_3, mhist_p, close_p)):
        return "HOLD", 0, {"reason": "non-finite indicator"}, "non-finite indicator"

    atr_pct   = (atr / price * 100) if price else 0.0
    last_move = abs((price - close_p) / close_p * 100) if close_p else 0.0

    # ── HARD RISK VETO — dangerous conditions ONLY ─────────────────────────
    if atr_pct > DANGER_ATR_PCT:
        return ("HOLD", 0,
                {"atr_pct": round(atr_pct, 3), "regime": regime or "UNKNOWN"},
                f"volatility blowout atr%={atr_pct:.2f}>{DANGER_ATR_PCT}")
    if regime == "DEAD" and last_move < FLAT_PCT:
        return ("HOLD", 0,
                {"atr_pct": round(atr_pct, 3), "regime": regime,
                 "last_move": round(last_move, 4)},
                f"flat/dead tape (move {last_move:.4f}%<{FLAT_PCT}%)")

    bull = 0.0
    bear = 0.0

    # 1. AI confidence (20) — directional conviction from the AI engine.
    if   ai_signal == "BUY":  bull += min(20.0, max(0, ai_confidence) / 5.0)
    elif ai_signal == "SELL": bear += min(20.0, max(0, ai_confidence) / 5.0)

    # 2. RSI (15) — graded oversold→BUY / overbought→SELL.
    if   rsi < 30: bull += 15
    elif rsi < 40: bull += 9
    elif rsi < 45: bull += 4
    if   rsi > 70: bear += 15
    elif rsi > 60: bear += 9
    elif rsi > 55: bear += 4

    # 3. EMA trend (20) — alignment (12) + slope (8).
    slope_pct = ((ema9 - ema9_3) / ema9_3 * 100) if ema9_3 else 0.0
    if ema9 > ema21: bull += 12
    else:            bear += 12
    if   slope_pct > 0: bull += min(8.0, abs(slope_pct) / 0.05 * 8)
    elif slope_pct < 0: bear += min(8.0, abs(slope_pct) / 0.05 * 8)

    # 4. MACD momentum (20) — sign (10) + fresh flip (10) / turning (4).
    if   mhist > 0: bull += 10
    elif mhist < 0: bear += 10
    if   mhist_p <= 0 and mhist > 0: bull += 10   # fresh flip up
    elif mhist_p >= 0 and mhist < 0: bear += 10   # fresh flip down
    elif mhist > mhist_p:            bull += 4    # turning up
    elif mhist < mhist_p:            bear += 4    # turning down

    # 5. Volume spike (15) — credited to the candle's direction.
    try:
        avg_vol = float(d["volume"].rolling(20).mean().iloc[-1])
    except Exception:
        avg_vol = 0.0
    vol_ratio = (vol / avg_vol) if avg_vol > 0 else 0.0
    vspike = min(15.0, max(0.0, (vol_ratio - 1.0) / 1.5 * 15))
    if price >= opn: bull += vspike
    else:            bear += vspike

    # 6. Candle structure (10) — body fraction (6) + wick rejection (4).
    rng        = max(high - low, 1e-9)
    body       = abs(price - opn)
    body_frac  = body / rng
    lower_wick = min(opn, price) - low
    upper_wick = high - max(opn, price)
    if price >= opn: bull += body_frac * 6
    else:            bear += body_frac * 6
    if body > 0 and lower_wick >= 2 * body: bull += min(4.0, (lower_wick / rng) * 6)
    if body > 0 and upper_wick >= 2 * body: bear += min(4.0, (upper_wick / rng) * 6)

    # Guard against any residual non-finite leaking into the totals.
    if not (np.isfinite(bull) and np.isfinite(bear)):
        return "HOLD", 0, {"reason": "non-finite score"}, "non-finite score"
    pre_bull = max(0.0, min(100.0, bull))
    pre_bear = max(0.0, min(100.0, bear))

    # Regime adjustment — applied to BOTH directions so the canonical score
    # (conviction in the FINAL signal's direction) is regime-aware. Same engine
    # as score_market for consistency.
    bull, bear = pre_bull, pre_bear
    if regime:
        try:
            from market_regime import apply_regime_to_score
            bull = apply_regime_to_score(pre_bull, regime)
            bear = apply_regime_to_score(pre_bear, regime)
        except Exception:
            pass
    bull = max(0, min(100, int(round(bull))))
    bear = max(0, min(100, int(round(bear))))
    if bull >= bear:
        signal, score = "BUY", bull
    else:
        signal, score = "SELL", bear

    breakdown = {
        "bull":          bull,
        "bear":          bear,
        "pre_bull":      round(pre_bull, 1),
        "pre_bear":      round(pre_bear, 1),
        "ai_signal":     ai_signal,
        "ai_confidence": int(ai_confidence),
        "rsi":           round(rsi, 1),
        "ema9_gt_ema21": bool(ema9 > ema21),
        "ema_slope_pct": round(slope_pct, 4),
        "macd_hist":     round(mhist, 6),
        "vol_ratio":     round(vol_ratio, 2),
        "body_frac":     round(body_frac, 2),
        "atr_pct":       round(atr_pct, 3),
        "regime":        regime or "UNKNOWN",
    }
    return signal, score, breakdown, ""
