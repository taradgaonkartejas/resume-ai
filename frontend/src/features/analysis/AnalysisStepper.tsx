import { useCallback, useEffect, useState } from "react";
import {
  ArrowLeft, ArrowRight, Check, ChevronLeft, CloudOff, Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScoreRing } from "@/features/analysis/ScoreRing";
import { RecommendationCard } from "./RecommendationCard";
import { StepSections } from "./StepSections";
import { useAutosave } from "./useAutosave";
import { useAnalysisSteps, useResume } from "@/api/queries";
import { rankFindings, type AnalysisStep, type Finding } from "@/services";
import { cn } from "cn";

/**
 * The guided step editor.
 *
 * Two modes in one component, matching the reference: a section list, and a
 * focused single-step editor entered by clicking a section.
 *
 * The score shown here comes from GET /analysis/steps, which recomputes from
 * the live structured_data on every call — so a fixed recommendation vanishes
 * and the number climbs within one debounce of the user's last keystroke,
 * without them pressing "Re-run".
 */
export function AnalysisStepper({
  resumeId,
  onExit,
}: {
  resumeId: string | null;
  onExit?: () => void;
}) {
  const { data: resume } = useResume(resumeId);
  const { data: steps, isLoading } = useAnalysisSteps(resumeId);
  const { draft, update, flush, state } = useAutosave(
    resumeId,
    resume?.structured_data,
  );

  const [active, setActive] = useState<number | null>(null);
  const [focusRef, setFocusRef] = useState<string | null>(null);

  // Leaving a step must not strand an unsaved edit in the debounce window.
  const goTo = useCallback(
    (index: number | null) => {
      flush();
      setFocusRef(null);
      setActive(index);
    },
    [flush],
  );

  const onFix = useCallback((finding: Finding) => {
    setFocusRef(finding.target_ref);
    const el = document.getElementById(`f-${finding.target_ref}`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  // Arrow-key navigation between steps, but never while the user is typing.
  useEffect(() => {
    if (active === null || !steps) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "ArrowRight" && active < steps.steps.length - 1) goTo(active + 1);
      if (e.key === "ArrowLeft" && active > 0) goTo(active - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, steps, goTo]);

  if (isLoading || !steps || !draft) {
    return (
      <div className="grid h-40 place-items-center">
        <Loader2 className="size-5 animate-spin text-brand-text" aria-hidden />
      </div>
    );
  }

  const step = active === null ? null : steps.steps[active];

  return (
    <div className="space-y-4">
      {step === null ? (
        <SectionList steps={steps.steps} total={steps.overall_score}
                     available={steps.points_available} onOpen={goTo}
                     onExit={onExit} />
      ) : (
        <>
          <StepHeader
            step={step}
            count={steps.steps.length}
            score={steps.overall_score}
            saveState={state}
            onPrev={active > 0 ? () => goTo(active - 1) : undefined}
            onNext={active < steps.steps.length - 1 ? () => goTo(active + 1) : undefined}
            onBack={() => goTo(null)}
          />

          {step.findings.length > 0 && (
            <section>
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                Recommendations
                <Badge variant="secondary">{step.findings.length}</Badge>
                <span className="ml-auto text-xs font-normal text-content-muted">
                  worth {step.points_available} point
                  {step.points_available === 1 ? "" : "s"}
                </span>
              </h3>
              <ul className="space-y-2">
                {rankFindings(step.findings).map((f) => (
                  <RecommendationCard
                    key={f.id}
                    finding={f}
                    resumeId={resumeId}
                    onFix={onFix}
                  />
                ))}
              </ul>
            </section>
          )}

          {step.findings.length === 0 && (
            <p className="flex items-center gap-2 rounded-xl border border-accept-text/30
                          bg-accept-text/10 px-3 py-2.5 text-sm text-accept-text">
              <Check className="size-4 shrink-0" aria-hidden />
              This section is scoring full marks. Edit it below if you want.
            </p>
          )}

          <section className="rounded-xl border border-line-soft p-3">
            <StepSections step={step.id} data={draft} onChange={update}
                          focusRef={focusRef} />
          </section>

          <div className="flex items-center justify-between gap-2 pt-1">
            <Button variant="ghost" onClick={() => goTo(null)}>
              <ChevronLeft className="size-4" aria-hidden /> Back to sections
            </Button>
            {active < steps.steps.length - 1 && (
              <Button onClick={() => goTo(active + 1)}>
                Next: {steps.steps[active + 1].title}
                <ArrowRight className="size-4" aria-hidden />
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- list --- */
function SectionList({
  steps, total, available, onOpen, onExit,
}: {
  steps: AnalysisStep[];
  total: number;
  available: number;
  onOpen: (i: number) => void;
  onExit?: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 rounded-xl border border-line-soft
                      bg-surface-2 p-4">
        <ScoreRing score={total} size={68} />
        <div className="min-w-0">
          <p className="text-sm font-semibold">Guided improvements</p>
          <p className="mt-0.5 text-xs text-content-muted">
            {available > 0
              ? `${available} points available across ${
                  steps.filter((s) => s.finding_count > 0).length
                } sections`
              : "Every check is passing."}
          </p>
        </div>
        {onExit && (
          <Button variant="ghost" size="sm" className="ml-auto self-start"
                  onClick={onExit}>
            Score view
          </Button>
        )}
      </div>

      <ol className="space-y-2">
        {steps.map((step) => (
          <li key={step.id}>
            <button
              onClick={() => onOpen(step.index)}
              className="flex w-full items-center gap-3 rounded-xl border
                         border-line-soft bg-surface-2 p-3 text-left transition
                         hover:border-brand/50 focus-visible:outline-2
                         focus-visible:outline-brand"
            >
              <span
                className={cn(
                  "grid size-8 shrink-0 place-items-center rounded-full text-xs font-semibold",
                  step.status === "clear"
                    ? "bg-accept-text/15 text-accept-text"
                    : step.status === "attention"
                      ? "bg-warning/15 text-warning"
                      : "bg-surface-3 text-content-muted",
                )}
              >
                {step.status === "clear"
                  ? <Check className="size-4" aria-hidden />
                  : step.index + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{step.title}</span>
                <span className="mt-0.5 block text-xs text-content-muted">
                  {step.status === "optional"
                    ? "Optional \u2014 not scored"
                    : step.finding_count === 0
                      ? "No issues found"
                      : `${step.finding_count} recommendation${
                          step.finding_count === 1 ? "" : "s"
                        }`}
                </span>
              </span>
              {step.points_available > 0 && (
                <Badge
                  variant="secondary"
                  className="shrink-0 bg-brand/15 font-semibold tabular-nums text-brand-text"
                >
                  +{step.points_available}
                </Badge>
              )}
              {step.max > 0 && (
                <span className="shrink-0 text-xs tabular-nums text-content-muted">
                  {step.score}/{step.max}
                </span>
              )}
              <ArrowRight className="size-4 shrink-0 text-content-muted" aria-hidden />
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

/* -------------------------------------------------------------- header --- */
function StepHeader({
  step, count, score, saveState, onPrev, onNext, onBack,
}: {
  step: AnalysisStep;
  count: number;
  score: number;
  saveState: string;
  onPrev?: () => void;
  onNext?: () => void;
  onBack: () => void;
}) {
  return (
    <header className="rounded-xl border border-line-soft bg-surface-2 p-3">
      <div className="flex items-center gap-2">
        <button
          onClick={onBack}
          className="rounded-md px-1.5 py-1 text-xs text-content-muted
                     hover:text-content"
        >
          Back to sections
        </button>
        <span className="ml-auto flex items-center gap-1">
          <button
            onClick={onPrev} disabled={!onPrev} aria-label="Previous step"
            className="rounded-md p-1 text-content-muted hover:text-content
                       disabled:opacity-30"
          >
            <ArrowLeft className="size-4" aria-hidden />
          </button>
          <button
            onClick={onNext} disabled={!onNext} aria-label="Next step"
            className="rounded-md p-1 text-content-muted hover:text-content
                       disabled:opacity-30"
          >
            <ArrowRight className="size-4" aria-hidden />
          </button>
        </span>
      </div>

      <div className="mt-1.5 flex items-baseline gap-2">
        <h2 className="text-base font-semibold">
          <span className="text-content-muted">Step {step.index + 1} · </span>
          {step.title}
        </h2>
        <span className="ml-auto text-xs tabular-nums text-content-muted">
          {step.score}/{step.max}
        </span>
      </div>
      <p className="mt-1 text-xs leading-relaxed text-content-muted">
        {step.description}
      </p>

      <div className="mt-2.5 flex items-center gap-3 border-t border-line-soft pt-2.5">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
          <div
            className="h-full rounded-full bg-brand transition-[width] duration-500"
            style={{ width: `${((step.index + 1) / count) * 100}%` }}
          />
        </div>
        <span className="text-[11px] tabular-nums text-content-muted">
          Score {score}/100
        </span>
        <SaveBadge state={saveState} />
      </div>
    </header>
  );
}

function SaveBadge({ state }: { state: string }) {
  if (state === "saving" || state === "dirty") {
    return (
      <span className="flex items-center gap-1 text-[11px] text-content-muted">
        <Loader2 className="size-3 animate-spin" aria-hidden /> Saving
      </span>
    );
  }
  if (state === "saved") {
    return (
      <span className="flex items-center gap-1 text-[11px] text-accept-text">
        <Check className="size-3" aria-hidden /> Saved
      </span>
    );
  }
  if (state === "error") {
    return (
      <span role="alert" className="flex items-center gap-1 text-[11px] text-reject-text">
        <CloudOff className="size-3" aria-hidden /> Not saved
      </span>
    );
  }
  return null;
}
