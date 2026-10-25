import { useCallback, useEffect, useState } from "react";
import {
  ArrowLeft, ArrowRight, Check, ChevronLeft, CloudOff, Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScoreRing } from "@/features/analysis/ScoreRing";
import { RecommendationCard } from "./RecommendationCard";
import { StepSections } from "./StepSections";
import { CommentaryBlock } from "./CommentaryBlock";
import { GroupedFindings } from "./GroupedFindings";
import { FormatStepPanel } from "./FormatStepPanel";
import { CategoryStrengths } from "./CategoryStrengths";
import { useAutosave } from "./useAutosave";
import { useAnalysisSteps, useResume } from "@/api/queries";
import {
  rankFindings,
  type AnalysisStep,
  type Finding,
  type SuggestionOut,
} from "@/services";
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

  /** "Go to field" -- take the user to the input the finding is about.
   *
   *  This button did nothing for most findings, and did it silently. Two
   *  causes, both worth naming:
   *
   *  1. Only the ACTIVE step is mounted. A finding listed on the summary step
   *     that points at `contact.email` had no element to find, because the
   *     contact step was not rendered. The lookup returned null, `el?.` 
   *     swallowed it, and the click looked like a no-op. So: find the step
   *     that owns the ref, switch to it, and scroll on the NEXT frame, once
   *     React has mounted the new step.
   *  2. `contact.links.missing` fires precisely when there are no links, and
   *     the links fieldset renders one row per link -- zero rows, nothing to
   *     focus. Landing on the empty fieldset is not help. `onSeed` adds the
   *     blank row first so there is a cursor to put somewhere.
   */
  /** Add the empty link row that `contact.links.missing` is complaining
   *  about, so "Go to field" has somewhere to put the cursor. */
  const onSeedLink = useCallback(() => {
    if (!draft) return;
    update({
      ...draft,
      contact: { ...draft.contact, links: [...(draft.contact.links ?? []), ""] },
    });
  }, [draft, update]);

  /**
   * Findings resolved by accepting an AI draft during THIS session.
   *
   * The server has no concept of a fixed finding -- fixing it makes it
   * absent from the next /analysis/steps response, so the card would
   * simply vanish mid-interaction with no confirmation that the thing the
   * user just clicked did anything. We keep the finding ourselves and
   * re-render it as a green acknowledgement. Deliberately session-only:
   * it is a congratulation, not state, and a refresh should clear it.
   */
  // Drafts the Step-4 bulk action created, keyed finding+ref, so the cards
  // above can show them. Session-only: they are real Suggestions on the
  // server, this map is just how the cards learn they exist.
  const [bulkDrafts, setBulkDrafts] = useState<Record<string, SuggestionOut>>({});

  const [fixedFindings, setFixedFindings] = useState<
    { stepId: string; finding: Finding }[]
  >([]);
  // The step id is captured here rather than derived from target_ref later:
  // "exp_0.bullets" -> experience is a guess, and the caller already knows
  // the answer for certain.
  const onFixed = useCallback((stepId: string, finding: Finding) => {
    setFixedFindings((prev) =>
      prev.some(
        (e) =>
          e.finding.id === finding.id &&
          e.finding.target_ref === finding.target_ref,
      )
        ? prev
        : [...prev, { stepId, finding }],
    );
  }, []);

  const onFix = useCallback(
    (finding: Finding) => {
      const ref = finding.target_ref;
      setFocusRef(ref);

      const owner = steps?.steps.findIndex((st) =>
        st.findings.some((f) => f.target_ref === ref),
      );
      const target = owner !== undefined && owner >= 0 ? owner : active;
      if (target !== null && target !== active) {
        flush();
        setActive(target);
      }

      if (ref === "contact.links" && (draft?.contact?.links?.length ?? 0) === 0) {
        onSeedLink();
      }

      // Two frames: one for the step swap to commit, one for any row the
      // seed above just added.
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          const el = document.getElementById(`f-${ref}`);
          if (!el) {
            // Never fail silently again. A navigation affordance that cannot
            // find its target is a bug, and the console is where it belongs.
            console.warn(`[AnalysisStepper] no element for target_ref "${ref}"`);
            return;
          }
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          const field =
            el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
              ? el
              : el.querySelector<HTMLElement>("input, textarea");
          field?.focus({ preventScroll: true });
        }),
      );
    },
    [steps, active, draft, flush, onSeedLink],
  );

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

  // Only findings the server has since dropped. If a rewrite was accepted
  // but the finding is still reported -- a partial fix, or an accept that
  // did not move the needle -- the live card stays and we claim nothing.
  const stepFixed =
    step === null
      ? []
      : fixedFindings
          .filter(
            (e) =>
              e.stepId === step.id &&
              !step.findings.some(
                (f) =>
                  f.id === e.finding.id &&
                  f.target_ref === e.finding.target_ref,
              ),
          )
          .map((e) => e.finding);

  return (
    <div className="space-y-4">
      {step === null ? (
        <SectionList steps={steps.steps} total={steps.overall_score}
                     available={steps.points_available} onOpen={goTo}
                     headline={steps.commentary?.headline ?? ""}
                     headlineStale={steps.commentary_stale}
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

          {/* The per-category prose we have been generating and discarding.
              "SECTION ANALYSIS" on the experience step, "Overall Assessment"
              everywhere else -- same data, named for what it describes. */}
          <CommentaryBlock
            text={steps.commentary?.category_notes?.[step.id] ?? ""}
            stale={steps.commentary_stale}
            label={step.id === "experience" ? "Section analysis" : "Overall Assessment"}
          />

          {/* Between the assessment and the recommendations, as in the
              reference: what works, then what to change. Renders nothing
              when the backend filtered everything out. */}
          <CategoryStrengths items={steps.commentary?.strengths?.[step.id]} />

          {(step.findings.length > 0 || stepFixed.length > 0) && (
            <section>
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                Recommendations
                <Badge variant="secondary">{step.findings.length}</Badge>
                {step.advisory ? (
                  <span className="ml-auto text-xs font-normal text-content-muted">
                    pass/fail — no points
                  </span>
                ) : (
                  <span className="ml-auto text-xs font-normal text-content-muted">
                    worth {step.points_available} point
                    {step.points_available === 1 ? "" : "s"}
                  </span>
                )}
              </h3>
              {/* Experience and Projects are the steps with repeating
                  subjects: the same "weak verb" finding can appear five
                  times, once per entry, distinguishable only by reading
                  target_ref. Every other step has a single subject and a
                  flat list is right. */}
              {step.id === "experience" || step.id === "projects" ? (
                <GroupedFindings
                  findings={step.findings}
                  data={draft}
                  section={step.id}
                  resumeId={resumeId}
                  onFix={onFix}
                  onFixed={(f) => onFixed(step.id, f)}
                />
              ) : (
                <ul className="space-y-2">
                  {rankFindings(step.findings).map((f) => (
                    <RecommendationCard
                      key={f.id}
                      finding={f}
                      resumeId={resumeId}
                      onFix={onFix}
                      onFixed={(fd) => onFixed(step.id, fd)}
                      presetDraft={bulkDrafts[`${f.id}::${f.target_ref}`]}
                    />
                  ))}
                </ul>
              )}

              {stepFixed.length > 0 && (
                <ul className="mt-2 space-y-2">
                  {stepFixed.map((f) => (
                    <RecommendationCard
                      key={`fixed-${f.id}-${f.target_ref}`}
                      finding={f}
                      fixed
                    />
                  ))}
                </ul>
              )}
            </section>
          )}

          {/* Step 4 only: the reference gives Format & Structure a bulk CTA
              and a prerequisite notice that no other step has. */}
          {step.id === "format" && (
            <FormatStepPanel
              steps={steps}
              step={step}
              resumeId={resumeId}
              onDrafts={(d) => setBulkDrafts((prev) => ({ ...prev, ...d }))}
            />
          )}

          {step.findings.length === 0 && step.id !== "format" && (
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
  steps, total, available, onOpen, onExit, headline, headlineStale,
}: {
  steps: AnalysisStep[];
  total: number;
  available: number;
  onOpen: (i: number) => void;
  onExit?: () => void;
  headline: string;
  headlineStale: boolean;
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

      {/* The ScoringAgent's one-line verdict. Computed on every analysis
          since the start; never shown until now. */}
      <CommentaryBlock text={headline} stale={headlineStale}
                       label="Overall Assessment" />

      <div className="px-1">
        <h2 className="text-base font-semibold text-content">
          Steps to increase your score
        </h2>
        <p className="mt-1 text-xs text-content-muted">
          Recruiter checks that are bringing your score down. Open each one to
          see what to change and why.
        </p>
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
                    : step.advisory
                      ? step.finding_count === 0
                        ? "All gate checks pass"
                        : `${step.finding_count} gate check${
                            step.finding_count === 1 ? "" : "s"
                          } failed`
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
          {step.max > 0
            ? `${step.score}/${step.max}`
            : step.advisory
              ? "gate"
              : "optional"}
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
