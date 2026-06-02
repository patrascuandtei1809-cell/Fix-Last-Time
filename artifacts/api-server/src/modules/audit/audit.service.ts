import { db, auditLogsTable, reportsTable, type InsertAuditLog } from "@workspace/db";
import { eq, asc } from "drizzle-orm";

export type AuditEntry = {
  step: string;
  status: string;
  detail: Record<string, unknown>;
  createdAt: Date;
};

/**
 * In-memory collector used during a pipeline run. Entries are buffered and only
 * persisted after the report row exists (audit_logs.request_id → reports FK).
 */
export class AuditRecorder {
  readonly entries: AuditEntry[] = [];

  add(step: string, status: string, detail: Record<string, unknown> = {}): void {
    this.entries.push({ step, status, detail, createdAt: new Date() });
  }
}

export async function persistAudit(requestId: string, entries: AuditEntry[]): Promise<void> {
  if (entries.length === 0) return;
  const rows: InsertAuditLog[] = entries.map((e) => ({
    requestId,
    step: e.step,
    status: e.status,
    detail: e.detail,
  }));
  await db.insert(auditLogsTable).values(rows);
}

export async function getAuditTrace(
  requestId: string,
): Promise<{ requestId: string; entries: AuditEntry[] } | null> {
  const report = await db
    .select({ id: reportsTable.requestId })
    .from(reportsTable)
    .where(eq(reportsTable.requestId, requestId))
    .limit(1);
  if (report.length === 0) return null;

  const rows = await db
    .select()
    .from(auditLogsTable)
    .where(eq(auditLogsTable.requestId, requestId))
    .orderBy(asc(auditLogsTable.id));

  return {
    requestId,
    entries: rows.map((r) => ({
      step: r.step,
      status: r.status,
      detail: (r.detail ?? {}) as Record<string, unknown>,
      createdAt: r.createdAt,
    })),
  };
}
