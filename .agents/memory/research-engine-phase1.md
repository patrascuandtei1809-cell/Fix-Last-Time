---
name: Research engine Phase 1 gotchas
description: Non-obvious traps building the deterministic research/decision engine in artifacts/api-server (drizzle-zod insert type, SAFE_MODE persistence boundary).
---

# Research / Decision Engine (artifacts/api-server) — durable lessons

## drizzle-zod Insert type vs table $inferInsert for jsonb columns
The `Insert<Table>` type exported via drizzle-zod (`createInsertSchema`) infers a
`jsonb` column as a **generic `Json`** type, NOT the column's typed shape. The
Drizzle table's own `typeof table.$inferInsert` keeps the typed shape (e.g.
`ReportMetrics | null`). Mixing them fails: a DTO built from the zod `Insert`
type has `metrics: Json` which is **not assignable** to the table insert's
`metrics: ReportMetrics | null`.

**Rule:** for the row you pass to `db.insert(table).values(...)`, type it as
`typeof table.$inferInsert`. Don't reuse the drizzle-zod `Insert<...>` type for
DB writes — use it for API/validation boundaries only.

## "SAFE_MODE / never throw to client" must wrap persistence too
The orchestrator's pipeline try/catch (which converts any compute failure into a
SAFE_MODE NO_TRADE report) does NOT by itself satisfy "never 500" — the DB
**store** step runs after that catch. A failing `db.insert` there throws and the
route 500s.

**Rule:** wrap `persistReport` in its own try/catch and still return the
(un-stored, degraded) report. Audit rows FK to the report, so only persist the
audit trace when the report actually stored. Record `STORE`/`COMPLETE` status as
ok/error + a `stored` flag so the degraded path is visible in the trace.
**Why:** the spec's hard contract is "any failure → SAFE_MODE, never throw"; a DB
outage is a failure too.

## requestId / circuit-breaker are intentionally non-deterministic
Determinism is scoped to the DECISION given the same market data — verify by
diffing two identical-input responses while ignoring `requestId` (unique per
call) and `generatedAt`. The in-memory circuit breaker (provider failover state)
and the random requestId are by design and are NOT determinism violations.
