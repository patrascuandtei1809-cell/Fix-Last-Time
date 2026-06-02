import { pgTable, text, integer, real, timestamp, jsonb } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";
import { assetsTable } from "./assets";

export type ReportMetrics = {
  lastClose: number;
  ema50: number;
  ema200: number;
  rsi: number;
  macdHistogram: number;
  atrPercent: number;
  volume: number;
  avgVolume20: number;
  volumeRatio: number;
  trend: string;
  regime: string;
};

export const reportsTable = pgTable("reports", {
  requestId: text("request_id").primaryKey(),
  assetId: integer("asset_id").references(() => assetsTable.id),
  asset: text("asset").notNull(),
  timeframe: text("timeframe").notNull(),
  mode: text("mode").notNull(),
  generatedAt: timestamp("generated_at", { withTimezone: true }).notNull(),
  dataState: text("data_state").notNull(),
  completenessScore: real("completeness_score").notNull(),
  freshnessScore: real("freshness_score").notNull(),
  institutionalScore: real("institutional_score").notNull(),
  confidence: real("confidence").notNull(),
  liquidityRisk: real("liquidity_risk").notNull(),
  decision: text("decision").notNull(),
  metrics: jsonb("metrics").$type<ReportMetrics | null>(),
  reasons: jsonb("reasons").$type<string[]>().notNull(),
  inconsistencies: jsonb("inconsistencies").$type<string[]>().notNull(),
  dataSource: text("data_source").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const insertReportSchema = createInsertSchema(reportsTable).omit({
  createdAt: true,
});
export type InsertReport = z.infer<typeof insertReportSchema>;
export type Report = typeof reportsTable.$inferSelect;
