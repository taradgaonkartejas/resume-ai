import { http } from "@/api/client";
import type { ChatHistoryOut, ChatReplyOut } from "./types";

/**
 * Mirrors backend/app/api/chat.py.
 *
 * quick_actions come back as 4 plain strings and are rendered verbatim as
 * chips. The backend owns that copy; hardcoding icon+description cards in the
 * UI would drift the moment the list changes.
 *
 * send() raises QuotaExceeded server-side at 0 tokens, which surfaces here as
 * an ApiError — disable the composer when tokens_left is 0 rather than
 * letting the user discover it by failure.
 */
export const chatService = {
  /** GET /api/resumes/{id}/chat → {messages, quick_actions, tokens_left} */
  history: (resumeId: string) =>
    http.get<ChatHistoryOut>(`/resumes/${resumeId}/chat`),

  /** POST /api/resumes/{id}/chat {content} → 201
   *  The reply may carry an inline suggestion to render as a compact card. */
  send: (resumeId: string, content: string) =>
    http.post<ChatReplyOut>(`/resumes/${resumeId}/chat`, { content }),
};
