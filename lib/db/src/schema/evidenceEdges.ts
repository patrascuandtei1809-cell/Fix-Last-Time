import { pgTable, serial, text, timestamp } from "drizzle-orm/pg-core";
import { reportsTable } from "./reports";

export type EvidenceRelation = "supports" | "contradicts" | "derived_from";

export const evidenceEdgesTable = pgTable("evidence_edges", {
  id: serial("id").primaryKey(),
  requestId: text("request_id")
    .notNull()
    .references(() => reportsTable.requestId),
  fromKey: text("from_key").notNull(),
  toKey: text("to_key").notNull(),
  relation: text("relation").$type<EvidenceRelation>().notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export type EvidenceEdge = typeof evidenceEdgesTable.$inferSelect;
export type InsertEvidenceEdge = typeof evidenceEdgesTable.$inferInsert;
