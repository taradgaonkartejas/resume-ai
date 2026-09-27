import { useEffect, useRef, useState } from "react";
import { Bot, Check, Loader2, Send, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChat, useSendMessage, useSuggestionAction } from "@/api/queries";
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
            {messages.map((m) => (
              <MessageBubble key={m.id} message={m} resumeId={resumeId} />
            ))}
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

function MessageBubble({
  message,
  resumeId,
}: {
  message: ChatMessageOut;
  resumeId: string | null;
}) {
  const isUser = message.role === "user";
  return (
    <li className={"flex flex-col " + (isUser ? "items-end" : "items-start")}>
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
  const [done, setDone] = useState<"accepted" | "rejected" | null>(null);

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
