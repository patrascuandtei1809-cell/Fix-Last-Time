import type { Candle } from "../modules/providers/types";

/**
 * Deterministic synthetic OHLCV. Replit is geo-blocked from Binance, and tests
 * must never hit the network, so the pipeline is exercised with reproducible
 * candles. Identical params → identical candles.
 */
export function trendingCandles(params: {
  count: number;
  direction: "up" | "down" | "flat";
  timeframeMs?: number;
  endTime?: number;
  basePrice?: number;
}): Candle[] {
  const { count, direction } = params;
  const timeframeMs = params.timeframeMs ?? 3_600_000; // 1h
  const endTime = params.endTime ?? 1_700_000_000_000;
  const base = params.basePrice ?? 100;
  const drift = direction === "up" ? 0.004 : direction === "down" ? -0.004 : 0;

  const candles: Candle[] = [];
  let close = base;
  for (let i = 0; i < count; i++) {
    const openTime = endTime - (count - 1 - i) * timeframeMs;
    const open = close;
    close = open * (1 + drift);
    const high = Math.max(open, close) * 1.001;
    const low = Math.min(open, close) * 0.999;
    const volume = 1000 + i; // gently rising, deterministic
    candles.push({ openTime, open, high, low, close, volume });
  }
  return candles;
}
