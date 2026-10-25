import { useState } from "react";
import { Check, HelpCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAnswerQuestion } from "@/api/queries";
import type { SuggestionOut } from "@/services";

/**
 * The one card that asks instead of proposing.
 *
 * Every other card in this app shows a draft and asks for approval. This one
 * has no draft: the job description wanted a skill the resume cannot support,
 * so the only thing that can resolve it is a fact the user has and we do not.
 *
 * Three deliberate departures from the reference screenshot:
 *
 *  1. **No amber.** The reference uses a warning colour. Nothing is wrong
 *     here -- a question is not an error -- and the palette has no yellow.
 *  2. **The question asks "where", not "whether".** Wording is generated
 *     backend-side by `clarifying.py`; see the note there on leading
 *     questions. The card renders it, never rephrases it.
 *  3. **"Not really" is a real button, not a dismissal X.** If saying no is
 *     harder than saying yes, the answers stop being worth anything.
 */
/**
 * The skills variant: a multiple choice, not a free-text claim.
 *
 * Nothing is pre-selected. Pre-ticking every box would make the default
 * state "I have all of these", which is exactly the claim the user is here
 * to make deliberately -- the whole reason this is a question card and not
 * an AI-written draft.
 *
 * The chips come from `suggestion.keywords`, which the server put there; the
 * submitted list is checked back against it server-side, so a tampered
 * client cannot widen the menu.
 */
function SkillPicker({
  suggestion,
  resumeId,
  sessionId = null,
  readOnly = false,
  onResolved,
}: {
  suggestion: SuggestionOut;
  resumeId: string;
  sessionId?: string | null;
  readOnly?: boolean;
  onResolved?: (outcome: "attested" | "declined") => void;
}) {
  const answerMutation = useAnswerQuestion(resumeId, sessionId ?? null);
  const [picked, setPicked] = useState<string[]>([]);
  const busy = answerMutation.isPending;

  function toggle(skill: string) {
    setPicked((prev) =>
      prev.includes(skill) ? prev.filter((s) => s !== skill) : [...prev, skill],
    );
  }

  function submit(selection: string[]) {
    answerMutation.mutate(
      { id: suggestion.id, answer: selection.join(", "), confirmed: selection.length > 0 },
      {
        onSuccess: (updated) =>
          onResolved?.(updated.attested ? "attested" : "declined"),
      },
    );
  }

  return (
    <div className="rounded-xl border border-line-soft bg-surface-2 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1 rounded-full bg-brand/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-content-accent">
          <HelpCircle className="size-3" />
          Pick what you can back up
        </span>
        <span className="ml-auto text-xs text-muted">{suggestion.placement}</span>
      </div>

      <p className="mt-2 text-sm leading-relaxed text-ink">{suggestion.question}</p>

      {!readOnly && (
        <>
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {suggestion.keywords.map((skill) => {
              const on = picked.includes(skill);
              return (
                <li key={skill}>
                  <button
                    type="button"
                    aria-pressed={on}
                    disabled={busy}
                    onClick={() => toggle(skill)}
                    className={
                      "rounded-full border px-2.5 py-1 text-xs transition-colors " +
                      (on
                        ? "border-change-border bg-change text-on-change"
                        : "border-line bg-surface-1 text-ink hover:border-brand")
                    }
                  >
                    {on && <Check className="mr-1 inline size-3" aria-hidden />}
                    {skill}
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => submit(picked)} disabled={busy || !picked.length}>
              {busy && <Loader2 className="mr-1 size-3 animate-spin" />}
              {picked.length
                ? `Add ${picked.length} skill${picked.length === 1 ? "" : "s"}`
                : "Select skills to add"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => submit([])} disabled={busy}>
              None of these
            </Button>
            <span className="text-xs text-muted">
              Added as yours, not as an AI edit.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

export function ClarifyingQuestionCard({
  suggestion,
  resumeId,
  sessionId = null,
  readOnly = false,
  onResolved,
}: {
  suggestion: SuggestionOut;
  resumeId: string;
  sessionId?: string | null;
  readOnly?: boolean;
  onResolved?: (outcome: "attested" | "declined") => void;
}) {
  const answerMutation = useAnswerQuestion(resumeId, sessionId ?? null);
  const [text, setText] = useState("");

  // Skills are a choice over a list, not a claim about one keyword, so they
  // get their own grammar rather than a textarea asking "where did you use
  // java8/17/21, springboot3, activemq".
  //
  // Keyed on target_ref, NOT section: the tailoring clarifying flow is also
  // section="skills" (with an empty target_ref) and asks about exactly one
  // keyword -- routing it here would replace a careful question with a
  // one-chip picker.
  if (suggestion.target_ref === "skills") {
    return (
      <SkillPicker
        suggestion={suggestion}
        resumeId={resumeId}
        sessionId={sessionId}
        readOnly={readOnly}
        onResolved={onResolved}
      />
    );
  }

  const skill = suggestion.keywords[0] ?? "this";
  const busy = answerMutation.isPending;
  // The server enforces this too (422). Mirroring it here means the button is
  // visibly unavailable rather than available-then-rejected.
  const confirmBlocked = suggestion.requires_text && text.trim().length === 0;

  function submit(confirmed: boolean) {
    answerMutation.mutate(
      { id: suggestion.id, answer: text.trim(), confirmed },
      {
        onSuccess: (updated) =>
          onResolved?.(updated.attested ? "attested" : "declined"),
      },
    );
  }

  return (
    <div className="rounded-xl border border-line-soft bg-surface-2 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1 rounded-full bg-brand/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-content-accent">
          <HelpCircle className="size-3" />
          Needs your answer
        </span>
        {suggestion.requires_text && (
          <span className="rounded-full border border-line px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted">
            Needs detail
          </span>
        )}
        <span className="ml-auto text-xs text-muted">{suggestion.placement}</span>
      </div>

      <p className="mt-2 text-sm leading-relaxed text-ink">{suggestion.question}</p>

      {!readOnly && (
        <>
          <label className="mt-3 block">
            <span className="sr-only">Your answer about {skill}</span>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={busy}
              rows={2}
              placeholder={
                suggestion.requires_text
                  ? "e.g. Ran it at Acme for two years on the platform team"
                  : "Where did you use it? (optional)"
              }
              className="w-full resize-y rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-brand focus:outline-none"
            />
          </label>

          {/* Only shown once the user has engaged with the box, so it reads as
              guidance rather than as an error on an untouched field. */}
          {confirmBlocked && text.length > 0 && (
            <p className="mt-1 text-xs text-muted">
              This job named a duration, so a yes on its own will not do - say
              where and how long.
            </p>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              onClick={() => submit(true)}
              disabled={busy || confirmBlocked}
            >
              {busy && <Loader2 className="mr-1 size-3 animate-spin" />}
              {suggestion.requires_text ? "Add this" : "Yes, I have"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                // Explicitly empty: a decline is an answer, and the backend
                // records it as one rather than treating it as a dismissal.
                setText("");
                answerMutation.mutate(
                  { id: suggestion.id, answer: "", confirmed: false },
                  { onSuccess: () => onResolved?.("declined") },
                );
              }}
              disabled={busy}
            >
              Not really
            </Button>
            <span className="text-xs text-muted">
              Only you can answer this - it is not in your resume.
            </span>
          </div>
        </>
      )}

      {answerMutation.isError && (
        <p className="mt-2 text-xs text-reject-text">
          {(answerMutation.error as Error).message}
        </p>
      )}
    </div>
  );
}
