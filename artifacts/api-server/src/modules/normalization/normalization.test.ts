import { describe, it, expect } from "vitest";
import { normalize } from "./normalization.service";
import { trendingCandles } from "../../test/fixtures";

const TF = "1h" as const;
const HOUR = 3_600_000;

describe("normalization + data-quality (deterministic)", () => {
  it("no candles → UNKNOWN with zero scores", () => {
    const r = normalize([], TF, 1_700_000_000_000);
    expect(r.dataState).toBe("UNKNOWN");
    expect(r.completenessScore).toBe(0);
    expect(r.freshnessScore).toBe(0);
  });

  it("too few candles (< 60) → UNKNOWN", () => {
    const end = 1_700_000_000_000;
    const candles = trendingCandles({ count: 30, direction: "up", endTime: end });
    expect(normalize(candles, TF, end).dataState).toBe("UNKNOWN");
  });

  it("plenty of fresh candles → OK", () => {
    const end = 1_700_000_000_000;
    const candles = trendingCandles({ count: 250, direction: "up", endTime: end });
    const r = normalize(candles, TF, end + HOUR);
    expect(r.dataState).toBe("OK");
    expect(r.completenessScore).toBe(100);
    expect(r.freshnessScore).toBeGreaterThanOrEqual(60);
  });

  it("enough candles but stale data → PARTIAL", () => {
    const end = 1_700_000_000_000;
    const candles = trendingCandles({ count: 250, direction: "up", endTime: end });
    const r = normalize(candles, TF, end + 6 * HOUR); // ~6 intervals old
    expect(r.dataState).toBe("PARTIAL");
  });

  it("de-dupes candles sharing an openTime", () => {
    const end = 1_700_000_000_000;
    const candles = trendingCandles({ count: 80, direction: "up", endTime: end });
    const withDupes = [...candles, candles[candles.length - 1]!];
    const r = normalize(withDupes, TF, end + HOUR);
    expect(r.candles.length).toBe(80);
  });
});
