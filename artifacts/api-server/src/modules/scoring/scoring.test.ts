import { describe, it, expect } from "vitest";
import { score } from "./scoring.service";
import { trendingCandles } from "../../test/fixtures";

const FULL_QUALITY = { completenessScore: 100, freshnessScore: 100 };

describe("scoring: institutional score + confidence + liquidity (deterministic)", () => {
  it("reads an uptrend as UP with a high technical score", () => {
    const candles = trendingCandles({ count: 260, direction: "up" });
    const r = score(candles, FULL_QUALITY);
    expect(r.metrics.trend).toBe("UP");
    expect(r.metrics.ema50).toBeGreaterThan(r.metrics.ema200);
    expect(r.institutionalScore).toBeGreaterThanOrEqual(55);
  });

  it("reads a downtrend as DOWN with a low technical score", () => {
    const candles = trendingCandles({ count: 260, direction: "down" });
    const r = score(candles, FULL_QUALITY);
    expect(r.metrics.trend).toBe("DOWN");
    expect(r.institutionalScore).toBeLessThan(55);
  });

  it("confidence is high when every factor agrees and data quality is full", () => {
    const candles = trendingCandles({ count: 260, direction: "up" });
    const r = score(candles, FULL_QUALITY);
    expect(r.confidence).toBeGreaterThanOrEqual(90);
    expect(r.confidence).toBeLessThanOrEqual(100);
  });

  it("poor data quality drags confidence down for the same candles", () => {
    const candles = trendingCandles({ count: 260, direction: "up" });
    const good = score(candles, FULL_QUALITY).confidence;
    const poor = score(candles, { completenessScore: 20, freshnessScore: 20 }).confidence;
    expect(poor).toBeLessThan(good);
  });

  it("liquidity risk and all metrics are finite and in range", () => {
    const candles = trendingCandles({ count: 260, direction: "up" });
    const r = score(candles, FULL_QUALITY);
    expect(r.liquidityRisk).toBeGreaterThanOrEqual(0);
    expect(r.liquidityRisk).toBeLessThanOrEqual(100);
    for (const v of Object.values(r.metrics)) {
      if (typeof v === "number") expect(Number.isFinite(v)).toBe(true);
    }
  });

  it("is deterministic — identical candles yield identical scoring", () => {
    const candles = trendingCandles({ count: 260, direction: "up" });
    expect(score(candles, FULL_QUALITY)).toEqual(score(candles, FULL_QUALITY));
  });
});
