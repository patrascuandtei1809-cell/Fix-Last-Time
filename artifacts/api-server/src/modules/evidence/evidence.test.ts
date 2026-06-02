import { describe, it, expect } from "vitest";
import { buildEvidenceGraph, type EvidenceGraph } from "./evidence.service";
import type { ReportMetrics } from "@workspace/db";

const bullishMetrics: ReportMetrics = {
  lastClose: 110,
  ema50: 108,
  ema200: 100,
  rsi: 62,
  macdHistogram: 1.5,
  atrPercent: 0.6,
  volume: 1200,
  avgVolume20: 1000,
  volumeRatio: 1.2,
  trend: "UP",
  regime: "TREND",
};

/** Follow only derived_from edges from a start node; does it reach a `source`? */
function reachesSource(graph: EvidenceGraph, startKey: string): boolean {
  const typeOf = new Map(graph.nodes.map((n) => [n.key, n.type]));
  const adj = new Map<string, string[]>();
  for (const e of graph.edges) {
    if (e.relation !== "derived_from") continue;
    (adj.get(e.from) ?? adj.set(e.from, []).get(e.from)!).push(e.to);
  }
  const seen = new Set<string>();
  const stack = [startKey];
  while (stack.length) {
    const k = stack.pop()!;
    if (seen.has(k)) continue;
    seen.add(k);
    if (typeOf.get(k) === "source") return true;
    for (const nx of adj.get(k) ?? []) stack.push(nx);
  }
  return false;
}

describe("evidence graph builder (pure, deterministic)", () => {
  it("builds a full graph whose decision traces to a source", () => {
    const g = buildEvidenceGraph({
      requestId: "req-1",
      source: { name: "binance", count: 250, attempts: 1, timeframe: "1h", asOf: 1_700_000_000_000, generatedAt: 1_700_000_003_600 },
      dataState: "OK",
      decision: "BUY",
      institutionalScore: 78,
      confidence: 80,
      liquidityRisk: 20,
      metrics: bullishMetrics,
    });

    const keys = new Set(g.nodes.map((n) => n.key));
    expect(keys.has("source:binance")).toBe(true);
    expect(keys.has("metric:rsi")).toBe(true);
    expect(keys.has("finding:trend")).toBe(true);
    expect(keys.has("conclusion:institutionalScore")).toBe(true);
    expect(keys.has("conclusion:decision")).toBe(true);
    expect(keys.has("report:req-1")).toBe(true);

    // every conclusion traces conclusion → finding → metric → source
    for (const c of g.nodes.filter((n) => n.type === "conclusion")) {
      expect(reachesSource(g, c.key)).toBe(true);
    }
    expect(reachesSource(g, "report:req-1")).toBe(true);

    // ...and the source closes the chain with a timestamp anchor (→ source → timestamp)
    const source = g.nodes.find((n) => n.key === "source:binance")!;
    expect(Number.isFinite(source.data.asOf as number)).toBe(true);
    expect(Number.isFinite(source.data.generatedAt as number)).toBe(true);
  });

  it("emits supports/contradicts between directional findings and a directional decision", () => {
    const g = buildEvidenceGraph({
      requestId: "req-2",
      source: { name: "binance", count: 250, attempts: 1, timeframe: "1h", asOf: 1_700_000_000_000, generatedAt: 1_700_000_003_600 },
      dataState: "OK",
      decision: "BUY",
      institutionalScore: 78,
      confidence: 80,
      liquidityRisk: 20,
      metrics: bullishMetrics,
    });
    const relations = g.edges.filter((e) => e.to === "conclusion:decision" && e.relation !== "derived_from");
    expect(relations.some((e) => e.relation === "supports")).toBe(true);
    // trend UP + momentum bullish support a BUY; RSI 62 is neutral (no contradiction forced)
    expect(relations.every((e) => e.relation === "supports" || e.relation === "contradicts")).toBe(true);
  });

  it("contradicts appear when findings disagree with the decision direction", () => {
    const g = buildEvidenceGraph({
      requestId: "req-3",
      source: { name: "binance", count: 250, attempts: 1, timeframe: "1h", asOf: 1_700_000_000_000, generatedAt: 1_700_000_003_600 },
      dataState: "OK",
      decision: "SELL",
      institutionalScore: 30,
      confidence: 70,
      liquidityRisk: 20,
      metrics: bullishMetrics, // bullish findings vs a bearish decision
    });
    const contradicts = g.edges.filter((e) => e.to === "conclusion:decision" && e.relation === "contradicts");
    expect(contradicts.length).toBeGreaterThan(0);
  });

  it("degraded path (no metrics) still traces decision → insufficient_data → source", () => {
    const g = buildEvidenceGraph({
      requestId: "req-4",
      source: { name: "none", count: 0, attempts: 2, timeframe: "1h", asOf: null, generatedAt: 1_700_000_003_600 },
      dataState: "UNKNOWN",
      decision: "NO_TRADE",
      institutionalScore: 0,
      confidence: 0,
      liquidityRisk: 0,
      metrics: null,
    });
    const keys = new Set(g.nodes.map((n) => n.key));
    expect(keys.has("finding:insufficient_data")).toBe(true);
    expect(keys.has("conclusion:decision")).toBe(true);
    expect(reachesSource(g, "conclusion:decision")).toBe(true);
    expect(g.nodes.some((n) => n.type === "metric")).toBe(false);
  });

  it("is deterministic — identical inputs yield an identical graph", () => {
    const input = {
      requestId: "req-5",
      source: { name: "binance", count: 250, attempts: 1, timeframe: "1h", asOf: 1_700_000_000_000, generatedAt: 1_700_000_003_600 },
      dataState: "OK",
      decision: "BUY",
      institutionalScore: 78,
      confidence: 80,
      liquidityRisk: 20,
      metrics: bullishMetrics,
    };
    expect(buildEvidenceGraph(input)).toEqual(buildEvidenceGraph(input));
  });
});
