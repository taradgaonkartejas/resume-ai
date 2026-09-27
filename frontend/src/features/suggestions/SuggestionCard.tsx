import { useState } from "react";
import { Check, Loader2, Pencil, ShieldCheck, TriangleAlert, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSuggestionAction } from "@/api/queries";
import { effectiveText, type SuggestionOut } from "@/services";

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
  onResolved?: () => void;
}) {
  const act = useSuggestionAction(resumeId, sessionId ?? null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(effectiveText(suggestion));

  return (
    <div className="rounded-xl border border-line-soft bg-surface-2 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
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
        <p className="mt-2 text-[11px] text-content-muted">{suggestion.placement}</p>
      )}

      {suggestion.original_text && (
        <div className="mt-2">
          <p className="text-[10px] font-medium uppercase tracking-wide text-content-muted">
            Original
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-content-muted line-through decoration-content-muted/40">
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
          <p className="mt-0.5 text-xs leading-relaxed text-content">
            <Highlighted
              text={effectiveText(suggestion)}
              keywords={suggestion.keywords}
            />
          </p>
        )}
      </div>

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
                        onResolved?.();
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
                disabled={act.isPending}
                onClick={() =>
                  act.mutate(
                    { id: suggestion.id, action: "accept" },
                    { onSuccess: () => onResolved?.() },
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
                    { onSuccess: () => onResolved?.() },
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
