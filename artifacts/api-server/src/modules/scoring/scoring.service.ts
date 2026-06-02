import type { Candle } from "../providers/types";
import type { ReportMetrics } from "@workspace/db";
import {
  emaSeries,
  rsiSeries,
  macdSeries,
  atrSeries,
  smaSeries,
  lastFinite,
} from "../indicators/indicators";

export type ScoringResult = {
  metrics: ReportMetrics;
  institutionalScore: number;
  confidence: number;
  liquidityRisk: number;
  reasons: string[];
};

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

function classifyRegime(atrPercent: number, emaSepPct: number, slope: number, volumeRatio: number): string {
  if (atrPercent < 0.05 && volumeRatio < 0.7) return "DEAD";
  if (atrPercent > 2.0) return "VOLATILE";
  if (emaSepPct < 0.15 && Math.abs(slope) < 0.02) return "RANGE";
  return "TREND";
}

/**
 * STEP 7 (metrics) + STEP 8 (scoring) + STEP 9 (confidence). All deterministic.
 *
 * NOTE (honest scope): with free market data only, the "institutional score" is a
 * weighted TECHNICAL score (trend/momentum/RSI/volume/volatility). It is not a
 * fundamental or on-chain score — those arrive in Phase 3 via the paid-feed
 * provider interfaces. Liquidity risk is a volume/volatility proxy, not order-book
 * depth.
 */
export function score(
  candles: Candle[],
  dataQuality: { completenessScore: number; freshnessScore: number },
): ScoringResult {
  const closes = candles.map((c) => c.close);
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);
  const volumes = candles.map((c) => c.volume);

  const lastClose = closes[closes.length - 1]!;
  const ema50s = emaSeries(closes, 50);
  const ema200s = emaSeries(closes, 200);
  const ema50 = lastFinite(ema50s);
  const ema200 = lastFinite(ema200s);
  const ema50Prev = ema50s[ema50s.length - 4];
  const slope = Number.isFinite(ema50) && Number.isFinite(ema50Prev as number) ? ema50 - (ema50Prev as number) : 0;

  const rsi = lastFinite(rsiSeries(closes, 14));
  const hist = macdSeries(closes).histogram;
  const histNow = lastFinite(hist);
  let histPrev = NaN;
  for (let i = hist.length - 2; i >= 0; i--) {
    if (Number.isFinite(hist[i]!)) {
      histPrev = hist[i]!;
      break;
    }
  }

  const atr = lastFinite(atrSeries(highs, lows, closes, 14));
  const atrPercent = Number.isFinite(atr) && lastClose > 0 ? (atr / lastClose) * 100 : 0;
  const avgVolume20 = lastFinite(smaSeries(volumes, 20));
  const volume = volumes[volumes.length - 1]!;
  const volumeRatio = avgVolume20 > 0 ? volume / avgVolume20 : 1;
  const emaSepPct = Number.isFinite(ema200) && ema200 > 0 ? (Math.abs(ema50 - ema200) / ema200) * 100 : 0;
  const slopePct = lastClose > 0 ? (slope / lastClose) * 100 : 0;

  const hasLongHistory = Number.isFinite(ema200);
  const emaBull = hasLongHistory ? ema50 > ema200 : lastClose > ema50;

  let trend = "SIDEWAYS";
  if (emaBull && slope > 0) trend = "UP";
  else if (!emaBull && slope < 0) trend = "DOWN";

  const regime = classifyRegime(atrPercent, emaSepPct, slopePct, volumeRatio);

  const reasons: string[] = [];

  // ---- institutional (technical) score, neutral ~50 ------------------------
  let trendScore = 15;
  trendScore += emaBull ? 7.5 : -7.5;
  trendScore += lastClose > ema50 ? 4 : -4;
  trendScore += slope > 0 ? 3.5 : -3.5;
  trendScore = clamp(trendScore, 0, 30);

  let momentumScore = 12.5;
  momentumScore += histNow > 0 ? 8 : -8;
  momentumScore += Number.isFinite(histPrev) && histNow > histPrev ? 4.5 : -4.5;
  momentumScore = clamp(momentumScore, 0, 25);

  let rsiScore: number;
  if (rsi >= 50) {
    rsiScore = 10 + Math.min(10, (rsi - 50) / 2);
    if (rsi > 72) rsiScore -= (rsi - 72) / 2; // overbought pulls back
  } else {
    rsiScore = 10 - Math.min(10, (50 - rsi) / 2);
  }
  rsiScore = clamp(rsiScore, 0, 20);

  const volumeScore = clamp(((volumeRatio - 0.5) / 1.0) * 15, 0, 15);

  let volatilityScore: number;
  if (atrPercent < 0.05) volatilityScore = 2;
  else if (atrPercent > 2.5) volatilityScore = 3;
  else if (atrPercent >= 0.2 && atrPercent <= 1.5) volatilityScore = 10;
  else volatilityScore = 6;

  const institutionalScore = Math.round(
    clamp(trendScore + momentumScore + rsiScore + volumeScore + volatilityScore, 0, 100),
  );

  reasons.push(
    `trend=${trend} (EMA50 ${ema50.toFixed(2)} vs EMA200 ${hasLongHistory ? ema200.toFixed(2) : "n/a"})`,
  );
  reasons.push(`momentum: MACD hist ${histNow.toFixed(4)} (${histNow > histPrev ? "rising" : "falling"})`);
  reasons.push(`RSI ${rsi.toFixed(1)}`);
  reasons.push(`volume ${volumeRatio.toFixed(2)}× 20-bar avg`);
  reasons.push(`regime=${regime}, ATR ${atrPercent.toFixed(2)}%`);
  if (!hasLongHistory) reasons.push("EMA200 unavailable (short history) — trend read uses price vs EMA50");

  // ---- confidence ----------------------------------------------------------
  const dataQ = dataQuality.completenessScore * 0.5 + dataQuality.freshnessScore * 0.5;
  const netBull = institutionalScore >= 55;
  const netBear = institutionalScore <= 45;
  const factors = [emaBull, lastClose > ema50, histNow > 0, rsi >= 50, volumeRatio >= 1];
  let agree: number;
  if (netBull) agree = factors.filter(Boolean).length;
  else if (netBear) agree = factors.filter((f) => !f).length;
  else agree = 3; // neutral
  const agreementScore = (agree / factors.length) * 100;
  let confidence = Math.round(clamp(0.4 * dataQ + 0.6 * agreementScore, 0, 100));
  if (!hasLongHistory) confidence = Math.round(confidence * 0.85);

  // ---- liquidity risk (proxy from volume + volatility) ---------------------
  const riskVolume = clamp((1 - Math.min(volumeRatio, 1.5) / 1.5) * 60, 0, 60);
  const riskVol = clamp(((atrPercent - 1.0) / 2.0) * 40, 0, 40);
  const liquidityRisk = Math.round(clamp(riskVolume + riskVol, 0, 100));
  reasons.push(`liquidity risk ${liquidityRisk} (volume/volatility proxy — no order-book/on-chain depth yet)`);

  const metrics: ReportMetrics = {
    lastClose,
    ema50: Number.isFinite(ema50) ? ema50 : 0,
    ema200: Number.isFinite(ema200) ? ema200 : 0,
    rsi: Number.isFinite(rsi) ? rsi : 0,
    macdHistogram: Number.isFinite(histNow) ? histNow : 0,
    atrPercent,
    volume,
    avgVolume20: Number.isFinite(avgVolume20) ? avgVolume20 : 0,
    volumeRatio,
    trend,
    regime,
  };

  return { metrics, institutionalScore, confidence, liquidityRisk, reasons };
}
