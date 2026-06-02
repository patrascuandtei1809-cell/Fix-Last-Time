import { Router, type IRouter } from "express";
import { GenerateResearchBody } from "@workspace/api-zod";
import type { Timeframe } from "../modules/providers/types";
import { generateResearch, getReport } from "../modules/research/research.service";
import { getAuditTrace } from "../modules/audit/audit.service";
import { getEvidenceGraph } from "../modules/evidence/evidence.service";

const router: IRouter = Router();

router.post("/research/generate", async (req, res) => {
  const parsed = GenerateResearchBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.issues.map((i) => i.message).join("; ") });
    return;
  }

  const { asset, timeframe, mode } = parsed.data;
  const report = await generateResearch(
    { asset, timeframe: (timeframe ?? "1h") as Timeframe, mode: mode ?? "standard" },
    req.log,
  );
  res.json(report);
});

router.get("/research/reports/:requestId", async (req, res) => {
  const report = await getReport(req.params.requestId);
  if (!report) {
    res.status(404).json({ error: "report not found" });
    return;
  }
  res.json(report);
});

router.get("/research/audit/:requestId", async (req, res) => {
  const trace = await getAuditTrace(req.params.requestId);
  if (!trace) {
    res.status(404).json({ error: "audit trace not found" });
    return;
  }
  res.json(trace);
});

router.get("/research/evidence/:requestId", async (req, res) => {
  const graph = await getEvidenceGraph(req.params.requestId);
  if (!graph) {
    res.status(404).json({ error: "evidence graph not found" });
    return;
  }
  res.json(graph);
});

export default router;
