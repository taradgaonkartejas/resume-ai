import { useState } from "react";
import { ChevronDown, History, Loader2, RefreshCw, Sparkles, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScoreRing } from "@/features/analysis/ScoreRing";
import { AnalysisStepper } from "@/features/analysis/AnalysisStepper";
import { AtsReviewCard } from "@/features/analysis/AtsReviewCard";
import { band } from "@/lib/score";
import { useAnalysis, useRunAnalysis } from "@/api/queries";
import { ApiError } from "@/api/client";
import { pointsTo80, totalFindings, type CategoryKey } from "@/services";
import { cn } from "cn";

const CATEGORY_LABELS: Record<CategoryKey, string> = {
  contact: "Contact details",
  summary: "Summary & education",
  skills: "Skills & keywords",
  experience: "Work experience",
  projects: "Projects",
  format: "Formatting & ATS",
};

/**
 * Resume Analysis tab.
 *
 * The score is deterministic — heuristics.py sets the number with fixed
 * weights (summary+education 10, skills 30, experience 45, projects 15) and
 * the LLM only writes the explanation. Contact and format are advisory
 * passes (knockout filter / parser blocker): they emit findings but no
 * points, so their bars show issue counts, not a filled score. Re-running on
 * unchanged content is a no-op, and the button says "Re-run" rather than
 * pretending to be a fresh opinion.
 */
export function AnalysisPane({ resumeId }: { resumeId: string | null }) {
  const { data: analysis, isLoading, error } = useAnalysis(resumeId);
  const run = useRunAnalysis(resumeId);
  const [open, setOpen] = useState<CategoryKey | null>("experience");
  // The guided editor is a mode of this pane rather than a route, so the
  // preview stays mounted beside it and updates as the user types.
  const [guided, setGuided] = useState(false);

  const neverRun = error instanceof ApiError && error.isNotFound;

  if (guided) {
    return <AnalysisStepper resumeId={resumeId} onExit={() => setGuided(false)} />;
  }

  if (isLoading) {
    return (
      <div className="grid h-40 place-items-center">
        <Loader2 className="size-5 animate-spin text-brand-text" aria-hidden />
      </div>
    );
  }

  if (!analysis || neverRun) {
    return (
      <div className="rounded-xl border border-dashed border-line px-4 py-8 text-center">
        <p className="text-sm font-medium">No analysis yet</p>
        <p className="mx-auto mt-1 max-w-xs text-xs text-content-muted">
          Score your resume against ATS rules across summary, skills,
          experience and projects — plus the contact and formatting gates.
        </p>
        <Button
          className="mt-4"
          onClick={() => run.mutate()}
          disabled={run.isPending || !resumeId}
        >
          {run.isPending ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <Sparkles className="size-4" aria-hidden />
          )}
          Run analysis
        </Button>
        {resumeId && (
          <p className="mt-3 text-xs text-content-muted">
            or{" "}
            <button
              onClick={() => setGuided(true)}
              className="font-medium text-brand-text hover:underline"
            >
              fix it step by step
            </button>{" "}
            — scoring runs as you edit.
          </p>
        )}
        {run.isError && (
          <p role="alert" className="mt-3 text-xs text-reject-text">
            {(run.error as Error)?.message ?? "Analysis failed."}
          </p>
        )}
      </div>
    );
  }

  const tone = band(analysis.overall_score);
  const gap = pointsTo80(analysis.overall_score);
  const findings = totalFindings(analysis);
  // Written by ScoringAgent, persisted since the graph was built, and until
  // now returned by no endpoint and rendered by nothing. Optional-chained
  // because an analysis stored before the field existed has no commentary.
  const headline = analysis.commentary?.headline?.trim() ?? "";

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 rounded-xl border border-line-soft bg-surface-2 p-4">
        <ScoreRing score={analysis.overall_score} size={68} />
        <div className="min-w-0">
          <p className="text-sm font-semibold">
            Resume Strength: <span className={tone.className}>{tone.label}</span>
          </p>
          <p className="mt-0.5 text-xs text-content-muted">
            {gap > 0
              ? `${gap} point${gap === 1 ? "" : "s"} to reach 80+`
              : "Above the 80-point bar"}
            {findings > 0 && ` · ${findings} recommended improvement${findings === 1 ? "" : "s"}`}
          </p>
          {headline && (
            <div className={cn("mt-2", analysis.commentary_stale && "opacity-70")}>
              {/* The score above is recomputed live; this prose is a snapshot
                  from the last run. They CAN disagree — "the summary leans on
                  filler" above a score that just climbed because the filler
                  was removed reads as a broken product — so the old half is
                  labelled rather than presented as current. Dimmed, not
                  hidden: the insight is still worth having. */}
              {analysis.commentary_stale && (
                <span
                  className="mb-1 inline-flex items-center gap-1 rounded-full bg-surface-3 px-2 py-0.5 text-[10px] font-medium text-content-muted"
                  title="Your resume has changed since this was written. Re-run the analysis to refresh it."
                >
                  <History className="size-3" aria-hidden />
                  From your last analysis
                </span>
              )}
              <p className="text-xs leading-relaxed text-content">{headline}</p>
            </div>
          )}
          {analysis.role_tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {analysis.role_tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full bg-surface-3 px-2 py-0.5 text-[10px] text-content-muted"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="ml-auto shrink-0 self-start rounded-md p-1.5 text-content-muted hover:text-content disabled:opacity-50"
          aria-label="Re-run analysis"
          title="Re-run analysis"
        >
          <RefreshCw className={cn("size-4", run.isPending && "animate-spin")} aria-hidden />
        </button>
      </div>

      <Button className="w-full" onClick={() => setGuided(true)}>
        <Wand2 className="size-4" aria-hidden />
        {findings > 0
          ? `Improve my resume · ${findings} recommendation${findings === 1 ? "" : "s"}`
          : "Open guided editor"}
      </Button>

      <div className="space-y-2">
        {(Object.keys(CATEGORY_LABELS) as CategoryKey[]).map((key) => {
          const cat = analysis.category_scores[key];
          if (!cat) return null;
          const expanded = open === key;
          const pct = cat.max > 0 ? Math.round((cat.score / cat.max) * 100) : 0;
          // Advisory passes (contact / format) are 0-point gates: the bar is
          // a pass/fail verdict, not a gradient, so a full-width bar reads
          // "all clear" and a warning bar reads "gate failed".
          const advisory = cat.max === 0;
          return (
            <div key={key} className="overflow-hidden rounded-lg border border-line-soft">
              <button
                onClick={() => setOpen(expanded ? null : key)}
                className="flex w-full items-center gap-3 bg-surface-2 px-3 py-2.5 text-left"
                aria-expanded={expanded}
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{CATEGORY_LABELS[key]}</p>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3">
                    <div
                      className={cn(
                        "h-full rounded-full transition-[width] duration-500",
                        advisory
                          ? cat.notes.length > 0
                            ? "bg-warning"
                            : "bg-accept-text"
                          : pct >= 80
                            ? "bg-accept-text"
                            : pct >= 50
                              ? "bg-brand"
                              : "bg-warning",
                      )}
                      style={{ width: advisory ? "100%" : `${pct}%` }}
                    />
                  </div>
                </div>
                <span className="shrink-0 text-xs tabular-nums text-content-muted">
                  {advisory
                    ? cat.notes.length === 0
                      ? "pass"
                      : `${cat.notes.length} failed`
                    : `${cat.score}/${cat.max}`}
                </span>
                {cat.notes.length > 0 && (
                  <span className="shrink-0 rounded-full bg-surface-3 px-1.5 text-[10px] text-content-muted">
                    {cat.notes.length}
                  </span>
                )}
                <ChevronDown
                  className={cn(
                    "size-4 shrink-0 text-content-muted transition-transform",
                    expanded && "rotate-180",
                  )}
                  aria-hidden
                />
              </button>
              {expanded && (
                <ul className="space-y-1.5 border-t border-line-soft bg-surface-1 px-3 py-2.5">
                  {cat.notes.length === 0 ? (
                    <li className="text-xs text-accept-text">
                      Nothing to fix in this category.
                    </li>
                  ) : (
                    cat.notes.map((note, i) => (
                      <li key={i} className="flex gap-2 text-xs text-content">
                        <span className="mt-1.5 size-1 shrink-0 rounded-full bg-warning" />
                        {note}
                      </li>
                    ))
                  )}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      <AtsReviewCard review={analysis.ats_review} />
    </div>
  );
}
