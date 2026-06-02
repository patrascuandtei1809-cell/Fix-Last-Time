import { describe, it, expect } from "vitest";
import { smaSeries, emaSeries, rsiSeries, macdSeries, atrSeries, lastFinite } from "./indicators";

describe("indicators (pure, deterministic)", () => {
  it("smaSeries warms up then averages a known window", () => {
    const out = smaSeries([2, 4, 6, 8], 2);
    expect(Number.isNaN(out[0]!)).toBe(true);
    expect(out[1]).toBe(3);
    expect(out[2]).toBe(5);
    expect(out[3]).toBe(7);
  });

  it("emaSeries seeds with an SMA and is finite after the warmup period", () => {
    const closes = Array.from({ length: 50 }, (_, i) => 100 + i);
    const out = emaSeries(closes, 10);
    expect(Number.isNaN(out[8]!)).toBe(true);
    expect(Number.isFinite(out[9]!)).toBe(true);
    expect(out[out.length - 1]!).toBeGreaterThan(out[10]!);
  });

  it("rsiSeries climbs toward 100 on a monotonic uptrend", () => {
    const closes = Array.from({ length: 40 }, (_, i) => 100 + i);
    const rsi = lastFinite(rsiSeries(closes, 14));
    expect(rsi).toBeGreaterThan(95);
  });

  it("rsiSeries falls toward 0 on a monotonic downtrend", () => {
    const closes = Array.from({ length: 40 }, (_, i) => 200 - i);
    const rsi = lastFinite(rsiSeries(closes, 14));
    expect(rsi).toBeLessThan(5);
  });

  it("macd histogram is positive when fast momentum leads", () => {
    const closes = Array.from({ length: 80 }, (_, i) => 100 * Math.pow(1.01, i));
    const hist = lastFinite(macdSeries(closes).histogram);
    expect(hist).toBeGreaterThan(0);
  });

  it("atrSeries is positive given real ranges", () => {
    const highs = Array.from({ length: 40 }, (_, i) => 101 + i);
    const lows = Array.from({ length: 40 }, (_, i) => 99 + i);
    const closes = Array.from({ length: 40 }, (_, i) => 100 + i);
    expect(lastFinite(atrSeries(highs, lows, closes, 14))).toBeGreaterThan(0);
  });

  it("is deterministic — identical input yields identical output", () => {
    const closes = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i));
    expect(rsiSeries(closes, 14)).toEqual(rsiSeries(closes, 14));
  });
});
