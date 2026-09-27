import { useState } from "react";
import { ArrowRight, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useRewriteSection } from "@/api/queries";
import { SuggestionCard } from "@/features/suggestions/SuggestionCard";
import type { Finding, SuggestionOut } from "@/services";
import { cn } from "cn";

const SEVERITY: Record<Finding["severity"], { dot: string; label: string }> = {
  high: { dot: "bg-reject-text", label: "High impact" },
  medium: { dot: "bg-warning", label: "Medium impact" },
  low: { dot: "bg-brand", label: "Low impact" },
};

/**
 * One recommendation, and the whole "Fix this" flow for it.
 *
 * The "+N points" badge is the load-bearing element of this feature: it is the
 * literal delta the score will move when the finding is resolved, and backend
 * tests (tests/test_findings.py) assert that fixing it changes overall_score by
 * exactly N. If those ever diverge, remove the badge rather than soften it — a
 * number the user can check and catch being wrong costs more trust than
 * showing nothing.
 *
 * Two different buttons, chosen by `finding.action`, because exactly half of
 * all findings cannot be fixed by a model:
 *
 *   action === "rewrite"  ->  "Rewrite with AI"   drafts a suggestion
 *   action === ""         ->  "Go to field"       scrolls and focuses
 *
 * contact.phone.missing cannot be written by an LLM — it does not know your
 * phone number. One button that sometimes calls a model and sometimes scrolls
 * is a button that means two things.
 */
export function RecommendationCard({
  finding,
  resumeId,
  onFix,
}: {
  finding: Finding;
  resumeId?: string | null;
  /** Scrolls to and focuses the field named by target_ref. */
  onFix?: (finding: Finding) => void;
}) {
  const severity = SEVERITY[finding.severity];
  const canRewrite = finding.action === "rewrite" && Boolean(resumeId);

  const rewrite = useRewriteSection(resumeId ?? null);
  const [draft, setDraft] = useState<SuggestionOut | null>(null);

  // A rejected or accepted draft returns the card to idle. Accept also makes
  // the finding disappear on the next steps refetch, so this is mostly for
  // Reject.
  const onResolved = () => setDraft(null);

  const start = (regenerate = false) =>
    rewrite.mutate(
      { targetRef: finding.target_ref, findingId: finding.id, regenerate },
      { onSuccess: setDraft },
    );

  return (
    <li className="rounded-xl border border-line-soft bg-surface-2 p-3">
      <div className="flex items-start gap-2.5">
        <span
          className={cn("mt-1.5 size-2 shrink-0 rounded-full", severity.dot)}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-content">{finding.message}</p>
          <p className="mt-1 text-xs leading-relaxed text-content-muted">
            {finding.fix_hint}
          </p>

          {!draft && !rewrite.isPending && (
            <div className="mt-2 flex flex-wrap items-center gap-3">
              {canRewrite && (
                <button
                  type="button"
                  onClick={() => start()}
                  className="inline-flex items-center gap-1 rounded-md text-xs font-medium
                             text-brand-text hover:underline
                             focus-visible:outline-2 focus-visible:outline-brand"
                >
                  <Sparkles className="size-3" aria-hidden />
                  Rewrite with AI
                </button>
              )}
              {onFix && (
                <button
                  type="button"
                  onClick={() => onFix(finding)}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-md text-xs font-medium",
                    "focus-visible:outline-2 focus-visible:outline-brand hover:underline",
                    canRewrite ? "text-content-muted" : "text-brand-text",
                  )}
                >
                  {canRewrite ? "Edit it myself" : "Go to field"}
                  <ArrowRight className="size-3" aria-hidden />
                </button>
              )}
            </div>
          )}

          {/* generating: 8.9s average, so never a frozen card. */}
          {rewrite.isPending && (
            <div className="mt-2.5" aria-live="polite">
              <p className="flex items-center gap-1.5 text-xs text-content-muted">
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                Writing a revision…
              </p>
              <div className="mt-2 space-y-1.5" aria-hidden>
                <div className="h-2.5 w-full animate-pulse rounded bg-surface-3" />
                <div className="h-2.5 w-4/5 animate-pulse rounded bg-surface-3" />
              </div>
            </div>
          )}

          {rewrite.isError && !rewrite.isPending && (
            <div className="mt-2.5" role="alert">
              <p className="text-xs leading-relaxed text-reject-text">
                {(rewrite.error as Error)?.message ??
                  "Could not draft a revision for this."}
              </p>
              <div className="mt-1.5 flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => start()}
                  className="inline-flex items-center gap-1 text-xs font-medium
                             text-brand-text hover:underline"
                >
                  <RefreshCw className="size-3" aria-hidden />
                  Try again
                </button>
                {onFix && (
                  <button
                    type="button"
                    onClick={() => onFix(finding)}
                    className="text-xs font-medium text-content-muted hover:underline"
                  >
                    Edit it myself
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        <Badge
          variant="secondary"
          className="shrink-0 bg-brand/15 font-semibold tabular-nums text-brand-text"
          title={`${severity.label} — resolving this raises your score by ${finding.points}`}
        >
          +{finding.points}
        </Badge>
      </div>

      {/* The revision. Same component the Tailor pane uses, so Accept /
          Reject / Edit behave identically in both places. */}
      {draft && (
        <div className="mt-3">
          <SuggestionCard
            suggestion={draft}
            resumeId={resumeId ?? ""}
            onResolved={onResolved}
          />
          <div className="mt-1.5 flex justify-end">
            <Button
              variant="ghost"
              shape="pill"
              className="h-7 px-3 text-[11px] text-content-muted"
              disabled={rewrite.isPending}
              onClick={() => start(true)}
            >
              <RefreshCw className="size-3" aria-hidden />
              Regenerate
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}
