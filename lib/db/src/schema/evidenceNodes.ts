import { pgTable, serial, text, timestamp, jsonb, uniqueIndex } from "drizzle-orm/pg-core";
import { reportsTable } from "./reports";

export type EvidenceNodeType = "source" | "metric" | "finding" | "conclusion" | "report";

export const evidenceNodesTable = pgTable(
  "evidence_nodes",
  {
    id: serial("id").primaryKey(),
    requestId: text("request_id")
      .notNull()
      .references(() => reportsTable.requestId),
    nodeKey: text("node_key").notNull(),
    type: text("type").$type<EvidenceNodeType>().notNull(),
    label: text("label").notNull(),
    data: jsonb("data").$type<Record<string, unknown>>(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => ({
    reqKeyUniq: uniqueIndex("evidence_nodes_req_key_uniq").on(t.requestId, t.nodeKey),
  }),
);

export type EvidenceNode = typeof evidenceNodesTable.$inferSelect;
export type InsertEvidenceNode = typeof evidenceNodesTable.$inferInsert;
