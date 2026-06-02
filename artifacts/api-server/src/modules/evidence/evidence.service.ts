import {
  db,
  evidenceNodesTable,
  evidenceEdgesTable,
  reportsTable,
  type EvidenceNodeType,
  type EvidenceRelation,
  type ReportMetrics,
} from "@workspace/db";
import { eq, asc } from "drizzle-orm";

/**
 * STEP 6: EVIDENCE GRAPH BUILD.
 *
 * A real, queryable lineage graph (not a flat trace). Node types:
 *   source | metric | finding | conclusion | report
 * Edge relations:
 *   derived_from  — B was computed/derived from A   (B --derived_from--> A)
 *   supports      — a finding agrees with the decision
 *   contradicts   — a finding disagrees with the decision
 *
 * Every conclusion can be traced: conclusion → finding → metric → source,
 * and every node carries a timestamp once persisted. The builder is PURE and
 * deterministic — identical inputs produce an identical graph.
 */

export type EvidenceGraphNode = {
  key: string;
  type: EvidenceNodeType;
  label: string;
  data: Record<string, unknown>;
};

export type EvidenceGraphEdge = {
  from: string;
  to: string;
  relation: EvidenceRelation;
};

export type EvidenceGraph = {
  nodes: EvidenceGraphNode[];
  edges: EvidenceGraphEdge[];
};

export type EvidenceInput = {
  requestId: string;
  source: {
    name: string;
    count: number;
    attempts: number;
    timeframe: string;
    /** Epoch ms of the latest candle — the "as of" timestamp the data anchors to. Null when no data. */
    asOf: number | null;
    /** Epoch ms when this report was generated. */
    generatedAt: number;
  };
  dataState: string;
  decision: string;
  institutionalScore: number;
  confidence: number;
  liquidityRisk: number;
  metrics: ReportMetrics | null;
};

type Direction = "bullish" | "bearish" | "neutral";

function decisionDirection(decision: string): Direction {
  if (decision === "STRONG_BUY" || decision === "BUY") return "bullish";
  if (decision === "SELL" || decision === "REDUCE" || decision === "AVOID") return "bearish";
  return "neutral"; // HOLD / NO_TRADE
}

class GraphBuilder {
  private readonly nodeMap = new Map<string, EvidenceGraphNode>();
  private readonly edgeSet = new Set<string>();
  private readonly edgeList: EvidenceGraphEdge[] = [];

  node(key: string, type: EvidenceNodeType, label: string, data: Record<string, unknown>): string {
    if (!this.nodeMap.has(key)) this.nodeMap.set(key, { key, type, label, data });
    return key;
  }

  edge(from: string, to: string, relation: EvidenceRelation): void {
    const id = `${from}|${relation}|${to}`;
    if (this.edgeSet.has(id)) return;
    this.edgeSet.add(id);
    this.edgeList.push({ from, to, relation });
  }

  build(): EvidenceGraph {
    return { nodes: [...this.nodeMap.values()], edges: this.edgeList };
  }
}

export function buildEvidenceGraph(input: EvidenceInput): EvidenceGraph {
  const g = new GraphBuilder();

  // ---- source -------------------------------------------------------------
  const sourceKey = g.node("source:" + input.source.name, "source", input.source.name, {
    provider: input.source.name,
    candleCount: input.source.count,
    attempts: input.source.attempts,
    timeframe: input.source.timeframe,
    // Timestamp anchor — closes the lineage chain conclusion → finding → metric → source → timestamp.
    asOf: input.source.asOf,
    generatedAt: input.source.generatedAt,
  });

  const reportKey = g.node("report:" + input.requestId, "report", "report", {
    decision: input.decision,
    dataState: input.dataState,
  });
  const decisionKey = g.node("conclusion:decision", "conclusion", "decision", {
    decision: input.decision,
    dataState: input.dataState,
  });
  g.edge(reportKey, decisionKey, "derived_from");

  // ---- degraded path: no metrics (insufficient data / SAFE_MODE) ----------
  if (!input.metrics) {
    const insufficient = g.node("finding:insufficient_data", "finding", "insufficient data", {
      dataState: input.dataState,
      direction: "neutral",
    });
    g.edge(insufficient, sourceKey, "derived_from");
    g.edge(decisionKey, insufficient, "derived_from");
    return g.build();
  }

  const m = input.metrics;

  // ---- metric nodes (each derived_from the source) ------------------------
  const metricEntries: Array<[string, unknown]> = [
    ["lastClose", m.lastClose],
    ["ema50", m.ema50],
    ["ema200", m.ema200],
    ["rsi", m.rsi],
    ["macdHistogram", m.macdHistogram],
    ["atrPercent", m.atrPercent],
    ["volume", m.volume],
    ["avgVolume20", m.avgVolume20],
    ["volumeRatio", m.volumeRatio],
    ["trend", m.trend],
    ["regime", m.regime],
  ];
  const metricKey: Record<string, string> = {};
  for (const [name, value] of metricEntries) {
    const key = g.node("metric:" + name, "metric", name, { value });
    metricKey[name] = key;
    g.edge(key, sourceKey, "derived_from");
  }

  // ---- findings (interpretations derived_from metrics) --------------------
  const findings: Array<{ key: string; direction: Direction }> = [];

  const trendDir: Direction = m.trend === "UP" ? "bullish" : m.trend === "DOWN" ? "bearish" : "neutral";
  const trendFinding = g.node("finding:trend", "finding", `trend ${m.trend}`, { direction: trendDir, trend: m.trend });
  g.edge(trendFinding, metricKey.trend!, "derived_from");
  g.edge(trendFinding, metricKey.ema50!, "derived_from");
  g.edge(trendFinding, metricKey.ema200!, "derived_from");
  findings.push({ key: trendFinding, direction: trendDir });

  const momoDir: Direction = m.macdHistogram > 0 ? "bullish" : m.macdHistogram < 0 ? "bearish" : "neutral";
  const momoFinding = g.node("finding:momentum", "finding", "momentum", {
    direction: momoDir,
    macdHistogram: m.macdHistogram,
  });
  g.edge(momoFinding, metricKey.macdHistogram!, "derived_from");
  findings.push({ key: momoFinding, direction: momoDir });

  const rsiDir: Direction = m.rsi < 35 ? "bullish" : m.rsi > 65 ? "bearish" : "neutral";
  const rsiLabel = m.rsi < 35 ? "RSI oversold" : m.rsi > 65 ? "RSI overbought" : "RSI neutral";
  const rsiFinding = g.node("finding:rsi", "finding", rsiLabel, { direction: rsiDir, rsi: m.rsi });
  g.edge(rsiFinding, metricKey.rsi!, "derived_from");
  findings.push({ key: rsiFinding, direction: rsiDir });

  const volLabel = m.volumeRatio >= 1 ? "volume above average" : "volume below average";
  const volumeFinding = g.node("finding:volume", "finding", volLabel, {
    direction: "neutral",
    volumeRatio: m.volumeRatio,
  });
  g.edge(volumeFinding, metricKey.volumeRatio!, "derived_from");
  g.edge(volumeFinding, metricKey.volume!, "derived_from");
  g.edge(volumeFinding, metricKey.avgVolume20!, "derived_from");

  const volatilityFinding = g.node("finding:volatility", "finding", `volatility ${m.regime}`, {
    direction: "neutral",
    atrPercent: m.atrPercent,
    regime: m.regime,
  });
  g.edge(volatilityFinding, metricKey.atrPercent!, "derived_from");
  g.edge(volatilityFinding, metricKey.regime!, "derived_from");

  // ---- conclusions --------------------------------------------------------
  const scoreConclusion = g.node("conclusion:institutionalScore", "conclusion", "institutional score", {
    value: input.institutionalScore,
  });
  for (const f of [trendFinding, momoFinding, rsiFinding, volumeFinding, volatilityFinding]) {
    g.edge(scoreConclusion, f, "derived_from");
  }

  const confidenceConclusion = g.node("conclusion:confidence", "conclusion", "confidence", {
    value: input.confidence,
  });
  g.edge(confidenceConclusion, scoreConclusion, "derived_from");
  for (const f of findings) g.edge(confidenceConclusion, f.key, "derived_from");

  const liquidityConclusion = g.node("conclusion:liquidityRisk", "conclusion", "liquidity risk", {
    value: input.liquidityRisk,
  });
  g.edge(liquidityConclusion, volumeFinding, "derived_from");
  g.edge(liquidityConclusion, volatilityFinding, "derived_from");

  // decision derives from the three conclusions
  g.edge(decisionKey, scoreConclusion, "derived_from");
  g.edge(decisionKey, confidenceConclusion, "derived_from");
  g.edge(decisionKey, liquidityConclusion, "derived_from");

  // ---- supports / contradicts (directional findings vs the decision) ------
  const dir = decisionDirection(input.decision);
  if (dir !== "neutral") {
    for (const f of findings) {
      if (f.direction === "neutral") continue;
      g.edge(f.key, decisionKey, f.direction === dir ? "supports" : "contradicts");
    }
  }

  return g.build();
}

export async function persistEvidenceGraph(requestId: string, graph: EvidenceGraph): Promise<void> {
  if (graph.nodes.length > 0) {
    await db.insert(evidenceNodesTable).values(
      graph.nodes.map((n) => ({
        requestId,
        nodeKey: n.key,
        type: n.type,
        label: n.label,
        data: n.data,
      })),
    );
  }
  if (graph.edges.length > 0) {
    await db.insert(evidenceEdgesTable).values(
      graph.edges.map((e) => ({
        requestId,
        fromKey: e.from,
        toKey: e.to,
        relation: e.relation,
      })),
    );
  }
}

export async function getEvidenceGraph(
  requestId: string,
): Promise<{ requestId: string; nodes: EvidenceGraphNode[]; edges: EvidenceGraphEdge[] } | null> {
  const report = await db
    .select({ id: reportsTable.requestId })
    .from(reportsTable)
    .where(eq(reportsTable.requestId, requestId))
    .limit(1);
  if (report.length === 0) return null;

  const nodeRows = await db
    .select()
    .from(evidenceNodesTable)
    .where(eq(evidenceNodesTable.requestId, requestId))
    .orderBy(asc(evidenceNodesTable.id));
  const edgeRows = await db
    .select()
    .from(evidenceEdgesTable)
    .where(eq(evidenceEdgesTable.requestId, requestId))
    .orderBy(asc(evidenceEdgesTable.id));

  return {
    requestId,
    nodes: nodeRows.map((n) => ({
      key: n.nodeKey,
      type: n.type,
      label: n.label,
      data: (n.data ?? {}) as Record<string, unknown>,
    })),
    edges: edgeRows.map((e) => ({ from: e.fromKey, to: e.toKey, relation: e.relation })),
  };
}
