import { http } from "@/api/client";
import type { SuggestionOut } from "./types";

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
};

/** The text actually applied: the user's edit when present, else the LLM's
 *  draft. Mirrors how the backend resolves it. */
export function effectiveText(suggestion: SuggestionOut): string {
  return suggestion.edited_text || suggestion.suggested_text;
}
