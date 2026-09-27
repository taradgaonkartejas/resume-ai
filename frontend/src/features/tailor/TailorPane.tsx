import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSuggestions, useTailorSessions } from "@/api/queries";
import { keywordProgress } from "@/services";
import { SuggestionCard } from "@/features/suggestions/SuggestionCard";
import { cn } from "cn";

type Bucket = "active" | "matched" | "rejected";

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
