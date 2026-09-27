import { useState } from "react";
import { Check, Loader2, Pencil, ShieldCheck, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSuggestionAction, useSuggestions, useTailorSessions } from "@/api/queries";
import { effectiveText, keywordProgress, type SuggestionOut } from "@/services";
import { cn } from "cn";

type Bucket = "active" | "matched" | "rejected";

/** Regex-escape: keywords legitimately contain "+" and "." — C++, Node.js. */
function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Wrap each keyword occurrence in a mark, longest first so "Node.js" wins
 *  over "Node". */
function Highlighted({ text, keywords }: { text: string; keywords: string[] }) {
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

function SuggestionCard({
  suggestion,
  resumeId,
  sessionId,
  readOnly,
}: {
  suggestion: SuggestionOut;
  resumeId: string;
  sessionId: string;
  readOnly: boolean;
}) {
  const act = useSuggestionAction(resumeId, sessionId);
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

      {suggestion.critic_notes && !editing && (
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
                className="h-8 flex-1 text-xs"
                disabled={act.isPending || !draft.trim()}
                onClick={() =>
                  act.mutate(
                    { id: suggestion.id, action: "edit", editedText: draft.trim() },
                    { onSuccess: () => setEditing(false) },
                  )
                }
              >
                {act.isPending && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
                Save and apply
              </Button>
              <Button
                variant="outline"
                className="h-8 text-xs"
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
                className="h-8 flex-1 bg-accept text-xs text-on-accept hover:bg-accept/90"
                disabled={act.isPending}
                onClick={() => act.mutate({ id: suggestion.id, action: "accept" })}
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
                className="h-8 border-reject-text/50 text-xs text-reject-text hover:bg-reject-text/10"
                disabled={act.isPending}
                onClick={() => act.mutate({ id: suggestion.id, action: "reject" })}
              >
                <X className="size-3.5" aria-hidden />
                Reject
              </Button>
              <Button
                variant="outline"
                className="h-8 text-xs"
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

/**
 * Tailor Resume tab.
 *
 * Shows the newest session for this resume. A tailored resume always has one,
 * because tailoring is what created it.
 */
export function TailorPane({
  resumeId,
  onStartTailoring,
}: {
  resumeId: string | null;
  onStartTailoring: () => void;
}) {
  const { data: sessions, isLoading } = useTailorSessions(resumeId);
  const session = sessions?.[0] ?? null;
  const { data: buckets, isLoading: loadingSuggestions } = useSuggestions(
    resumeId,
    session?.id ?? null,
  );
  const [tab, setTab] = useState<Bucket>("active");

  if (isLoading) {
    return (
      <div className="grid h-40 place-items-center">
        <Loader2 className="size-5 animate-spin text-brand-text" aria-hidden />
      </div>
    );
  }

  if (!session) {
    return (
      <div className="rounded-xl border border-dashed border-line px-4 py-8 text-center">
        <p className="text-sm font-medium">Not tailored to a job yet</p>
        <p className="mx-auto mt-1 max-w-xs text-xs text-content-muted">
          Paste a job description and the agents will rewrite your bullets around
          its keywords — grounded in experience you actually have.
        </p>
        <Button className="mt-4" onClick={onStartTailoring} disabled={!resumeId}>
          <Sparkles className="size-4" aria-hidden />
          Tailor to a job
        </Button>
      </div>
    );
  }

  const progress = keywordProgress(session);
  const counts = {
    active: buckets?.active.length ?? 0,
    matched: buckets?.matched.length ?? 0,
    rejected: buckets?.rejected.length ?? 0,
  };
  const list = buckets?.[tab] ?? [];

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-line-soft bg-surface-2 p-4">
        <div className="flex items-baseline justify-between">
          <p className="text-sm font-semibold">Keyword match</p>
          <p className="text-lg font-bold tabular-nums text-brand-text">
            {Math.round(session.match_percent)}%
          </p>
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-3">
          <div
            className="h-full rounded-full bg-brand transition-[width] duration-500"
            style={{ width: `${Math.min(100, session.match_percent)}%` }}
          />
        </div>
        <p className="mt-1.5 text-xs text-content-muted">
          {progress.matched} of {progress.total} keywords integrated
          {session.baseline_percent > 0 &&
            ` · started at ${Math.round(session.baseline_percent)}%`}
        </p>
      </div>

      <div className="flex items-center gap-1 border-b border-line-soft">
        {(["active", "matched", "rejected"] as const).map((b) => (
          <button
            key={b}
            onClick={() => setTab(b)}
            className={cn(
              "-mb-px flex items-center gap-1.5 border-b-2 px-2.5 py-1.5 text-xs capitalize",
              tab === b
                ? "border-brand font-semibold text-content"
                : "border-transparent text-content-muted hover:text-content",
            )}
          >
            {b === "matched" ? "Already matched" : b}
            <span className="rounded bg-surface-3 px-1 text-[10px]">{counts[b]}</span>
          </button>
        ))}
      </div>

      {loadingSuggestions ? (
        <div className="grid h-24 place-items-center">
          <Loader2 className="size-5 animate-spin text-brand-text" aria-hidden />
        </div>
      ) : list.length === 0 ? (
        <p className="px-1 py-6 text-center text-xs text-content-muted">
          {tab === "active"
            ? "No pending suggestions. Every one has been accepted or rejected."
            : `Nothing in ${tab}.`}
        </p>
      ) : (
        <div className="space-y-3">
          {list.map((s) => (
            <SuggestionCard
              key={s.id}
              suggestion={s}
              resumeId={resumeId!}
              sessionId={session.id}
              readOnly={tab !== "active"}
            />
          ))}
        </div>
      )}
    </div>
  );
}
