import { useState } from "react";
import { HelpCircle, Loader2 } from "lucide-react";
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
