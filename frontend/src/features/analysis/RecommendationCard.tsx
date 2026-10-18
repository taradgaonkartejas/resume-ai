import { useState } from "react";
import {
  ArrowDownNarrowWide,
  ArrowRight,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/api/client";
import { useRerankSection, useRewriteSection } from "@/api/queries";
import { SuggestionCard } from "@/features/suggestions/SuggestionCard";
import type { Finding, SuggestionOut } from "@/services";
import { cn } from "cn";

/**
 * Severity, in the reference's words.
 *
 * `pill` is the user-facing label; `dot` keeps the colour coding. The backend
 * `_sev` mapping is deliberately untouched -- it derives severity from the
 * POINTS a finding is worth, which is what keeps the badge and the score
 * from ever disagreeing. Renaming it there would decouple the two.
 */
const SEVERITY: Record<
  Finding["severity"],
  { dot: string; label: string; pill: string; tone: string }
> = {
  high: {
    dot: "bg-reject-text", label: "High impact", pill: "crucial",
    tone: "border-reject-text/40 text-reject-text",
  },
  medium: {
    dot: "bg-warning", label: "Medium impact", pill: "important",
    tone: "border-warning/40 text-warning",
  },
  low: {
    dot: "bg-brand", label: "Low impact", pill: "optional",
    tone: "border-brand/40 text-brand-text",
  },
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
  onFixed,
  fixed = false,
  presetDraft,
}: {
  finding: Finding;
  resumeId?: string | null;
  /** Scrolls to and focuses the field named by target_ref. */
  onFix?: (finding: Finding) => void;
  /** Fired when an AI draft for this finding is ACCEPTED. */
  onFixed?: (finding: Finding) => void;
  /**
   * Render as already resolved.
   *
   * Our model deletes a finding the instant it is fixed -- the next
   * /analysis/steps call simply does not contain it -- so there is no
   * "fixed" state on the server to read. This is a session-only
   * acknowledgement held by the parent: a congratulation, not data, and
   * correctly gone on refresh.
   */
  fixed?: boolean;
  /**
   * A draft created for this finding by the Step-4 bulk action.
   *
   * `draft` is local state set by this card's own mutation, so a suggestion
   * drafted anywhere else would exist on the server and be invisible here --
   * the user would see an unchanged card and conclude the bulk action did
   * nothing. The local draft still wins, so anything the user starts after
   * the bulk run takes precedence.
   */
  presetDraft?: SuggestionOut | null;
}) {
  const severity = SEVERITY[finding.severity];
  const canRewrite = finding.action === "rewrite" && Boolean(resumeId);
  // The third branch. ats.not_reverse_chronological is the only finding whose
  // fix is a pure reordering -- no text changes, so there is nothing for a
  // model to write and nothing to review. One click, done on the server.
  const canRerank = finding.action === "rerank" && Boolean(resumeId);
  // The fourth branch. summary.no_metric and experience.unquantified_bullets
  // are cleared only by a number, and inventing one is the worst thing this
  // product could do -- so the writer makes ROOM for the figure and leaves a
  // [__] behind. Same endpoint as a rewrite; a different promise, hence a
  // different label.
  const canQuantify = finding.action === "quantify" && Boolean(resumeId);
  const canDraft = canRewrite || canQuantify;
  const rerank = useRerankSection(resumeId ?? null);

  const rewrite = useRewriteSection(resumeId ?? null);
  const [draft, setDraft] = useState<SuggestionOut | null>(null);
  const [dismissedPreset, setDismissedPreset] = useState(false);
  const shownDraft = draft ?? (dismissedPreset ? null : (presetDraft ?? null));

  // A rejected or accepted draft returns the card to idle. Accept also makes
  // the finding disappear on the next steps refetch, so the parent is told
  // about it -- that is the only moment anything knows this finding was
  // resolved by the AI rather than by hand, and a vanished card is a poor
  // confirmation that anything happened.
  const onResolved = (action: "accept" | "reject" | "edit") => {
    setDraft(null);
    // Without this a resolved *preset* draft springs straight back, because
    // it lives in the parent and clearing local state does not touch it.
    setDismissedPreset(true);
    if (action === "accept") onFixed?.(finding);
  };

  const start = (regenerate = false) =>
    rewrite.mutate(
      { targetRef: finding.target_ref, findingId: finding.id, regenerate },
      { onSuccess: setDraft },
    );

  // Resolved cards collapse to an acknowledgement. The two-column
  // improve/fix grid and the buttons are all about work still to do, and
  // keeping them would invite the user to rewrite something they already
  // fixed -- the finding is gone from the server, so the button would fail.
  if (fixed) {
    return (
      <li
        className="flex items-start gap-2.5 rounded-xl border border-accept-text/40
                   bg-accept-text/5 p-3"
      >
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-accept-text" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-accept-text">Fixed with AI</p>
          <p className="mt-0.5 text-xs leading-relaxed text-content-muted">
            {finding.message}
          </p>
        </div>
        {finding.points > 0 && (
          <span
            className="shrink-0 rounded-full bg-accept-text/15 px-2 py-0.5 text-[10px]
                       font-semibold text-accept-text"
          >
            +{finding.points} pts applied
          </span>
        )}
      </li>
    );
  }

  return (
    <li
      className={cn(
        "rounded-xl border p-3",
        fixed
          ? "border-accept-text/40 bg-accept-text/5"
          : "border-line-soft bg-surface-2",
      )}
    >
      <div className="flex items-start gap-2.5">
        <span
          className={cn("mt-1.5 size-2 shrink-0 rounded-full", severity.dot)}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                severity.tone,
              )}
            >
              {severity.pill}
            </span>
          </div>
          <p className="mt-1.5 text-sm font-medium text-content">{finding.message}</p>

          {/* Two columns, as in the reference: the problem and the remedy
              read as separate thoughts instead of one run-on paragraph.
              Stacks on narrow viewports -- the analysis pane is often a
              side panel next to the live preview. */}
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg bg-surface-3/60 p-2.5">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-content-muted">
                What to improve
              </p>
              <p className="mt-1 text-xs leading-relaxed text-content">
                {finding.message}
              </p>
            </div>
            <div className="rounded-lg bg-brand/10 p-2.5">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-brand-text">
                Why and how to fix
              </p>
              <p className="mt-1 text-xs leading-relaxed text-content">
                {finding.fix_hint}
              </p>
            </div>
          </div>

          {!shownDraft && !rewrite.isPending && (
            <div className="mt-2 flex flex-wrap items-center gap-3">
              {canRerank && (
                <button
                  type="button"
                  disabled={rerank.isPending}
                  onClick={() => rerank.mutate(finding.target_ref)}
                  className="inline-flex items-center gap-1 rounded-md text-xs font-medium
                             text-brand-text hover:underline disabled:opacity-60
                             focus-visible:outline-2 focus-visible:outline-brand"
                >
                  {rerank.isPending ? (
                    <Loader2 className="size-3 animate-spin" aria-hidden />
                  ) : (
                    <ArrowDownNarrowWide className="size-3" aria-hidden />
                  )}
                  Reorder newest-first
                </button>
              )}
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
              {canQuantify && (
                <button
                  type="button"
                  onClick={() => start()}
                  title="The AI reshapes this line so a figure fits and leaves a blank — you supply the number"
                  className="inline-flex items-center gap-1 rounded-md text-xs font-medium
                             text-brand-text hover:underline
                             focus-visible:outline-2 focus-visible:outline-brand"
                >
                  <Sparkles className="size-3" aria-hidden />
                  Make room for a metric
                </button>
              )}
              {onFix && (
                <button
                  type="button"
                  onClick={() => onFix(finding)}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-md text-xs font-medium",
                    "focus-visible:outline-2 focus-visible:outline-brand hover:underline",
                    canDraft ? "text-content-muted" : "text-brand-text",
                  )}
                >
                  {canDraft ? "Edit it myself" : "Go to field"}
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

          {rerank.isError && !rerank.isPending && (
            <p className="mt-2.5 text-xs leading-relaxed text-reject-text" role="alert">
              {/* Nearly always the 409: drafts are open on this section and
                  reordering would re-point them at another role. The server
                  message already names the count, so it is shown verbatim
                  rather than replaced with something vaguer. */}
              {(rerank.error as Error)?.message ??
                "Could not reorder this section."}
            </p>
          )}

          {rewrite.isError && !rewrite.isPending && (
            <div className="mt-2.5" role="alert">
              {/* A refusal and an outage are not the same news. Only the
                  former is the user's problem, so only the former is red. */}
              <p
                className={
                  "text-xs leading-relaxed " +
                  (rewrite.error instanceof ApiError && rewrite.error.isTransient
                    ? "text-content-muted"
                    : "text-reject-text")
                }
              >
                {(rewrite.error as Error)?.message ??
                  "Could not draft a revision for this."}
              </p>
              <div className="mt-1.5 flex items-center gap-3">
                {/* "Try again" only when trying again could work.
                    rewrite_for_finding already makes two informed attempts
                    server-side, so this button is attempt three. When the
                    writer has declined because the missing number is one only
                    the user knows, every one of those attempts is guaranteed
                    to 422 -- the corpus grades that refusal as correct
                    behaviour. Offering a retry there is an invitation to a
                    dead end; the useful affordance is "Go to field". */}
                {!(rewrite.error instanceof ApiError && rewrite.error.isFinalRefusal) && (
                  <button
                    type="button"
                    onClick={() => start()}
                    className="inline-flex items-center gap-1 text-xs font-medium
                               text-brand-text hover:underline"
                  >
                    <RefreshCw className="size-3" aria-hidden />
                    Try again
                  </button>
                )}
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
      {shownDraft && (
        <div className="mt-3">
          <SuggestionCard
            suggestion={shownDraft}
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
