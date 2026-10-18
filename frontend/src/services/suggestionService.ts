import { http } from "@/api/client";
import { LLM_REQUEST_TIMEOUT_MS } from "@/lib/env";
import type { AcceptAllOut, SuggestionOut } from "./types";

/**
 * Mirrors backend/app/api/suggestions.py — one route, three actions.
 *
 * Accepting is a five-step server transaction (ownership check → patch the
 * structured data → snapshot a version → set status → recompute the match
 * percent), committed once. That is why the UI updates optimistically: a
 * spinner on every click would feel broken.
 */
export const suggestionService = {
  /** PATCH /api/suggestions/{id} {"action":"accept"} */
  accept: (suggestionId: string) =>
    http.patch<SuggestionOut>(`/suggestions/${suggestionId}`, { action: "accept" }),

  /** PATCH /api/suggestions/{id} {"action":"reject"} */
  reject: (suggestionId: string) =>
    http.patch<SuggestionOut>(`/suggestions/${suggestionId}`, { action: "reject" }),

  /**
   * POST /api/suggestions/{id}/answer
   *
   * Resolves a `needs_input` card. Returns 200 whether the user attested
   * (card becomes `pending`) or declined (card becomes `rejected`) --
   * declining is a normal outcome, not an error. 422 is reserved for a bare
   * confirm on a question that needs text.
   *
   * `confirmed` is the bare Yes button and is only accepted when
   * `requires_text` is false.
   */
  answer: (suggestionId: string, body: { answer?: string; confirmed?: boolean }) =>
    http.post<SuggestionOut>(`/suggestions/${suggestionId}/answer`, {
      answer: body.answer ?? "",
      confirmed: body.confirmed ?? false,
    }),

  /**
   * PATCH /api/suggestions/{id} {"action":"edit", "edited_text": "..."}
   *
   * Edit stores the user's rewrite and applies it, so it is an accept with
   * different text rather than a separate lifecycle branch.
   */
  edit: (suggestionId: string, editedText: string) =>
    http.patch<SuggestionOut>(`/suggestions/${suggestionId}`, {
      action: "edit",
      edited_text: editedText,
    }),

  /**
   * POST /api/resumes/{id}/sections/{target_ref}/rewrite
   *
   * Drafts an AI fix for one finding and returns an ordinary pending
   * SuggestionOut, so everything above applies to it unchanged. The server
   * runs the critic before persisting, so a 422 here means the draft was
   * rejected as ungrounded and nothing was stored.
   *
   * target_ref contains dots but never slashes, so it needs no encoding
   * beyond the usual.
   */
  rewrite: (
    resumeId: string,
    targetRef: string,
    opts: { findingId?: string; regenerate?: boolean } = {},
  ) =>
    http.post<SuggestionOut>(
      `/resumes/${resumeId}/sections/${encodeURIComponent(targetRef)}/rewrite`,
      { finding_id: opts.findingId ?? "", regenerate: opts.regenerate ?? false },
      // writer -> critic -> up to CRITIC_MAX_REVISIONS retries, each over the
      // model chain. The longest LLM path in the app.
      { timeoutMs: LLM_REQUEST_TIMEOUT_MS },
    ),

  /**
   * POST /api/resumes/{id}/suggestions/accept-all
   *
   * Applies every applicable pending suggestion as ONE version, so one undo
   * puts the whole batch back. Stale suggestions and unanswered questions
   * are skipped and named in `skipped`, never forced.
   *
   * `sessionId` scopes it to one tailoring run; omit it in the Analysis
   * pane, where the rows carry `session_id: null`.
   */
  acceptAll: (resumeId: string, sessionId?: string | null) =>
    http.post<AcceptAllOut>(`/resumes/${resumeId}/suggestions/accept-all`, {
      session_id: sessionId ?? null,
    }),
};

/** The text actually applied: the user's edit when present, else the LLM's
 *  draft. Mirrors how the backend resolves it. */
export function effectiveText(suggestion: SuggestionOut): string {
  return suggestion.edited_text || suggestion.suggested_text;
}
