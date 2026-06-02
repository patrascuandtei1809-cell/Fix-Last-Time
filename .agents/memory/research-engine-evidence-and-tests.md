---
name: Research engine evidence graph + test seam
description: Durable decisions for the api-server research/decision engine's evidence graph and its vitest suite.
---

# Evidence graph lineage + research-engine test seam

## Timestamp lives ON the source node, not as a 6th node type
The evidence-graph node types are fixed at `source|metric|finding|conclusion|report`.
When the spec says lineage ends at `conclusion → finding → metric → source → timestamp`,
the timestamp is an **anchor carried in the `source` node's data** (`asOf` = latest
candle openTime, `generatedAt` = report time) — do NOT invent a `timestamp` node type.

**Why:** the user's spec enumerates exactly five node types; adding a sixth to satisfy
the word "timestamp" would deviate from the agreed model. A code review flagged the
missing anchor; the honest fix is to timestamp the source, not to grow the schema.

**How to apply:** any change to the lineage requirement is satisfied by enriching an
existing node's `data`, unless the node-type enum itself is intentionally changed in
`lib/db/src/schema`.

## Tests use an injectable MarketDataPort; they never hit the network
`generateResearch()` takes a `MarketDataPort` param (default = real `marketData`).
Tests pass a fake provider returning deterministic synthetic candles.

**Why:** Replit is geo-blocked from Binance, so any test that fetches real candles is
flaky/dead. The DI seam is the only reliable way to exercise the full pipeline.

**How to apply:** never write a test that calls the real provider; build candles with
`src/test/fixtures.ts` (`trendingCandles`) and inject them.

## Integration test closes the shared pg pool per file
The DB-using integration test calls `await pool.end()` in `afterAll`. vitest isolates
each test file in its own worker (forks), so this is per-file safe and prevents the
worker hanging on an open pg handle. Cleanup deletes evidence_edges → evidence_nodes →
audit_logs → reports by `requestId` (FK order), never the shared `assets` rows.

**Why:** a code review worried `pool.end()` on a singleton could break later DB tests;
under vitest's default per-file worker isolation it cannot. Without it, the open pool
keeps the worker alive.
