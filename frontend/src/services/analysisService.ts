import { http } from "@/api/client";
import type { AnalysisOut, AnalysisStepsOut, Finding } from "./types";

/**
 * Mirrors backend/app/api/analysis.py.
 *
 * The score is deterministic: heuristics.py sets the number with fixed
 * weights (contact 15, summary 20, experience 45, format 20) and the LLM only
 * writes the explanation. Re-running analyze() on unchanged content yields
 * the same overall_score.
 */
export const analysisService = {
  /** POST /api/resumes/{id}/analyze — runs the graph, returns 201. */
  run: (resumeId: string) => http.post<AnalysisOut>(`/resumes/${resumeId}/analyze`),

  /** GET /api/resumes/{id}/analysis — the most recent stored result.
   *  404 when the resume has never been analysed. */
  latest: (resumeId: string) => http.get<AnalysisOut>(`/resumes/${resumeId}/analysis`),

  /**
   * GET /api/resumes/{id}/analysis/steps — the guided editor's step list.
   *
   * Unlike latest() this is computed from the CURRENT structured_data on every
   * call, so it never 404s and always reflects unsaved-then-saved edits.
   */
  steps: (resumeId: string) =>
    http.get<AnalysisStepsOut>(`/resumes/${resumeId}/analysis/steps`),
};

/** "N points to 80+" from the reference screenshot. Never negative. */
export function pointsTo80(overallScore: number): number {
  return Math.max(0, 80 - overallScore);
}

/** Total count across all four categories — the "N recommended improvements"
 *  badge. Defensive against a category arriving without notes. */
export function totalFindings(analysis: AnalysisOut): number {
  return Object.values(analysis.category_scores).reduce(
    (sum, category) => sum + (category?.notes?.length ?? 0),
    0,
  );
}

/**
 * Highest-value findings first, so the guided flow leads with the edits that
 * actually move the number. Ties break on severity, then id for stability —
 * an unstable sort would make cards jump around as the user types.
 */
export function rankFindings(findings: Finding[]): Finding[] {
  const weight = { high: 0, medium: 1, low: 2 } as const;
  return [...findings].sort(
    (a, b) =>
      b.points - a.points ||
      weight[a.severity] - weight[b.severity] ||
      a.id.localeCompare(b.id),
  );
}
