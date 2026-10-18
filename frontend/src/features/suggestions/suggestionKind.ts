import type { SuggestionOut } from "@/services";

/**
 * What *kind* of suggestion a card is showing.
 *
 * The reference prints a type pill on every card -- `MODIFICATION` versus
 * `CONTEXTUAL MATCH`. We cannot copy that pair literally: `CONTEXTUAL MATCH`
 * labels the reference's clarifying-question card, which is a mechanism we
 * have not built yet. A pill naming a state that cannot occur is worse than
 * no pill.
 *
 * The third case has now landed. `clarifying` is checked FIRST because it is
 * a status, not a shape: a question card carries keywords too (the skill it
 * asks about), so testing `keywords.length` first would mislabel every
 * question as a keyword match.
 *
 * Two fields were considered and rejected as discriminators:
 *
 *  - **`grounded`** is set to `true` at every call site that creates a
 *    Suggestion (tailoring, chat, rewrite). It is vestigial, so a pill
 *    derived from it would read the same on every card forever.
 *  - **`status`** is lifecycle, not type; the buckets already show it.
 */
export type SuggestionKind = "clarifying" | "keyword_match" | "rewrite";

export function suggestionKind(s: SuggestionOut): SuggestionKind {
  // Status first -- see the note above. An answered question keeps
  // `attested`, so it stays identifiable after it becomes pending.
  if (s.status === "needs_input") return "clarifying";
  // Keywords are attached by the tailoring graph only when the rewrite
  // actually weaves JD vocabulary in, which is exactly the distinction a
  // user scanning the Tailor tab cares about: "is this making me match the
  // job, or just read better?"
  return s.keywords.length > 0 ? "keyword_match" : "rewrite";
}

export const KIND_LABEL: Record<SuggestionKind, string> = {
  clarifying: "Needs your answer",
  keyword_match: "Keyword match",
  rewrite: "Rewrite",
};

/**
 * A card resting on something the user told us rather than on resume text.
 *
 * Shown on the card after the question is answered, because the provenance
 * is the single most important thing about these: everything else in the
 * Tailor tab is traceable to text the resume already contained.
 */
export function isAttested(s: SuggestionOut): boolean {
  return s.attested;
}

/**
 * Tailor shows suggestions that came from the Analysis side too. Without a
 * marker, a card with no keywords on it looks like the tailoring run simply
 * did a poor job, when in fact it was never about the job description.
 */
export function isFromAnalysis(s: SuggestionOut): boolean {
  return s.origin === "analysis";
}
