import { http } from "@/api/client";
import type { SuggestionBuckets, TailorIn, TailorSessionOut } from "./types";

/**
 * Mirrors backend/app/api/tailoring.py.
 *
 * start() drives the full multi-agent LangGraph pass — JD analyst, writer,
 * critic, with up to 2 revision rounds. On UnoRouter's free tier that is
 * several sequential LLM calls, so it can take well over a minute. It relies
 * on the default 90 s timeout from VITE_API_TIMEOUT_MS; raise that rather
 * than adding a per-call override, so the value stays configurable.
 */
export const tailorService = {
  /** POST /api/resumes/{id}/tailor — body {jd_title, jd_content}. 201. */
  start: (resumeId: string, jd: TailorIn) =>
    http.post<TailorSessionOut>(`/resumes/${resumeId}/tailor`, {
      jd_title: jd.jd_title ?? "",
      jd_content: jd.jd_content,
    }),

  /** GET /api/resumes/{id}/tailor — past sessions, newest first. */
  listSessions: (resumeId: string) =>
    http.get<TailorSessionOut[]>(`/resumes/${resumeId}/tailor`),

  /** GET /api/resumes/{id}/tailor/{sessionId}/suggestions
   *  → {active, matched, rejected}; the three tabs are the array lengths. */
  suggestions: (resumeId: string, sessionId: string) =>
    http.get<SuggestionBuckets>(
      `/resumes/${resumeId}/tailor/${sessionId}/suggestions`,
    ),
};

/** "X of Y keywords integrated" from the reference. Y is matched + gap. */
export function keywordProgress(session: TailorSessionOut) {
  const matched = session.matched_keywords.length;
  const total = matched + session.gap_keywords.length;
  return { matched, total, remaining: session.gap_keywords.length };
}
