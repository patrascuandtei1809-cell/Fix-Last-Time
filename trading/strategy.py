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

def get_signal(df: pd.DataFrame, strategy: str, threshold: float = 0.0003) -> Tuple[str, str, int]:
    """Returns (signal, reason, confidence_0_100)."""
    if strategy == "Active Scalper":
        # `threshold` is fractional (0.0001 = 0.01%); convert to percent.
        return active_scalper_signal(df, threshold_pct=max(threshold, 0.00001) * 100)
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
    df["stoch_k"], df["stoch_d"] = calculate_stochastic(df)
    df["rsi"]     = calculate_rsi(df["close"])
    df["atr"]     = calculate_atr(df)
    macd, sigl, hist = calculate_macd(df["close"])
    df["macd"]      = macd
    df["macd_sig"]  = sigl
    df["macd_hist"] = hist
    return df


# ── SMART PRIORITY SCALPER — cross-symbol opportunity scoring ─────────────────
#
# score_market() rates a symbol's current setup 0–100 by combining 6 weighted
# components. The orchestrator runs this on every active symbol every tick and
# trades ONLY the highest-scoring one (if ≥ score_threshold).
#
# Weights (sum = 100):
#   • trend       25  — EMA9 vs EMA21 alignment + EMA9 slope, in trade direction
#   • momentum    20  — MACD histogram direction + magnitude, in trade direction
#   • volume      20  — current volume vs 20-bar average
#   • candle      15  — body/range ratio (strong candles vs dojis)
#   • rsi_quality 10  — RSI in healthy zone for the trade direction
#   • volatility  10  — ATR% in sweet spot (0.05–0.5%), penalize flat or spike
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

    # ── 1. Trend (25): EMA9 vs EMA21 alignment + slope direction ────────────
    ema_aligned = (ema9 > ema21) if is_buy else (ema9 < ema21)
    slope_pct   = ((ema9 - ema9_3) / ema9_3 * 100) if ema9_3 else 0.0
    slope_aligned = (slope_pct > 0) if is_buy else (slope_pct < 0)
    trend_score = 0
    if ema_aligned:   trend_score += 15
    if slope_aligned:
        # 0..10 scaled by |slope| up to 0.05% (clamped)
        trend_score += min(10, abs(slope_pct) / 0.05 * 10)

    # ── 2. Momentum (20): MACD histogram in trade direction ─────────────────
    mom_aligned = (mhist > 0) if is_buy else (mhist < 0)
    mom_strength = abs(mhist) / max(price * 0.0005, 1e-9)   # normalize vs 0.05% of price
    mom_score = 0
    if mom_aligned:
        mom_score = min(20, 10 + mom_strength * 10)

    # ── 3. Volume (20): current vs 20-bar avg ───────────────────────────────
    try:
        avg_vol = float(d["volume"].rolling(20).mean().iloc[-1])
    except Exception:
        avg_vol = 0.0
    vol_ratio = (vol / avg_vol) if avg_vol > 0 else 0.0
    # 1.0× = neutral 10pts, 2.0× = max 20pts, <0.5× = 0
    vol_score = 0
    if vol_ratio >= 0.5:
        vol_score = min(20, (vol_ratio - 0.5) / 1.5 * 20)

    # ── 4. Candle body (15): strong body in trade direction ────────────────
    rng = max(high - low, 1e-9)
    body = abs(price - opn)
    body_ratio = body / rng
    body_aligned = (price > opn) if is_buy else (price < opn)
    cdl_score = 0
    if body_aligned:
        cdl_score = min(15, body_ratio * 15)
    elif body_ratio < 0.2:
        # tiny doji — neutral, half credit
        cdl_score = 4

    # ── 5. RSI quality (10): healthy zone for direction ─────────────────────
    rsi_score = 0
    if is_buy:
        if   50 <= rsi <= 70: rsi_score = 10
        elif 40 <= rsi < 50:  rsi_score = 6
        elif 70 < rsi <= 80:  rsi_score = 5      # overbought drift
        elif rsi > 80:        rsi_score = 1      # extreme — likely fade
        elif rsi < 30:        rsi_score = 7      # oversold bounce
    else:  # SELL
        if   30 <= rsi <= 50: rsi_score = 10
        elif 50 < rsi <= 60:  rsi_score = 6
        elif 20 <= rsi < 30:  rsi_score = 5
        elif rsi < 20:        rsi_score = 1
        elif rsi > 70:        rsi_score = 7

    # ── 6. Volatility quality (10): ATR% in sweet spot ──────────────────────
    atr_pct = (atr / price * 100) if price else 0.0
    if   0.05 <= atr_pct <= 0.50: vol_q = 10
    elif 0.02 <= atr_pct < 0.05:  vol_q = 5       # tight but tradeable
    elif 0.50 < atr_pct <= 1.00:  vol_q = 6       # choppy
    elif atr_pct < 0.02:          vol_q = 0       # flat — penalty
    else:                          vol_q = 2       # spike — penalty

    total = int(round(trend_score + mom_score + vol_score + cdl_score + rsi_score + vol_q))
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
        "trend":    round(trend_score, 1),
        "momentum": round(mom_score, 1),
        "volume":   round(vol_score, 1),
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
