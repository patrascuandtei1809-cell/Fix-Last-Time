"""
AlphaTrade strategies.

All signal functions return (signal: str, reason: str, confidence: int)
where confidence is an integer 0-100 (50 = neutral). HOLD signals always
return confidence 0 unless they explain why a near-miss was rejected.
"""
import pandas as pd
import numpy as np
from typing import Tuple


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


# ── Dispatcher ────────────────────────────────────────────────────────────────

def get_signal(df: pd.DataFrame, strategy: str, threshold: float = 0.0003) -> Tuple[str, str, int]:
    """Returns (signal, reason, confidence_0_100)."""
    if strategy == "EMA Crossover":
        return ema_crossover_signal(df)
    if strategy == "Price Movement":
        return price_movement_signal(df, threshold=threshold)
    if strategy == "Momentum (RSI)":
        return momentum_signal(df)
    return "HOLD", f"Unknown strategy: {strategy}", 0


def get_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"]    = calculate_ema(df["close"], 9)
    df["ema21"]   = calculate_ema(df["close"], 21)
    df["ema50"]   = calculate_ema(df["close"], 50)
    df["stoch_k"], df["stoch_d"] = calculate_stochastic(df)
    df["rsi"]     = calculate_rsi(df["close"])
    df["atr"]     = calculate_atr(df)
    return df
