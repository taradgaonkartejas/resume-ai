import { http } from "@/api/client";
import type { AgentRunOut, HealthOut, JobDescriptionOut } from "./types";

/**
 * Mirrors backend/app/api/health.py and app/api/admin.py.
 *
 * health() is the fastest way to confirm the whole stack is wired: it reports
 * the database, whether pgvector is present, whether storage resolved to
 * MinIO or fell back to disk, and whether an LLM key is configured.
 */
export const systemService = {
  /** GET /api/health — no X-User-Id required. */
  health: () => http.get<HealthOut>("/health"),

  /** GET /api/sample-jd — the seeded SRE job description, for prefilling the
   *  tailor form during a demo. */
  sampleJd: () => http.get<JobDescriptionOut>("/sample-jd"),

  /** GET /api/admin/agent-runs?limit=100 — LLM call log: agent, task, model,
   *  latency and token counts. Useful for showing why a tailor run was slow. */
  agentRuns: (limit = 100) =>
    http.get<AgentRunOut[]>("/admin/agent-runs", { query: { limit } }),
};
