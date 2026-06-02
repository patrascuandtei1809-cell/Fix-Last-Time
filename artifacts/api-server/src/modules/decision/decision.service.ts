import type { DataState } from "../normalization/normalization.service";

export type TradingDecision =
  | "STRONG_BUY"
  | "BUY"
  | "HOLD"
  | "REDUCE"
  | "SELL"
  | "AVOID"
  | "NO_TRADE";

export type DecisionInput = {
  dataState: DataState;
  confidence: number;
  liquidityRisk: number;
  institutionalScore: number;
};

export type DecisionResult = {
  decision: TradingDecision;
  inconsistencies: string[];
};

const CONFIDENCE_FLOOR = 60;
const LIQUIDITY_CEILING = 70;

/**
 * STEP 10 (decision) + STEP 11/consistency layer. Deterministic rules engine,
 * exactly per spec:
 *   UNKNOWN data           → NO_TRADE
 *   confidence < 60        → NO_TRADE
 *   liquidity risk > 70    → AVOID
 *   else map institutional score to a band.
 */
export function decide(input: DecisionInput): DecisionResult {
  const { dataState, confidence, liquidityRisk, institutionalScore } = input;

  let decision: TradingDecision;
  if (dataState === "UNKNOWN") {
    decision = "NO_TRADE";
  } else if (confidence < CONFIDENCE_FLOOR) {
    decision = "NO_TRADE";
  } else if (liquidityRisk > LIQUIDITY_CEILING) {
    decision = "AVOID";
  } else if (institutionalScore >= 90) {
    decision = "STRONG_BUY";
  } else if (institutionalScore >= 75) {
    decision = "BUY";
  } else if (institutionalScore >= 55) {
    decision = "HOLD";
  } else if (institutionalScore >= 40) {
    decision = "REDUCE";
  } else if (institutionalScore >= 20) {
    decision = "SELL";
  } else {
    decision = "AVOID";
  }

  // Consistency-check layer: surface contradictions instead of hiding them.
  const inconsistencies: string[] = [];
  if (institutionalScore > 80 && decision !== "BUY" && decision !== "STRONG_BUY") {
    inconsistencies.push(
      `score ${institutionalScore} > 80 but decision is ${decision} (gated by confidence/liquidity/data)`,
    );
  }
  if (confidence < CONFIDENCE_FLOOR && decision !== "NO_TRADE") {
    inconsistencies.push(`confidence ${confidence} < ${CONFIDENCE_FLOOR} but decision is ${decision}`);
  }

  return { decision, inconsistencies };
}
