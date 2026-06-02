import { describe, it, expect } from "vitest";
import { decide } from "./decision.service";

const OK = "OK" as const;

describe("decision rules (exact, deterministic)", () => {
  it("UNKNOWN data → NO_TRADE regardless of score", () => {
    expect(decide({ dataState: "UNKNOWN", confidence: 99, liquidityRisk: 0, institutionalScore: 99 }).decision).toBe(
      "NO_TRADE",
    );
  });

  it("confidence < 60 → NO_TRADE", () => {
    expect(decide({ dataState: OK, confidence: 59, liquidityRisk: 0, institutionalScore: 95 }).decision).toBe(
      "NO_TRADE",
    );
  });

  it("liquidity risk > 70 → AVOID (when confidence passes)", () => {
    expect(decide({ dataState: OK, confidence: 80, liquidityRisk: 71, institutionalScore: 95 }).decision).toBe("AVOID");
  });

  it("maps institutional score to the exact bands", () => {
    const at = (institutionalScore: number) =>
      decide({ dataState: OK, confidence: 80, liquidityRisk: 10, institutionalScore }).decision;
    expect(at(90)).toBe("STRONG_BUY");
    expect(at(75)).toBe("BUY");
    expect(at(55)).toBe("HOLD");
    expect(at(40)).toBe("REDUCE");
    expect(at(20)).toBe("SELL");
    expect(at(19)).toBe("AVOID");
  });

  it("surfaces an inconsistency when a strong score is gated to a non-buy", () => {
    const r = decide({ dataState: OK, confidence: 50, liquidityRisk: 10, institutionalScore: 85 });
    expect(r.decision).toBe("NO_TRADE");
    expect(r.inconsistencies.length).toBeGreaterThan(0);
  });

  it("clean BUY produces no inconsistencies", () => {
    const r = decide({ dataState: OK, confidence: 80, liquidityRisk: 10, institutionalScore: 78 });
    expect(r.decision).toBe("BUY");
    expect(r.inconsistencies).toEqual([]);
  });
});
