import type { Candle, Timeframe } from "../providers/types";
import { TIMEFRAME_SECONDS } from "../providers/types";

export type DataState = "OK" | "PARTIAL" | "UNKNOWN";

export type NormalizationResult = {
  candles: Candle[];
  completenessScore: number;
  freshnessScore: number;
  dataState: DataState;
  notes: string[];
};

const MIN_CANDLES = 60; // below this we cannot compute reliable indicators
const FULL_CANDLES = 200; // EMA200 needs this many for a complete picture

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/**
 * STEP 2 (normalize) + STEP 4 (data-quality check). Providers already emit a
 * unified Candle schema, so normalization here de-dupes/sorts and scores the
 * data's completeness and freshness, then assigns a deterministic data state.
 */
export function normalize(
  rawCandles: Candle[],
  timeframe: Timeframe,
  now: number = Date.now(),
): NormalizationResult {
  const notes: string[] = [];

  const byTime = new Map<number, Candle>();
  for (const c of rawCandles) {
    if (Number.isFinite(c.close) && Number.isFinite(c.openTime)) byTime.set(c.openTime, c);
  }
  const candles = [...byTime.values()].sort((a, b) => a.openTime - b.openTime);

  if (candles.length === 0) {
    return {
      candles,
      completenessScore: 0,
      freshnessScore: 0,
      dataState: "UNKNOWN",
      notes: ["no candles available"],
    };
  }

  const completenessScore = Math.round(clamp((candles.length / FULL_CANDLES) * 100, 0, 100));
  if (candles.length < FULL_CANDLES) {
    notes.push(`have ${candles.length} candles, ${FULL_CANDLES} preferred for full indicators`);
  }

  const intervalMs = TIMEFRAME_SECONDS[timeframe] * 1000;
  const lastOpen = candles[candles.length - 1]!.openTime;
  const ageIntervals = (now - lastOpen) / intervalMs;
  // Fresh within ~2 intervals → 100; decays to 0 by ~10 intervals stale.
  const freshnessScore = Math.round(clamp(((10 - ageIntervals) / 8) * 100, 0, 100));
  if (freshnessScore < 100) {
    notes.push(`last candle is ~${ageIntervals.toFixed(1)} intervals old`);
  }

  let dataState: DataState;
  if (candles.length < MIN_CANDLES) {
    dataState = "UNKNOWN";
    notes.push(`only ${candles.length} candles (< ${MIN_CANDLES}) — insufficient for a decision`);
  } else if (completenessScore < 90 || freshnessScore < 60) {
    dataState = "PARTIAL";
  } else {
    dataState = "OK";
  }

  return { candles, completenessScore, freshnessScore, dataState, notes };
}
