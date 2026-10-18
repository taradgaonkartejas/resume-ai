import { useState } from "react";
import {
  Check,
  Loader2,
  Pencil,
  PencilLine,
  ShieldCheck,
  TriangleAlert,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSuggestionAction } from "@/api/queries";
import { effectiveText, type SuggestionOut } from "@/services";
import { METRIC_PLACEHOLDER } from "@/lib/placeholder";
import { ClarifyingQuestionCard } from "./ClarifyingQuestionCard";
import {
  isAttested,
  isFromAnalysis,
  KIND_LABEL,
  suggestionKind,
} from "./suggestionKind";

/**
 * The Accept / Reject / Edit card, shared by the Tailor and Analysis panes.
 *
 * Extracted from TailorPane so both entry points drive ONE suggestion
 * lifecycle. Analysis-origin suggestions have `session_id = null`; the backend
 * `act()` is session-agnostic and `useSuggestionAction` already guards on it,
 * so `sessionId` is optional here rather than a second code path.
 *
 * Do not fork this component. A second Accept button is a second set of bugs.
 */
/** Sentinel the backend writes into critic_notes when the AI writer was
 *  unavailable and the deterministic fallback produced the text. Mirrors
 *  SuggestionService.rewrite_section. */
const NO_AI = "written without AI";

/** Regex-escape: keywords legitimately contain "+" and "." — C++, Node.js. */
function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Wrap each keyword occurrence in a mark, longest first so "Node.js" wins
 *  over "Node". */
export function Highlighted({ text, keywords }: { text: string; keywords: string[] }) {
  const terms = keywords.filter(Boolean).sort((a, b) => b.length - a.length);
  if (terms.length === 0) return <>{text}</>;

  const pattern = new RegExp(`(${terms.map(escapeRegex).join("|")})`, "gi");
  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, i) =>
        terms.some((t) => t.toLowerCase() === part.toLowerCase()) ? (
          <mark key={i} className="rounded bg-mark px-0.5 text-paper-ink">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

/**
 * The resume-score effect of a suggestion, in Analysis-tab currency.
 *
 * Three cases, all stated plainly rather than hidden:
 *   > 0   gains points
 *   = 0   score-neutral, which most keyword work is
 *   < 0   should be unreachable; see the comment at the call site
 */
function ScoreDelta({ delta }: { delta: number }) {
  if (delta === 0) {
    return (
      <span className="ml-auto text-[10px] text-content-muted">
        no score change
      </span>
    );
  }
  const gains = delta > 0;
  return (
    <span
      className={
        "ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold tabular-nums " +
        (gains
          ? "bg-brand/15 text-brand-text"
          : "bg-reject-text/15 text-reject-text")
      }
      title={
        gains
          ? `Accepting this raises your resume score by ${delta}`
          : `This would LOWER your resume score by ${-delta}. ` +
            "That should not be possible — please report it."
      }
    >
      {gains ? `+${delta} pts` : `${delta} pts`}
    </span>
  );
}

export function SuggestionCard({
  suggestion,
  resumeId,
  sessionId = null,
  readOnly = false,
  onResolved,
}: {
  suggestion: SuggestionOut;
  resumeId: string;
  sessionId?: string | null;
  readOnly?: boolean;
  /** Called after accept/reject/edit succeeds, so a host card can reset. */
  /**
   * Fired after a successful action, told WHICH one.
   *
   * Accept and reject used to be indistinguishable here, so a caller could
   * not react differently to "the user took this" and "the user threw it
   * away". A no-arg callback stays assignable, so existing callers are
   * unaffected.
   */
  onResolved?: (action: "accept" | "reject" | "edit") => void;
}) {
  const act = useSuggestionAction(resumeId, sessionId ?? null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(effectiveText(suggestion));

  const kind = suggestionKind(suggestion);
  const fromAnalysis = isFromAnalysis(suggestion);
  const attested = isAttested(suggestion);

  // A question is a different card, not a variant of this one: it has no
  // draft to diff, no Accept, and its own two-button grammar. Branching
  // inside the body instead would mean every line below carries an "unless
  // it is a question" caveat.
  if (suggestion.status === "needs_input") {
    return (
      <ClarifyingQuestionCard
        suggestion={suggestion}
        resumeId={resumeId}
        sessionId={sessionId}
        readOnly={readOnly}
        onResolved={(outcome) =>
          onResolved?.(outcome === "attested" ? "edit" : "reject")
        }
      />
    );
  }

  return (
    <div
      className={
        "rounded-xl border bg-surface-2 p-3 " +
        (suggestion.is_stale
          ? "border-dashed border-line opacity-60"
          : "border-line-soft")
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        {/* Type pill. Not the reference's MODIFICATION / CONTEXTUAL MATCH
            pair verbatim -- CONTEXTUAL MATCH names its clarifying-question
            card, which does not exist here yet, and a pill for an
            unreachable state is worse than none. See suggestionKind.ts. */}
        <span
          className={
            "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide " +
            (kind === "keyword_match"
              ? "bg-brand/15 text-brand-text"
              : "bg-surface-3 text-content-muted")
          }
        >
          {KIND_LABEL[kind]}
        </span>
        {fromAnalysis && (
          <span
            className="rounded-full bg-surface-3 px-2 py-0.5 text-[10px] font-medium
                       uppercase tracking-wide text-content-muted"
            title="Raised by resume analysis, not by the job description"
          >
            From analysis
          </span>
        )}
        {/* An answered question keeps its provenance. This is the only card
            in the app whose text is NOT traceable to the resume, and that is
            worth one word on screen rather than a row in a table. */}
        {attested && (
          <span
            className="rounded-full bg-surface-3 px-2 py-0.5 text-[10px] font-medium
                       uppercase tracking-wide text-content-muted"
            title={
              suggestion.answer
                ? `You told us: ${suggestion.answer}`
                : "You confirmed this when asked"
            }
          >
            You confirmed
          </span>
        )}
        {/* What this does to the RESUME score -- the same number, from the
            same rule engine, that the Analysis tab prints on its cards.
            Deterministic and free: `introduced_findings` was already
            rescoring the patched resume during review and discarding it.

            0 is the common case and is shown, not hidden: most keyword
            swaps are score-neutral and saying so beats implying impact.
            A negative should be impossible -- the review gate rejects any
            draft that introduces a finding -- so it is rendered loudly
            rather than clamped, because it would mean that gate has a hole. */}
        <ScoreDelta delta={suggestion.score_delta} />
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {suggestion.keywords.map((k) => (
          <span
            key={k}
            className="rounded-full bg-brand/15 px-2 py-0.5 text-[10px] font-medium text-brand-text"
          >
            {k}
          </span>
        ))}
        {suggestion.grounded && (
          <span
            className="ml-auto flex items-center gap-1 text-[10px] text-accept-text"
            title="The critic agent confirmed this is grounded in your real experience"
          >
            <ShieldCheck className="size-3" aria-hidden />
            grounded
          </span>
        )}
      </div>

      {suggestion.placement && (
        <p className="mt-2 text-[11px] text-content-muted">
          <span className="font-semibold uppercase tracking-wide">Placement: </span>
          {suggestion.placement}
        </p>
      )}

      {suggestion.original_text && (
        <div className="mt-2">
          <p className="text-[10px] font-medium uppercase tracking-wide text-content-muted">
            Original
          </p>
          <p className="mt-0.5 rounded-lg bg-surface-3/60 px-2.5 py-2 text-xs leading-relaxed text-content-muted">
            {suggestion.original_text}
          </p>
        </div>
      )}

      <div className="mt-2">
        <p className="text-[10px] font-medium uppercase tracking-wide text-content-muted">
          {suggestion.original_text ? "Modified" : "Suggested"}
        </p>
        {editing ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            autoFocus
            className="mt-1 w-full resize-y rounded-lg border border-brand bg-surface-1 px-2 py-1.5 text-xs leading-relaxed outline-none"
            aria-label="Edit suggestion"
          />
        ) : (
          <p className="mt-0.5 rounded-lg border border-brand/30 bg-brand/10 px-2.5 py-2 text-xs leading-relaxed text-content">
            <Highlighted
              text={effectiveText(suggestion)}
              keywords={suggestion.keywords}
            />
          </p>
        )}
      </div>

      {/* The blank is the user's to fill, and nothing else says so.
          Export refuses while it is still there, but that message arrives
          only when they try to download -- by which point they have read
          the line several times and stopped seeing the brackets. */}
      {!editing && effectiveText(suggestion).includes(METRIC_PLACEHOLDER) && (
        <p className="mt-1.5 flex items-start gap-1.5 rounded-lg bg-mark/20 px-2.5 py-1.5 text-[11px] leading-relaxed text-content">
          <PencilLine className="mt-0.5 size-3 shrink-0 text-brand-text" aria-hidden />
          <span>
            Replace <code className="rounded bg-mark px-1 text-on-mark">{METRIC_PLACEHOLDER}</code>{" "}
            with your real figure. The AI left the blank on purpose — it will
            not invent a number, and your resume cannot be exported until you
            fill it in.
          </span>
        </p>
      )}

      {suggestion.reasoning && !editing && (
        <p className="mt-2 border-l-2 border-line-soft pl-2 text-[11px] italic text-content-muted">
          {suggestion.reasoning}
        </p>
      )}

      {suggestion.critic_notes === NO_AI && !editing && (
        <p className="mt-1.5 flex items-center gap-1 text-[11px] text-warning">
          <TriangleAlert className="size-3 shrink-0" aria-hidden />
          Written without AI — the provider was unavailable. Review it closely.
        </p>
      )}

      {suggestion.critic_notes && suggestion.critic_notes !== NO_AI && !editing && (
        <p className="mt-1.5 text-[11px] text-content-muted">
          Critic: {suggestion.critic_notes}
        </p>
      )}

      {act.isError && (
        <p role="alert" className="mt-2 text-[11px] text-reject-text">
          {(act.error as Error)?.message ?? "Could not update this suggestion."}
        </p>
      )}

      {/* Say it before the click, not after.
          `act()` returns 409 for this case, but only once the user has read
          the card, compared the bullets and committed to a decision. That is
          the entire bug: the suggestion really is obsolete, we just used to
          find out last. */}
      {suggestion.is_stale && (
        <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-surface-3/60 px-2.5 py-2 text-[11px] text-content-muted">
          <TriangleAlert className="mt-px size-3 shrink-0" aria-hidden />
          <span>
            You have edited this line since the suggestion was written.
            Accepting it would overwrite that newer text, so re-run tailoring
            to get one for the current version.
          </span>
        </p>
      )}

      {!readOnly && (
        <div className="mt-3 flex items-center gap-2">
          {editing ? (
            <>
              <Button
                shape="pill"
                className="h-8 flex-1 px-3.5 text-xs"
                disabled={act.isPending || !draft.trim()}
                onClick={() =>
                  act.mutate(
                    { id: suggestion.id, action: "edit", editedText: draft.trim() },
                    {
                      onSuccess: () => {
                        setEditing(false);
                        onResolved?.("edit");
                      },
                    },
                  )
                }
              >
                {act.isPending && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
                Save and apply
              </Button>
              <Button
                variant="outline"
                shape="pill"
                className="h-8 px-3.5 text-xs"
                onClick={() => {
                  setDraft(effectiveText(suggestion));
                  setEditing(false);
                }}
                disabled={act.isPending}
              >
                Cancel
              </Button>
            </>
          ) : (
            <>
              {/* Accept is filled, Reject is a ghost with a border: the
                  destructive action must not compete visually. */}
              <Button
                shape="pill"
                className="h-8 flex-1 bg-accept px-3.5 text-xs text-on-accept hover:bg-accept/90"
                // Reject stays enabled: clearing a stale card is exactly what
                // the user should be able to do.
                disabled={act.isPending || suggestion.is_stale}
                title={
                  suggestion.is_stale
                    ? "This line changed after the suggestion was written"
                    : undefined
                }
                onClick={() =>
                  act.mutate(
                    { id: suggestion.id, action: "accept" },
                    { onSuccess: () => onResolved?.("accept") },
                  )
                }
              >
                {act.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : (
                  <Check className="size-3.5" aria-hidden />
                )}
                Accept
              </Button>
              <Button
                variant="outline"
                shape="pill"
                className="h-8 border-reject-text/50 px-3.5 text-xs text-reject-text hover:bg-reject-text/10"
                disabled={act.isPending}
                onClick={() =>
                  act.mutate(
                    { id: suggestion.id, action: "reject" },
                    { onSuccess: () => onResolved?.("reject") },
                  )
                }
              >
                <X className="size-3.5" aria-hidden />
                Reject
              </Button>
              <Button
                variant="outline"
                size="icon"
                shape="pill"
                disabled={act.isPending}
                onClick={() => setEditing(true)}
                aria-label="Edit suggestion"
              >
                <Pencil className="size-3.5" aria-hidden />
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
