import pandas as pd
import numpy as np
from typing import Tuple


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calculate_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    denom = (high_max - low_min).replace(0, np.nan)
    k = 100 * (df["close"] - low_min) / denom
    d = k.rolling(d_period).mean()
    return k, d


def ema_crossover_signal(df: pd.DataFrame) -> Tuple[str, str]:
    if len(df) < 25:
        return "HOLD", "Not enough candles for EMA calculation"

    ema9 = calculate_ema(df["close"], 9)
    ema21 = calculate_ema(df["close"], 21)

    prev9, curr9 = ema9.iloc[-2], ema9.iloc[-1]
    prev21, curr21 = ema21.iloc[-2], ema21.iloc[-1]

    if prev9 <= prev21 and curr9 > curr21:
        return "BUY", (
            f"EMA9 crossed above EMA21 — bullish crossover. "
            f"EMA9={curr9:.4f} > EMA21={curr21:.4f} (was {prev9:.4f} ≤ {prev21:.4f})"
        )
    if prev9 >= prev21 and curr9 < curr21:
        return "SELL", (
            f"EMA9 crossed below EMA21 — bearish crossover. "
            f"EMA9={curr9:.4f} < EMA21={curr21:.4f} (was {prev9:.4f} ≥ {prev21:.4f})"
        )

    trend = "uptrend" if curr9 > curr21 else "downtrend"
    return "HOLD", (
        f"No new EMA crossover — {trend}. "
        f"EMA9={curr9:.4f}, EMA21={curr21:.4f}"
    )


def price_movement_signal(df: pd.DataFrame, threshold: float = 0.0003) -> Tuple[str, str]:
    if len(df) < 3:
        return "HOLD", "Not enough data for price movement check"

    prev = df["close"].iloc[-2]
    curr = df["close"].iloc[-1]
    pct = (curr - prev) / prev

    if pct >= threshold:
        return "BUY", (
            f"Price moved +{pct * 100:.4f}% ≥ +{threshold * 100:.4f}% threshold. "
            f"{prev:.4f} → {curr:.4f}"
        )
    if pct <= -threshold:
        return "SELL", (
            f"Price moved {pct * 100:.4f}% ≤ -{threshold * 100:.4f}% threshold. "
            f"{prev:.4f} → {curr:.4f}"
        )

    return "HOLD", (
        f"Price movement {pct * 100:.4f}% within ±{threshold * 100:.4f}% — no trigger. "
        f"Price: {curr:.4f}"
    )


def momentum_signal(df: pd.DataFrame, period: int = 14) -> Tuple[str, str]:
    if len(df) < period + 5:
        return "HOLD", "Not enough data for momentum (RSI) calculation"

    close = df["close"]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()

    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_val = rsi.iloc[-1]

    avg_vol = df["volume"].rolling(10).mean().iloc[-1]
    curr_vol = df["volume"].iloc[-1]
    vol_conf = curr_vol > avg_vol * 1.2
    vol_note = f" | Volume: {curr_vol:.0f} vs avg {avg_vol:.0f} ({'✓ confirmed' if vol_conf else '✗ weak'})"

    if rsi_val < 30:
        base = f"RSI={rsi_val:.1f} — oversold (<30){vol_note}"
        if vol_conf:
            return "BUY", base
        return "HOLD", base + " — waiting for volume confirmation"

    if rsi_val > 70:
        base = f"RSI={rsi_val:.1f} — overbought (>70){vol_note}"
        if vol_conf:
            return "SELL", base
        return "HOLD", base + " — waiting for volume confirmation"

    return "HOLD", f"RSI={rsi_val:.1f} — neutral zone (30–70), no signal{vol_note}"


def get_signal(df: pd.DataFrame, strategy: str, threshold: float = 0.0003) -> Tuple[str, str]:
    if strategy == "EMA Crossover":
        return ema_crossover_signal(df)
    if strategy == "Price Movement":
        return price_movement_signal(df, threshold=threshold)
    if strategy == "Momentum (RSI)":
        return momentum_signal(df)
    return "HOLD", f"Unknown strategy: {strategy}"


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def get_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"]    = calculate_ema(df["close"], 9)
    df["ema21"]   = calculate_ema(df["close"], 21)
    df["stoch_k"], df["stoch_d"] = calculate_stochastic(df)
    df["rsi"]     = calculate_rsi(df["close"])
    return df
