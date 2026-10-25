import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Bot,
  Check,
  Loader2,
  RotateCw,
  Send,
  Sparkles,
  TrendingDown,
  TrendingUp,
  TriangleAlert,
  Wand2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useChat,
  useSendMessage,
  useSuggestion,
  useSuggestionAction,
} from "@/api/queries";
import { ClarifyingQuestionCard } from "@/features/suggestions/ClarifyingQuestionCard";
import type { ChatMessageOut } from "@/services";

/**
 * Centre pane: the AI chat assistant.
 *
 * Two rules from DESIGN.md that this component exists to honour:
 *
 * 1. Quick actions are rendered VERBATIM from the backend strings. No icons,
 *    no rewritten copy, no local fallback list -- the backend owns that text
 *    so it can change without a frontend release.
 *
 * 2. Chat NEVER edits the resume directly. When a reply carries a suggestion
 *    it renders as an Accept/Reject card and goes through the exact same
 *    review path as tailoring (Epic 5). The assistant is prompt-bound never
 *    to claim it applied a change, and this UI must not imply otherwise.
 */

/** Below this many remaining messages the counter appears as a warning. */
const LOW_BUDGET_THRESHOLD = 5;

export function ChatPane({ resumeId }: { resumeId: string | null }) {
  const { data, isLoading } = useChat(resumeId);
  const send = useSendMessage(resumeId);
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const messages = data?.messages ?? [];
  const quickActions = data?.quick_actions ?? [];
  const left = data?.tokens_left ?? 0;
  const exhausted = left <= 0;

  // Follow the conversation as it grows. `messages.length` rather than the
  // array identity: refetches produce a new array with the same content and
  // would otherwise yank the user back down mid-scroll.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  function submit(text: string) {
    const content = text.trim();
    if (!content || exhausted || send.isPending) return;
    setDraft("");
    send.mutate(content, {
      // Rolling the optimistic message back without saying why looks like the
      // app ate it. Put the text back in the box so it is not lost.
      onError: () => setDraft(content),
    });
  }

  return (
    <section className="flex min-h-0 flex-col border-r border-line-soft bg-surface-1">
      <header className="flex shrink-0 items-center gap-2 border-b border-line-soft px-4 py-2.5">
        <Sparkles className="size-4 text-brand-text" aria-hidden />
        <h2 className="text-sm font-semibold">AI Assistant</h2>

        {/* Budget is hidden until it is nearly gone: a permanent counter makes
            a 40-message allowance feel like a meter running down. */}
        {!isLoading && left <= LOW_BUDGET_THRESHOLD && (
          <span
            className={
              "ml-auto rounded-full px-2 py-0.5 text-xs font-medium " +
              (exhausted
                ? "bg-reject/10 text-reject-text"
                : "bg-warning/10 text-warning")
            }
          >
            {exhausted
              ? "No messages left"
              : `${left} message${left === 1 ? "" : "s"} left`}
          </span>
        )}
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {isLoading ? (
          <div className="flex items-center justify-center py-10 text-content-muted">
            <Loader2 className="size-5 animate-spin" aria-hidden />
          </div>
        ) : messages.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="flex flex-col gap-3">
            {messages.map((m, i) => {
              // Retry re-sends the message this reply was answering. Looked
              // up rather than remembered in state: the reply can arrive on
              // a later render, and a refetch replaces the array wholesale.
              const asked = messages
                .slice(0, i)
                .reverse()
                .find((x) => x.role === "user");
              return (
                <MessageBubble
                  key={m.id}
                  message={m}
                  resumeId={resumeId}
                  onRetry={asked ? () => submit(asked.content) : undefined}
                  retrying={send.isPending}
                />
              );
            })}
          </ul>
        )}

        {send.isPending && (
          <div className="mt-3 flex items-center gap-2 text-xs text-content-muted">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            Thinking…
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* Quick actions: backend copy, rendered as-is. */}
      {quickActions.length > 0 && !exhausted && (
        <div className="flex shrink-0 flex-wrap gap-1.5 border-t border-line-soft px-4 py-2.5">
          {quickActions.map((action) => (
            <button
              key={action}
              onClick={() => submit(action)}
              disabled={send.isPending}
              className="rounded-full border border-line-soft px-2.5 py-1 text-xs text-content-muted transition-colors hover:border-brand-text hover:text-content disabled:opacity-50"
            >
              {action}
            </button>
          ))}
        </div>
      )}

      {send.isError && (
        <p className="shrink-0 border-t border-line-soft bg-reject/10 px-4 py-2 text-xs text-reject-text">
          {(send.error as { status?: number })?.status === 429
            ? "Message budget spent — your text has been put back in the box."
            : "Could not send that message. Your text has been put back in the box."}
        </p>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(draft);
        }}
        className="shrink-0 border-t border-line-soft p-3"
      >
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter breaks the line -- chat convention.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(draft);
              }
            }}
            rows={2}
            disabled={exhausted || !resumeId}
            placeholder={
              exhausted
                ? "You have used all your messages for this demo user."
                : "Ask about your resume…"
            }
            className="min-h-[44px] flex-1 resize-none rounded-lg border border-line-soft bg-surface-2 px-3 py-2 text-sm outline-none placeholder:text-content-muted focus-visible:border-brand-text disabled:opacity-60"
          />
          <Button
            type="submit"
            size="icon"
            aria-label="Send message"
            disabled={!draft.trim() || exhausted || send.isPending}
          >
            <Send className="size-4" aria-hidden />
          </Button>
        </div>
        {/* Say WHY the box is dead. Letting the user type into a disabled
            composer and discover the limit on send is worse. */}
        {exhausted && (
          <p className="mt-1.5 text-xs text-reject-text">
            Message budget spent. Switch demo user to keep exploring.
          </p>
        )}
      </form>
    </section>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center px-4 py-10 text-center">
      <div className="mb-3 rounded-full bg-surface-2 p-3">
        <Bot className="size-5 text-brand-text" aria-hidden />
      </div>
      <p className="text-sm font-medium">Ask about your resume</p>
      <p className="mt-1 max-w-[34ch] text-xs leading-relaxed text-content-muted">
        Get feedback on wording, structure or a specific bullet. Any edit the
        assistant proposes comes back as a suggestion you review — nothing is
        applied on its own.
      </p>
    </div>
  );
}

/**
 * The score movement an accepted edit produced.
 *
 * Rendered only when the score actually MOVED -- a "+0 points" badge after
 * every accept teaches the user to stop reading the badge, which costs them
 * the ones that matter.
 *
 * The bar is the resume's absolute position out of 100, split into what the
 * score already was and what this edit added, so a +2 on a weak resume and a
 * +2 on a strong one look different -- which they are.
 */
export function ScoreCard({
  before,
  after,
  delta,
}: {
  before: number;
  after: number;
  delta: number;
}) {
  const up = delta > 0;
  const lo = Math.max(0, Math.min(100, Math.min(before, after)));
  const hi = Math.max(0, Math.min(100, Math.max(before, after)));

  return (
    <div className="w-full rounded-xl border border-line-soft bg-surface-1 p-3 shadow-sm">
      <div className="flex items-start gap-3">
        <span
          className={
            "mt-0.5 grid size-9 shrink-0 place-items-center rounded-full " +
            (up ? "bg-accept text-on-accept" : "bg-reject text-on-reject")
          }
          aria-hidden
        >
          {up ? <TrendingUp className="size-4.5" /> : <TrendingDown className="size-4.5" />}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className="text-sm font-semibold text-content">
              Resume score {up ? "increased" : "decreased"}
            </p>
            <span
              className={
                "rounded-full px-2 py-0.5 text-[11px] font-semibold " +
                (up
                  ? "bg-accept-text/15 text-accept-text"
                  : "bg-reject-text/15 text-reject-text")
              }
            >
              {up ? "+" : ""}
              {delta} point{Math.abs(delta) === 1 ? "" : "s"}
            </span>
          </div>

          <div className="mt-2 flex items-baseline gap-2">
            <span className="flex flex-col">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-content-muted">
                Before
              </span>
              <span className="text-xl font-semibold tabular-nums text-content-muted">
                {before}
              </span>
            </span>
            <ArrowRight className="size-3.5 shrink-0 self-end mb-1.5 text-content-muted" aria-hidden />
            <span className="flex flex-col">
              <span
                className={
                  "text-[10px] font-semibold uppercase tracking-wide " +
                  (up ? "text-accept-text" : "text-reject-text")
                }
              >
                Now
              </span>
              <span
                className={
                  "text-3xl font-bold leading-none tabular-nums " +
                  (up ? "text-accept-text" : "text-reject-text")
                }
              >
                {after}
              </span>
            </span>
          </div>
        </div>
      </div>

      {/* Absolute position out of 100. The second segment is this edit's
          contribution, so the user can see how far the resume still has to
          go rather than only how far it just moved. */}
      <div
        className="mt-3 flex h-1.5 w-full overflow-hidden rounded-full bg-surface-3"
        role="img"
        aria-label={`Resume score ${after} out of 100, ${up ? "up" : "down"} ${Math.abs(delta)} from ${before}`}
      >
        <div
          className={up ? "h-full bg-accept-text/50" : "h-full bg-reject-text/50"}
          style={{ width: `${lo}%` }}
        />
        <div
          className={up ? "h-full bg-accept-text" : "h-full bg-reject-text"}
          style={{ width: `${hi - lo}%` }}
        />
      </div>
    </div>
  );
}

/**
 * A row mirrored from the Analyze / Tailor panes.
 *
 * Rendered as an EVENT, never as an assistant bubble. The rule at the top of
 * this file is that the assistant must never claim it changed the resume;
 * these rows say "this happened", which is a different claim and the only
 * one that is true -- the edit was made by the user accepting a card.
 *
 * This is deliberately NOT the reference screenshot's treatment, which opens
 * with "I've updated your resume. The bullet ... now reads:". That sentence
 * is first-person authorship of someone else's edit. The same information --
 * where it landed, what it now says, why it is better -- is presented here
 * without the model taking credit for it.
 */
export function EventRow({
  message,
  resumeId,
}: {
  message: ChatMessageOut;
  resumeId: string | null;
}) {
  const meta = message.meta ?? {};
  const applied = meta.kind === "suggestion_accepted";
  const delta = meta.score_delta ?? 0;
  const quoted = applied ? meta.applied_text : meta.draft_text;
  const where = meta.placement || meta.target_ref;

  return (
    <li className="flex flex-col items-stretch gap-2 py-1">
      {/* Status line. Full-width and un-bubbled: an event is a note in the
          margin of the conversation, not a party speaking in it. */}
      <div className="flex items-center gap-2">
        <span
          className={
            "grid size-5 shrink-0 place-items-center rounded-full " +
            (applied ? "bg-accept text-on-accept" : "bg-edit text-on-edit")
          }
          aria-hidden
        >
          {applied ? <Check className="size-3" /> : <Wand2 className="size-2.5" />}
        </span>
        <span className="text-xs font-semibold text-content">
          {applied ? "Resume edited" : "Draft ready for review"}
        </span>
        {where && (
          <span className="truncate text-[11px] text-content-muted" title={where}>
            {where}
          </span>
        )}
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
      </div>

      {applied && delta !== 0 && (
        <ScoreCard
          before={meta.score_before ?? 0}
          after={meta.score_after ?? 0}
          delta={delta}
        />
      )}

      {quoted && (
        <figure className="m-0">
          <figcaption className="mb-1 text-xs text-content-muted">
            {applied ? "Now reads:" : "Proposed:"}
          </figcaption>
          {/* Green is the changed-text colour everywhere else in the app
              (--color-change owns it; --color-mark stays keyword/[__]-only),
              so the quote is tinted the same way the diff is. */}
          <blockquote className="m-0 border-l-2 border-change-border bg-change/10 py-1.5 pl-3 pr-2 text-sm italic leading-relaxed text-content">
            {quoted}
          </blockquote>
        </figure>
      )}

      {message.content && (
        <p className="whitespace-pre-wrap text-xs leading-relaxed text-content-muted">
          {message.content}
        </p>
      )}

      {/* Nothing is applied yet on a drafted event -- the same Accept/Reject
          gate the Tailor pane uses is the only way text reaches the resume. */}
      {message.suggestion_id && !applied && (
        <InlineSuggestion suggestionId={message.suggestion_id} resumeId={resumeId} />
      )}
    </li>
  );
}

export function MessageBubble({
  message,
  resumeId,
  onRetry,
  retrying,
}: {
  message: ChatMessageOut;
  resumeId: string | null;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  if (message.role === "event") {
    return <EventRow message={message} resumeId={resumeId} />;
  }
  const isUser = message.role === "user";
  // A turn the user caused by pressing a button rather than typing. It is
  // genuinely theirs, so it keeps the user styling -- but it is labelled, so
  // the transcript never pretends they typed a sentence they did not.
  const fromButton = message.meta?.kind === "fix_requested";
  return (
    <li className={"flex flex-col " + (isUser ? "items-end" : "items-start")}>
      {fromButton && (
        <span className="mb-1 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-content-muted">
          <Wand2 className="size-2.5" aria-hidden />
          from Resume Analysis
        </span>
      )}
      <div
        className={
          "max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm leading-relaxed " +
          (isUser
            ? "rounded-br-sm bg-brand text-on-brand"
            : "rounded-bl-sm bg-surface-2 text-content")
        }
      >
        {message.content}
      </div>
      {/* An outage must not read as the assistant being vague. Saying which
          one it is -- and offering the retry -- is the whole difference
          between "it is broken" and "it is stupid". */}
      {message.meta?.kind === "agent_unreachable" && (
        <div className="mt-1.5 flex max-w-[85%] flex-wrap items-center gap-2 rounded-lg border border-line-soft bg-surface-2 px-2.5 py-1.5">
          <TriangleAlert className="size-3.5 shrink-0 text-warning" aria-hidden />
          <span className="text-xs text-content-muted">
            The AI assistant was unreachable, so this came from the built-in
            rules. This message was not charged.
          </span>
          {message.meta?.reason && (
            <details className="w-full">
              <summary className="cursor-pointer text-[11px] text-content-muted underline-offset-2 hover:underline">
                Why?
              </summary>
              <p className="mt-1 break-words font-mono text-[10px] leading-relaxed text-content-muted">
                {message.meta.reason}
              </p>
            </details>
          )}
          {message.meta?.retryable && onRetry && (
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-xs"
              onClick={onRetry}
              disabled={retrying}
            >
              {retrying ? (
                <Loader2 className="size-3 animate-spin" aria-hidden />
              ) : (
                <RotateCw className="size-3" aria-hidden />
              )}
              Retry
            </Button>
          )}
        </div>
      )}
      {message.suggestion_id && (
        <InlineSuggestion
          suggestionId={message.suggestion_id}
          resumeId={resumeId}
        />
      )}
    </li>
  );
}

/**
 * The compact Accept/Reject card attached to an assistant message.
 *
 * Intentionally minimal -- the full card (keywords, critic notes, grounding)
 * lives in the Tailor pane. What matters here is that the same review gate
 * exists: the resume does not change until the user says so.
 */
function InlineSuggestion({
  suggestionId,
  resumeId,
}: {
  suggestionId: string;
  resumeId: string | null;
}) {
  const action = useSuggestionAction(resumeId, null);
  const { data: suggestion } = useSuggestion(suggestionId);
  const [done, setDone] = useState<"accepted" | "rejected" | null>(null);

  // A question card has no draft to accept -- pressing Accept on one raises
  // "Answer this question before accepting it". Chat stores only an id, so
  // without reading the card back the skills picker is unreachable here.
  if (!done && suggestion?.status === "needs_input" && resumeId) {
    return (
      <div className="mt-2 w-[85%]">
        <ClarifyingQuestionCard
          suggestion={suggestion}
          resumeId={resumeId}
          onResolved={(outcome) =>
            setDone(outcome === "attested" ? "accepted" : "rejected")
          }
        />
      </div>
    );
  }

  if (done) {
    return (
      <p className="mt-1.5 text-xs text-content-muted">
        {done === "accepted"
          ? "Applied to your resume."
          : "Dismissed — resume unchanged."}
      </p>
    );
  }

  return (
    <div className="mt-2 w-[85%] rounded-lg border border-line-soft bg-surface-2 p-2.5">
      <p className="mb-2 text-xs font-medium text-content-muted">
        Proposed edit — review before it is applied
      </p>
      <div className="flex gap-2">
        <Button
          size="sm"
          className="h-7 bg-accept text-on-accept hover:bg-accept/90"
          disabled={action.isPending}
          onClick={() =>
            action.mutate(
              { id: suggestionId, action: "accept" },
              { onSuccess: () => setDone("accepted") },
            )
          }
        >
          <Check className="size-3.5" aria-hidden />
          Accept
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 border-reject-border text-reject-text"
          disabled={action.isPending}
          onClick={() =>
            action.mutate(
              { id: suggestionId, action: "reject" },
              { onSuccess: () => setDone("rejected") },
            )
          }
        >
          <X className="size-3.5" aria-hidden />
          Reject
        </Button>
      </div>
    </div>
  );
}
