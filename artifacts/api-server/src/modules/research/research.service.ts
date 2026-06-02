import { randomUUID } from "node:crypto";
import { db, assetsTable, reportsTable, type Asset, type ReportMetrics } from "@workspace/db";
import { eq } from "drizzle-orm";
import type { Logger } from "pino";
import type { AssetDescriptor, Timeframe } from "../providers/types";
import { MarketDataService } from "../providers/provider.service";
import { normalize } from "../normalization/normalization.service";
import { score } from "../scoring/scoring.service";
import { decide } from "../decision/decision.service";
import { AuditRecorder, persistAudit } from "../audit/audit.service";

export type GenerateInput = {
  asset: string;
  timeframe: Timeframe;
  mode: string;
};

export type ResearchReportDTO = {
  requestId: string;
  asset: string;
  timeframe: string;
  mode: string;
  generatedAt: Date;
  dataState: string;
  completenessScore: number;
  freshnessScore: number;
  institutionalScore: number;
  confidence: number;
  liquidityRisk: number;
  decision: string;
  metrics: ReportMetrics | null;
  reasons: string[];
  inconsistencies: string[];
  dataSource: string;
};

const CANDLE_LIMIT = 500;

const DEFAULT_ASSETS: Record<string, { name: string; binanceSymbol: string; coinbaseProduct: string }> = {
  BTC: { name: "Bitcoin", binanceSymbol: "BTCUSDT", coinbaseProduct: "BTC-USD" },
  ETH: { name: "Ethereum", binanceSymbol: "ETHUSDT", coinbaseProduct: "ETH-USD" },
  SOL: { name: "Solana", binanceSymbol: "SOLUSDT", coinbaseProduct: "SOL-USD" },
};

const marketData = new MarketDataService();

/** STEP 3: entity resolution — map a symbol/alias to a canonical asset row. */
async function resolveAsset(symbol: string): Promise<Asset | null> {
  const up = symbol.trim().toUpperCase();
  const existing = await db.select().from(assetsTable).where(eq(assetsTable.symbol, up)).limit(1);
  if (existing.length > 0) return existing[0]!;

  const seed = DEFAULT_ASSETS[up];
  if (!seed) return null;

  const inserted = await db
    .insert(assetsTable)
    .values({ symbol: up, name: seed.name, binanceSymbol: seed.binanceSymbol, coinbaseProduct: seed.coinbaseProduct })
    .onConflictDoNothing()
    .returning();
  if (inserted.length > 0) return inserted[0]!;

  const again = await db.select().from(assetsTable).where(eq(assetsTable.symbol, up)).limit(1);
  return again[0] ?? null;
}

export async function generateResearch(input: GenerateInput, log: Logger): Promise<ResearchReportDTO> {
  const requestId = randomUUID();
  const generatedAt = new Date();
  const recorder = new AuditRecorder();
  recorder.add("INIT", "ok", { requestId, ...input });

  let assetId: number | null = null;
  let report: ResearchReportDTO;

  try {
    const asset = await resolveAsset(input.asset);
    if (!asset) {
      recorder.add("ENTITY_RESOLUTION", "error", { reason: "unknown asset", asset: input.asset });
      report = safeReport(requestId, input, generatedAt, "unknown asset — not in canonical registry");
    } else {
      assetId = asset.id;
      recorder.add("ENTITY_RESOLUTION", "ok", { symbol: asset.symbol, binanceSymbol: asset.binanceSymbol });

      const descriptor: AssetDescriptor = {
        symbol: asset.symbol,
        binanceSymbol: asset.binanceSymbol,
        coinbaseProduct: asset.coinbaseProduct,
      };

      const fetched = await marketData.getCandles({ asset: descriptor, timeframe: input.timeframe, limit: CANDLE_LIMIT });
      recorder.add("FETCH_DATA", fetched.candles.length > 0 ? "ok" : "error", {
        source: fetched.source,
        count: fetched.candles.length,
        attempts: fetched.attempts,
      });

      const norm = normalize(fetched.candles, input.timeframe, generatedAt.getTime());
      recorder.add("NORMALIZE", "ok", {
        completeness: norm.completenessScore,
        freshness: norm.freshnessScore,
        dataState: norm.dataState,
        notes: norm.notes,
      });
      // STEP 5: truth resolution — single source for now, no conflicts to resolve.
      recorder.add("RESOLVE_CONFLICTS", "ok", { sources: fetched.source === "none" ? 0 : 1 });

      if (norm.dataState === "UNKNOWN" || norm.candles.length < 60) {
        recorder.add("SCORE", "skipped", { reason: "insufficient data" });
        const dec = decide({ dataState: "UNKNOWN", confidence: 0, liquidityRisk: 0, institutionalScore: 0 });
        recorder.add("DECIDE", "ok", { decision: dec.decision });
        report = {
          requestId,
          asset: asset.symbol,
          timeframe: input.timeframe,
          mode: input.mode,
          generatedAt,
          dataState: "UNKNOWN",
          completenessScore: norm.completenessScore,
          freshnessScore: norm.freshnessScore,
          institutionalScore: 0,
          confidence: 0,
          liquidityRisk: 0,
          decision: dec.decision,
          metrics: null,
          reasons: ["insufficient market data for a reliable decision", ...norm.notes],
          inconsistencies: dec.inconsistencies,
          dataSource: fetched.source,
        };
      } else {
        const scoring = score(norm.candles, norm);
        recorder.add("SCORE", "ok", { institutionalScore: scoring.institutionalScore, metrics: scoring.metrics });
        recorder.add("CONFIDENCE", "ok", { confidence: scoring.confidence, liquidityRisk: scoring.liquidityRisk });

        const dec = decide({
          dataState: norm.dataState,
          confidence: scoring.confidence,
          liquidityRisk: scoring.liquidityRisk,
          institutionalScore: scoring.institutionalScore,
        });
        recorder.add("DECIDE", "ok", { decision: dec.decision, inconsistencies: dec.inconsistencies });

        report = {
          requestId,
          asset: asset.symbol,
          timeframe: input.timeframe,
          mode: input.mode,
          generatedAt,
          dataState: norm.dataState,
          completenessScore: norm.completenessScore,
          freshnessScore: norm.freshnessScore,
          institutionalScore: scoring.institutionalScore,
          confidence: scoring.confidence,
          liquidityRisk: scoring.liquidityRisk,
          decision: dec.decision,
          metrics: scoring.metrics,
          reasons: scoring.reasons,
          inconsistencies: dec.inconsistencies,
          dataSource: fetched.source,
        };
      }
    }
  } catch (err) {
    // ANY FAILURE → SAFE_MODE. Never throw to the client; emit NO_TRADE.
    const message = err instanceof Error ? err.message : String(err);
    log.error({ err, requestId }, "research pipeline failed → SAFE_MODE");
    recorder.add("SAFE_MODE", "error", { error: message });
    report = safeReport(requestId, input, generatedAt, `pipeline failure: ${message}`);
  }

  // STORE: persist report first (FK target), then the audit trace.
  // Persistence failures must NEVER throw to the client — the report is still
  // returned (degraded: not durably stored / no retrievable audit trace).
  let stored = false;
  try {
    await persistReport(report, assetId);
    stored = true;
    recorder.add("STORE", "ok", { requestId });
  } catch (err) {
    log.error({ err, requestId }, "failed to persist report — returning un-stored report");
    recorder.add("STORE", "error", { error: err instanceof Error ? err.message : String(err) });
  }
  recorder.add("COMPLETE", stored ? "ok" : "error", {
    decision: report.decision,
    dataState: report.dataState,
    stored,
  });
  // Audit rows FK to the report; only persist the trace if the report stored.
  if (stored) {
    try {
      await persistAudit(requestId, recorder.entries);
    } catch (err) {
      log.error({ err, requestId }, "failed to persist audit trace");
    }
  }

  return report;
}

function safeReport(
  requestId: string,
  input: GenerateInput,
  generatedAt: Date,
  reason: string,
): ResearchReportDTO {
  return {
    requestId,
    asset: input.asset.toUpperCase(),
    timeframe: input.timeframe,
    mode: input.mode,
    generatedAt,
    dataState: "UNKNOWN",
    completenessScore: 0,
    freshnessScore: 0,
    institutionalScore: 0,
    confidence: 0,
    liquidityRisk: 0,
    decision: "NO_TRADE",
    metrics: null,
    reasons: [reason],
    inconsistencies: [],
    dataSource: "none",
  };
}

async function persistReport(report: ResearchReportDTO, assetId: number | null): Promise<void> {
  const row: typeof reportsTable.$inferInsert = { ...report, assetId };
  await db.insert(reportsTable).values(row);
}

export async function getReport(requestId: string): Promise<ResearchReportDTO | null> {
  const rows = await db.select().from(reportsTable).where(eq(reportsTable.requestId, requestId)).limit(1);
  if (rows.length === 0) return null;
  const r = rows[0]!;
  return {
    requestId: r.requestId,
    asset: r.asset,
    timeframe: r.timeframe,
    mode: r.mode,
    generatedAt: r.generatedAt,
    dataState: r.dataState,
    completenessScore: r.completenessScore,
    freshnessScore: r.freshnessScore,
    institutionalScore: r.institutionalScore,
    confidence: r.confidence,
    liquidityRisk: r.liquidityRisk,
    decision: r.decision,
    metrics: r.metrics ?? null,
    reasons: r.reasons,
    inconsistencies: r.inconsistencies,
    dataSource: r.dataSource,
  };
}
