/** SSR entry for the chat-event styling preview. Not shipped in the app. */
import { renderToStaticMarkup } from "react-dom/server";
import { EventRow, MessageBubble } from "@/features/chat/ChatPane";
import type { ChatMessageOut } from "@/services";

function msg(over: Partial<ChatMessageOut>): ChatMessageOut {
  return {
    id: "1", resume_id: "r", role: "event", content: "",
    suggestion_id: null, created_at: "", meta: {}, ...over,
  };
}

export function render(): string {
  const rows = [
    msg({
      content:
        "Replaces the previous claim of a 100% reduction, which implied 0ms latency \u2014 an impossible result. The 45% figure is a more credible, trust-building metric for recruiters.",
      meta: {
        kind: "suggestion_accepted", target_ref: "exp_0.bullet_0",
        placement: "Software Engineer \u2014 Ceinsys Tech Ltd.",
        applied_text:
          "Engineered Java Full Stack solutions, designing and shipping RESTful APIs and service-to-service integrations across Java/Spring Boot and Python/FastAPI services, while performing backend development to optimize PostgreSQL/PostGIS queries and cut p95 response time by 45%.",
        score_before: 83, score_after: 85, score_delta: 2,
      },
    }),
    msg({
      content: "Recruiters need to see your code. The direct link makes the profile reachable in one click.",
      meta: {
        kind: "suggestion_drafted", target_ref: "contact.links",
        placement: "Contact \u2014 links", draft_text: "github.com/taradgaonkartejas",
        applied: false,
      },
    }),
    msg({
      meta: {
        kind: "suggestion_accepted", target_ref: "summary.text",
        placement: "Professional Summary",
        applied_text: "Full Stack Engineer with 4 years building Java and Python services in production.",
        score_before: 85, score_after: 85, score_delta: 0,
      },
    }),
    msg({
      content:
        "Removed a filler phrase and led with the action. Scores are unchanged because the rule that fired carries no points.",
      meta: {
        kind: "suggestion_accepted", target_ref: "exp_1.bullet_2",
        placement: "Intern \u2014 Acme", applied_text: "Automated the nightly export, removing a manual step.",
        score_before: 85, score_after: 82, score_delta: -3,
      },
    }),
  ];

  const user = msg({
    role: "user",
    content: "Fix with AI: The GitHub placeholder lacks a clickable URL.",
    meta: { kind: "fix_requested", target_ref: "contact.links" },
  });

  return renderToStaticMarkup(
    <ul className="flex flex-col gap-5 p-5">
      <EventRow message={rows[0]} resumeId={null} />
      <EventRow message={rows[2]} resumeId={null} />
      <EventRow message={rows[3]} resumeId={null} />
      <li className="flex flex-col items-end">
        <span className="mb-1 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-content-muted">
          from Resume Analysis
        </span>
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-brand px-3.5 py-2 text-sm leading-relaxed text-on-brand">
          {user.content}
        </div>
      </li>
      <EventRow message={rows[1]} resumeId={null} />
      <MessageBubble
        message={msg({ role: "user", content: "Suggest skills I am missing" })}
        resumeId={null}
      />
      <MessageBubble
        message={msg({
          role: "assistant",
          content:
            "To tell you what is missing I need something to compare against. Paste a job description in the Tailor tab and run it \u2014 then ask me again and I will list the skills that posting wants which your resume does not mention.",
        })}
        resumeId={null}
      />
      <MessageBubble
        message={msg({
          role: "assistant",
          content:
            "I can help with your summary, a specific bullet, or your skills.",
          meta: {
            kind: "agent_unreachable",
            retryable: true,
            reason:
              "LLMUnavailable: All models failed for task chat: groq:openai/gpt-oss-120b rate_limit_exceeded (429); groq:openai/gpt-oss-20b read timeout after 45s",
          },
        })}
        resumeId={null}
        onRetry={() => {}}
      />
    </ul>,
  );
}
