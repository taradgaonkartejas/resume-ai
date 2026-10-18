import { useState } from "react";
import { ArrowRight, FileText, Info, Loader2, Sparkles } from "lucide-react";
import { useRerankSection, useRewriteSection } from "@/api/queries";
import type { AnalysisStepsOut, SuggestionOut } from "@/services";
import { partitionFormatFindings, runFormatFixes } from "./formatFixes";

/**
 * Step 4 (Format & Structure): the prerequisite notice and the bulk CTA.
 *
 * Reference `Screenshot 2026-10-11 165822`. Two deliberate departures from it:
 *
 * 1. **The notice is not amber.** The reference renders it yellow-on-cream;
 *    the standing palette rule excludes yellow entirely, so it uses the
 *    brand accent family instead. Flagged rather than silently converted.
 *
 * 2. **The button does not claim to "format your resume".** Of the format
 *    step's findings only some can be resolved without the user: today one
 *    is deterministic (reorder) and one can be drafted by the writer. The
 *    rest -- no skills section, no education, too thin, too long,
 *    unparseable dates, decorative glyphs -- need information or a judgement
 *    only the user has. A button labelled "Format My Resume" that silently
 *    leaves five findings standing is a promise the product cannot keep, so
 *    the copy states the counts up front and the summary states what
 *    actually happened.
 */
export function FormatStepPanel({
  steps,
  step,
  resumeId,
  onDrafts,
}: {
  steps: AnalysisStepsOut;
  step: AnalysisStepsOut["steps"][number];
  resumeId?: string | null;
  /** Hands bulk-created drafts back so the cards above can render them. */
  onDrafts: (drafts: Record<string, SuggestionOut>) => void;
}) {
  const rerank = useRerankSection(resumeId ?? null);
  const rewrite = useRewriteSection(resumeId ?? null);

  const [progress, setProgress] = useState<{ done: number; total: number } | null>(
    null,
  );
  const [report, setReport] = useState<{
    applied: number;
    drafted: number;
    failed: string[];
  } | null>(null);

  // Earlier steps still carrying findings. The analysis of *this* step reads
  // the whole document, so fixing experience first genuinely changes what
  // the format checks see -- the advice is real, not decoration.
  const myIndex = steps.steps.findIndex((s) => s.id === step.id);
  const incomplete = steps.steps
    .slice(0, myIndex < 0 ? 0 : myIndex)
    .filter((s) => s.findings.length > 0);

  const { auto, draftable, manual, actionable } = partitionFormatFindings(
    step.findings,
  );

  const run = async () => {
    if (!resumeId) return;
    setReport(null);
    const r = await runFormatFixes({
      findings: step.findings,
      rerank: (ref) => rerank.mutateAsync(ref),
      rewrite: (args) => rewrite.mutateAsync(args),
      onProgress: (done, total) => setProgress({ done, total }),
    });
    setProgress(null);
    setReport(r);
    if (Object.keys(r.drafts).length) onDrafts(r.drafts);
  };

  const busy = progress !== null;

  return (
    <section className="space-y-3">
      {incomplete.length > 0 && (
        <div className="flex items-start gap-2.5 rounded-xl border border-brand/30 bg-brand/10 p-3">
          <Info className="mt-0.5 size-4 shrink-0 text-brand-text" aria-hidden />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-brand-text">
              Recommended: complete the earlier steps first
            </p>
            <p className="mt-1 text-xs leading-relaxed text-content-muted">
              Formatting checks read the whole document, so{" "}
              {incomplete.map((s) => s.title).join(", ")} still{" "}
              {incomplete.length === 1 ? "has" : "have"}{" "}
              {incomplete.reduce((a, s) => a + s.findings.length, 0)} open{" "}
              {incomplete.reduce((a, s) => a + s.findings.length, 0) === 1
                ? "recommendation"
                : "recommendations"}{" "}
              that will change what this step sees.
            </p>
          </div>
        </div>
      )}

      <div className="rounded-xl border border-line-soft bg-surface-2 p-5 text-center">
        <div className="mx-auto flex size-11 items-center justify-center rounded-xl bg-brand/15">
          <FileText className="size-5 text-brand-text" aria-hidden />
        </div>
        <h3 className="mt-3 text-base font-semibold text-content">
          {actionable > 0
            ? "Ready to reformat your resume?"
            : "Nothing left to fix automatically"}
        </h3>
        <p className="mx-auto mt-1.5 max-w-md text-xs leading-relaxed text-content-muted">
          {actionable > 0 ? (
            <>
              {auto.length > 0 && (
                <>
                  {auto.length} fix{auto.length === 1 ? "" : "es"} can be applied
                  straight away
                </>
              )}
              {auto.length > 0 && draftable.length > 0 && ", and "}
              {draftable.length > 0 && (
                <>
                  {draftable.length} can be drafted by AI for you to review
                </>
              )}
              .
              {manual.length > 0 && (
                <>
                  {" "}
                  The other {manual.length} need{manual.length === 1 ? "s" : ""}{" "}
                  your input and {manual.length === 1 ? "is" : "are"} listed above.
                </>
              )}
            </>
          ) : manual.length > 0 ? (
            <>
              All {manual.length} remaining recommendation
              {manual.length === 1 ? "" : "s"} need information only you have —
              work through the cards above.
            </>
          ) : (
            <>This section is scoring full marks.</>
          )}
        </p>

        {actionable > 0 && (
          <button
            type="button"
            onClick={run}
            disabled={busy || !resumeId}
            className="mx-auto mt-4 inline-flex items-center gap-2.5 rounded-xl bg-brand px-5
                       py-2.5 text-left text-white transition hover:opacity-90
                       disabled:opacity-60 focus-visible:outline-2
                       focus-visible:outline-offset-2 focus-visible:outline-brand"
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Sparkles className="size-4" aria-hidden />
            )}
            <span>
              <span className="block text-sm font-semibold">
                {busy ? "Working…" : "Fix formatting"}
              </span>
              <span className="block text-[11px] opacity-80">
                {busy
                  ? `${progress.done} of ${progress.total} done`
                  : `${actionable} item${actionable === 1 ? "" : "s"}`}
              </span>
            </span>
            {!busy && <ArrowRight className="size-4" aria-hidden />}
          </button>
        )}

        {report && (
          <div className="mt-3 space-y-1 text-xs" role="status">
            {report.applied > 0 && (
              <p className="text-accept-text">
                {report.applied} fix{report.applied === 1 ? "" : "es"} applied to
                your resume.
              </p>
            )}
            {report.drafted > 0 && (
              <p className="text-content">
                {report.drafted} draft{report.drafted === 1 ? "" : "s"} ready to
                review in the cards above.
              </p>
            )}
            {report.failed.map((m, i) => (
              <p key={i} className="text-reject-text">
                {m}
              </p>
            ))}
            {report.applied === 0 &&
              report.drafted === 0 &&
              report.failed.length === 0 && (
                <p className="text-content-muted">Nothing changed.</p>
              )}
          </div>
        )}
      </div>
    </section>
  );
}
