import { describe, it, expect, afterAll } from "vitest";
import pino from "pino";
import {
  db,
  pool,
  reportsTable,
  auditLogsTable,
  evidenceNodesTable,
  evidenceEdgesTable,
} from "@workspace/db";
import { eq } from "drizzle-orm";
import { generateResearch, getReport, type MarketDataPort } from "./research.service";
import { getAuditTrace } from "../audit/audit.service";
import { getEvidenceGraph } from "../evidence/evidence.service";
import { trendingCandles } from "../../test/fixtures";

const log = pino({ level: "silent" });
const requestIds: string[] = [];

/** Fake provider — feeds deterministic candles so the pipeline never touches the network. */
function fakeProvider(candles: ReturnType<typeof trendingCandles>): MarketDataPort {
  return {
    getCandles: async () => ({ candles, source: "binance", attempts: [{ provider: "binance", ok: true }] }),
  };
}

afterAll(async () => {
  for (const id of requestIds) {
    await db.delete(evidenceEdgesTable).where(eq(evidenceEdgesTable.requestId, id));
    await db.delete(evidenceNodesTable).where(eq(evidenceNodesTable.requestId, id));
    await db.delete(auditLogsTable).where(eq(auditLogsTable.requestId, id));
    await db.delete(reportsTable).where(eq(reportsTable.requestId, id));
  }
  await pool.end();
});

describe("/research/generate pipeline (integration, real DB, injected provider)", () => {
  it("runs the full chain end-to-end and persists a retrievable report", async () => {
    const candles = trendingCandles({ count: 260, direction: "up", endTime: Date.now() });
    const report = await generateResearch(
      { asset: "BTC", timeframe: "1h", mode: "standard" },
      log,
      fakeProvider(candles),
    );
    requestIds.push(report.requestId);

    expect(report.dataState).toBe("OK");
    expect(report.metrics).not.toBeNull();
    expect(report.dataSource).toBe("binance");
    expect(["STRONG_BUY", "BUY", "HOLD", "REDUCE", "SELL", "AVOID", "NO_TRADE"]).toContain(report.decision);

    const fetched = await getReport(report.requestId);
    expect(fetched?.decision).toBe(report.decision);
  });

  it("creates an audit trace covering the full state machine", async () => {
    const candles = trendingCandles({ count: 260, direction: "up", endTime: Date.now() });
    const report = await generateResearch(
      { asset: "ETH", timeframe: "1h", mode: "standard" },
      log,
      fakeProvider(candles),
    );
    requestIds.push(report.requestId);

    const trace = await getAuditTrace(report.requestId);
    expect(trace).not.toBeNull();
    const steps = new Set(trace!.entries.map((e) => e.step));
    for (const step of ["INIT", "FETCH_DATA", "SCORE", "DECIDE", "BUILD_EVIDENCE", "STORE", "COMPLETE"]) {
      expect(steps.has(step)).toBe(true);
    }
  });

  it("creates a real evidence graph where the decision traces to a source", async () => {
    const candles = trendingCandles({ count: 260, direction: "up", endTime: Date.now() });
    const report = await generateResearch(
      { asset: "SOL", timeframe: "1h", mode: "standard" },
      log,
      fakeProvider(candles),
    );
    requestIds.push(report.requestId);

    const graph = await getEvidenceGraph(report.requestId);
    expect(graph).not.toBeNull();
    const keys = new Set(graph!.nodes.map((n) => n.key));
    expect(keys.has("source:binance")).toBe(true);
    expect(keys.has("conclusion:decision")).toBe(true);
    expect(keys.has(`report:${report.requestId}`)).toBe(true);
    expect(graph!.nodes.some((n) => n.type === "metric")).toBe(true);
    expect(graph!.edges.some((e) => e.relation === "derived_from")).toBe(true);

    // source closes the lineage with a timestamp anchor
    const source = graph!.nodes.find((n) => n.type === "source")!;
    expect(Number.isFinite(source.data.asOf as number)).toBe(true);
    expect(Number.isFinite(source.data.generatedAt as number)).toBe(true);
  });

  it("insufficient data → UNKNOWN / NO_TRADE with a minimal evidence graph", async () => {
    const candles = trendingCandles({ count: 30, direction: "up", endTime: Date.now() });
    const report = await generateResearch(
      { asset: "BTC", timeframe: "1h", mode: "standard" },
      log,
      fakeProvider(candles),
    );
    requestIds.push(report.requestId);

    expect(report.dataState).toBe("UNKNOWN");
    expect(report.decision).toBe("NO_TRADE");
    expect(report.metrics).toBeNull();

    const graph = await getEvidenceGraph(report.requestId);
    expect(graph!.nodes.some((n) => n.key === "finding:insufficient_data")).toBe(true);
    expect(graph!.nodes.some((n) => n.type === "metric")).toBe(false);
  });

  it("unknown asset → SAFE_MODE NO_TRADE", async () => {
    const candles = trendingCandles({ count: 260, direction: "up", endTime: Date.now() });
    const report = await generateResearch(
      { asset: "NOTACOIN", timeframe: "1h", mode: "standard" },
      log,
      fakeProvider(candles),
    );
    requestIds.push(report.requestId);
    expect(report.decision).toBe("NO_TRADE");
    expect(report.dataState).toBe("UNKNOWN");
  });
});
