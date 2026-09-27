import { ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "cn";
import type { Finding } from "@/services";

const SEVERITY: Record<Finding["severity"], { dot: string; label: string }> = {
  high: { dot: "bg-reject-text", label: "High impact" },
  medium: { dot: "bg-warning", label: "Medium impact" },
  low: { dot: "bg-brand", label: "Low impact" },
};

/**
 * One recommendation.
 *
 * The "+N points" badge is the load-bearing element of this whole feature: it
 * is the literal delta the score will move when the finding is resolved, and
 * backend tests (tests/test_findings.py) assert that fixing it changes
 * overall_score by exactly N. If those ever diverge, remove the badge rather
 * than soften it — a number the user can check and catch being wrong costs
 * more trust than showing nothing.
 */
export function RecommendationCard({
  finding,
  onFix,
}: {
  finding: Finding;
  /** Scrolls to and focuses the field named by target_ref. */
  onFix?: (finding: Finding) => void;
}) {
  const severity = SEVERITY[finding.severity];

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
          {onFix && (
            <button
              type="button"
              onClick={() => onFix(finding)}
              className="mt-2 inline-flex items-center gap-1 rounded-md text-xs
                         font-medium text-brand-text hover:underline
                         focus-visible:outline-2 focus-visible:outline-brand"
            >
              Fix this
              <ArrowRight className="size-3" aria-hidden />
            </button>
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
    </li>
  );
}
