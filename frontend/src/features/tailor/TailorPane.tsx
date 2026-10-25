import { useState } from "react";
import {
  CheckCheck,
  FileText,
  Loader2,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useAcceptAll,
  useAnalysisSteps,
  useResume,
  useSuggestions,
  useTailorSessions,
} from "@/api/queries";
import { ScoreRing } from "@/features/analysis/ScoreRing";
import { keywordProgress } from "@/services";
import { SuggestionCard } from "@/features/suggestions/SuggestionCard";
import { groupSuggestions } from "./groupSuggestions";
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
  // The RESUME score, on the tailoring screen.
  //
  // Keyword match answers "does this look like the job ad". It cannot answer
  // "is my resume getting better or worse", and until now that was the only
  // number here -- a user could drive keyword match to 100% with no idea what
  // it did to their score. Same endpoint the Analysis tab uses, recomputed
  // live, so accepting a suggestion moves both numbers at once.
  const { data: steps } = useAnalysisSteps(resumeId);
  // Tailoring edits THIS resume in place, so the user is owed its name:
  // accepted suggestions change the same document the Analysis tab scores
  // and the export writes out, and that is not guessable from a list of
  // bullet rewrites.
  const { data: resume } = useResume(resumeId);
  const session = sessions?.[0] ?? null;
  // Declared with the other hooks: everything below the empty state is
  // behind an early return, and a hook called there runs in a different
  // order on the render where no session exists.
  const acceptAll = useAcceptAll(resumeId, session?.id ?? null);
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
  // Stale cards are unacceptable-by-design, so a user staring at six
  // suggestions needs to know how many are actually actionable without
  // clicking each one and collecting 409s.
  const staleCount = (buckets?.active ?? []).filter((x) => x.is_stale).length;

  const groups = groupSuggestions(list);

  return (
    <div className="space-y-4">
      {/* Which document is being changed. Tailoring is in place, so an
          accepted suggestion rewrites this resume -- the same one the
          Analysis tab scores. Saying so is cheaper than letting the user
          discover it. */}
      {resume && (
        <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 rounded-lg bg-surface-2 px-3 py-2 text-[11px] text-content-muted">
          <FileText className="size-3 shrink-0" aria-hidden />
          <span>
            Editing <span className="font-medium text-content">{resume.title}</span>
          </span>
          <span aria-hidden>·</span>
          <span>accepted changes appear in Analysis and in your export</span>
        </p>
      )}

      <div className="rounded-xl border border-line-soft bg-surface-2 p-4">
        <div className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
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
              {progress.remaining > 0 && ` · ${progress.remaining} to go`}
              {session.baseline_percent > 0 &&
                ` · started at ${Math.round(session.baseline_percent)}%`}
            </p>
          </div>

          {/* The two numbers side by side. This is the sync: tailoring and
              analysis stop being separate scores the user has to reconcile. */}
          {steps && (
            <div className="shrink-0 text-center">
              <ScoreRing score={steps.overall_score} size={56} />
              <p className="mt-1 text-[10px] font-medium text-content-muted">
                Resume score
              </p>
            </div>
          )}
        </div>

        {staleCount > 0 && (
          <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-surface-3/60 px-2.5 py-2 text-[11px] text-content-muted">
            <TriangleAlert className="mt-px size-3 shrink-0" aria-hidden />
            <span>
              {staleCount === 1
                ? "1 suggestion is out of date"
                : `${staleCount} suggestions are out of date`}{" "}
              because you edited those lines after tailoring ran. Re-run
              tailoring to refresh them.
            </span>
          </p>
        )}

        <p className="mt-3 border-t border-line-soft pt-2 text-[11px] text-content-muted">
          Keyword match is section-weighted like an ATS reads it:
          experience&nbsp;45%&nbsp;· skills&nbsp;30%&nbsp;· projects&nbsp;15%&nbsp;·
          summary&nbsp;&amp;&nbsp;education&nbsp;10% — the same keyword counts
          more when it is proven inside a job than merely listed in your
          skills section. Job title, education and keywords that do not
          apply to you are not changed by tailoring, so they do not count
          against your progress.
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

      {/* Accept All, on the Active tab only. Accepting from "Already
          matched" or "Rejected" would mean re-applying decisions the user
          already made. `applicable` excludes the stale ones because the
          server will skip them anyway -- promising six and applying four
          reads as a bug even when the four are right. */}
      {tab === "active" && counts.active - staleCount > 0 && (
        <div className="flex items-center gap-2 rounded-lg border border-line-soft bg-surface-2 px-2.5 py-1.5">
          <span className="text-[11px] text-content-muted">
            {counts.active - staleCount} ready to apply
            {staleCount > 0 && ` · ${staleCount} stale, skipped`}
          </span>
          <button
            type="button"
            disabled={acceptAll.isPending}
            onClick={() => {
              const n = counts.active - staleCount;
              if (
                window.confirm(
                  `Apply ${n} suggestion${n === 1 ? "" : "s"} to your resume? ` +
                    "This counts as one change, so a single undo reverts all of them.",
                )
              ) {
                acceptAll.mutate();
              }
            }}
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-brand px-2 py-1
                       text-[11px] font-medium text-content-accent transition-opacity
                       hover:opacity-90 disabled:opacity-60"
          >
            {acceptAll.isPending ? (
              <Loader2 className="size-3 animate-spin" aria-hidden />
            ) : (
              <CheckCheck className="size-3" aria-hidden />
            )}
            Accept all
          </button>
        </div>
      )}

      {acceptAll.data && acceptAll.data.skipped_count > 0 && (
        <p className="rounded-lg bg-surface-3/60 px-2.5 py-1.5 text-[11px] text-content-muted">
          Applied {acceptAll.data.applied_count}; skipped{" "}
          {acceptAll.data.skipped_count} that changed since they were written.
        </p>
      )}

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
        <div className="space-y-5">
          {groups.map((g) => (
            <section key={g.key} className="space-y-2.5">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-content-muted">
                  {g.title}
                </h3>
                <span className="rounded bg-surface-3 px-1 text-[10px] text-content-muted">
                  {g.suggestions.length}
                </span>
                {/* The keywords this section would add. The reason to read
                    the group at all, so it belongs in the heading rather
                    than only inside each card. */}
                {g.keywords.length > 0 && (
                  <span className="min-w-0 flex-1 truncate text-[11px] text-content-muted">
                    adds {g.keywords.join(", ")}
                  </span>
                )}
              </div>
              <div className="space-y-3">
                {g.suggestions.map((s) => (
                  <SuggestionCard
                    key={s.id}
                    suggestion={s}
                    resumeId={resumeId!}
                    sessionId={session.id}
                    readOnly={tab !== "active"}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
