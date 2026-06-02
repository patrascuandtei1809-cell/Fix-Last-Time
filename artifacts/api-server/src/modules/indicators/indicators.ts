// Deterministic technical indicators. Pure functions: identical input → identical
// output. Each returns the full series (NaN during warmup) so callers can read the
// latest value and compute slopes.

export function smaSeries(values: number[], period: number): number[] {
  const out = new Array<number>(values.length).fill(NaN);
  if (period <= 0) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i]!;
    if (i >= period) sum -= values[i - period]!;
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

export function emaSeries(values: number[], period: number): number[] {
  const out = new Array<number>(values.length).fill(NaN);
  if (period <= 0 || values.length < period) return out;
  const k = 2 / (period + 1);
  let seed = 0;
  for (let i = 0; i < period; i++) seed += values[i]!;
  seed /= period;
  out[period - 1] = seed;
  let prev = seed;
  for (let i = period; i < values.length; i++) {
    const ema = values[i]! * k + prev * (1 - k);
    out[i] = ema;
    prev = ema;
  }
  return out;
}

export function rsiSeries(closes: number[], period = 14): number[] {
  const out = new Array<number>(closes.length).fill(NaN);
  if (closes.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = closes[i]! - closes[i - 1]!;
    if (diff >= 0) gain += diff;
    else loss -= diff;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  const rsiFrom = (g: number, l: number) => (l === 0 ? (g === 0 ? 50 : 100) : 100 - 100 / (1 + g / l));
  out[period] = rsiFrom(avgGain, avgLoss);
  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i]! - closes[i - 1]!;
    const g = diff >= 0 ? diff : 0;
    const l = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + g) / period;
    avgLoss = (avgLoss * (period - 1) + l) / period;
    out[i] = rsiFrom(avgGain, avgLoss);
  }
  return out;
}

export function macdSeries(
  closes: number[],
  fast = 12,
  slow = 26,
  signal = 9,
): { histogram: number[] } {
  const emaFast = emaSeries(closes, fast);
  const emaSlow = emaSeries(closes, slow);
  const macdLine = closes.map((_, i) => {
    const f = emaFast[i]!;
    const s = emaSlow[i]!;
    return Number.isFinite(f) && Number.isFinite(s) ? f - s : NaN;
  });
  const finiteStart = macdLine.findIndex((v) => Number.isFinite(v));
  const histogram = new Array<number>(closes.length).fill(NaN);
  if (finiteStart === -1) return { histogram };
  const compact = macdLine.slice(finiteStart);
  const signalLine = emaSeries(compact, signal);
  for (let i = 0; i < compact.length; i++) {
    const sig = signalLine[i]!;
    if (Number.isFinite(sig)) histogram[finiteStart + i] = compact[i]! - sig;
  }
  return { histogram };
}

export function atrSeries(
  highs: number[],
  lows: number[],
  closes: number[],
  period = 14,
): number[] {
  const n = closes.length;
  const out = new Array<number>(n).fill(NaN);
  if (n <= period) return out;
  const tr = new Array<number>(n).fill(NaN);
  tr[0] = highs[0]! - lows[0]!;
  for (let i = 1; i < n; i++) {
    tr[i] = Math.max(
      highs[i]! - lows[i]!,
      Math.abs(highs[i]! - closes[i - 1]!),
      Math.abs(lows[i]! - closes[i - 1]!),
    );
  }
  let sum = 0;
  for (let i = 1; i <= period; i++) sum += tr[i]!;
  let atr = sum / period;
  out[period] = atr;
  for (let i = period + 1; i < n; i++) {
    atr = (atr * (period - 1) + tr[i]!) / period;
    out[i] = atr;
  }
  return out;
}

export function lastFinite(series: number[]): number {
  for (let i = series.length - 1; i >= 0; i--) {
    if (Number.isFinite(series[i]!)) return series[i]!;
  }
  return NaN;
}
