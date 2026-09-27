import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";
import {
  analysisService,
  chatService,
  resumeService,
  suggestionService,
  tailorService,
  templateService,
  userService,
  type ChatHistoryOut,
  type ChatMessageOut,
  type ResumeOut,
  type StructuredData,
  type TailorIn,
  type TailorSessionOut,
} from "@/services";
import { ApiError } from "@/api/client";

/**
 * React Query bindings.
 *
 * This layer owns caching and invalidation ONLY. Endpoint paths and payload
 * shapes live in services/ — nothing here builds a URL.
 */

export const keys = {
  users: ["users"] as const,
  templates: ["templates"] as const,
  resumes: ["resumes"] as const,
  resume: (id: string) => ["resume", id] as const,
  parseStatus: (id: string) => ["parse-status", id] as const,
  analysis: (id: string) => ["analysis", id] as const,
  analysisSteps: (id: string) => ["analysis", id, "steps"] as const,
  tailor: (id: string) => ["tailor", id] as const,
  suggestions: (sessionId: string) => ["suggestions", sessionId] as const,
  chat: (id: string) => ["chat", id] as const,
  versions: (id: string) => ["versions", id] as const,
};

/* ---------- reads ---------- */

export function useUsers() {
  return useQuery({
    queryKey: keys.users,
    queryFn: userService.list,
    staleTime: Infinity,
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: keys.templates,
    queryFn: templateService.list,
    staleTime: Infinity,
  });
}

/** activeUserId is part of the key so switching user refetches instead of
 *  showing the previous user's list from cache. */
export function useResumes(activeUserId: string | null) {
  return useQuery({
    queryKey: [...keys.resumes, activeUserId],
    queryFn: resumeService.list,
    enabled: Boolean(activeUserId),
  });
}

export function useResume(resumeId: string | null) {
  return useQuery({
    queryKey: keys.resume(resumeId ?? ""),
    queryFn: () => resumeService.get(resumeId!),
    enabled: Boolean(resumeId),
  });
}

/**
 * Upload parses inline, so the happy path never polls. This covers the
 * stranded-"pending" case and stops as soon as the status is terminal.
 */
export function useParseStatus(resumeId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: keys.parseStatus(resumeId ?? ""),
    queryFn: () => resumeService.parseStatus(resumeId!),
    enabled: Boolean(resumeId) && enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.parse_status;
      return status === "ready" || status === "failed" ? false : 1200;
    },
  });
}

/* ---------- writes ---------- */

type UploadVars = { file: File; title: string };
type ForkVars = { resumeId: string; title?: string; tailoredFor?: string };
type StartTailorVars = { resumeId: string; jd: TailorIn };
type SuggestionActionVars = {
  id: string;
  action: "accept" | "reject" | "edit";
  editedText?: string;
};

export function useUploadResume(
  options?: UseMutationOptions<ResumeOut, Error, UploadVars>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, title }: UploadVars) => resumeService.upload(file, title),
    ...options,
    // v5.103 signature: (data, variables, onMutateResult, context).
    onSuccess: (resume, vars, onMutateResult, context) => {
      qc.setQueryData(keys.resume(resume.id), resume);
      qc.invalidateQueries({ queryKey: keys.resumes });
      options?.onSuccess?.(resume, vars, onMutateResult, context);
    },
  });
}

export function useApplyTemplate(resumeId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (templateKey: string) =>
      resumeService.applyTemplate(resumeId!, templateKey),
    onSuccess: (resume) => {
      qc.setQueryData(keys.resume(resume.id), resume);
      qc.invalidateQueries({ queryKey: keys.resumes });
    },
  });
}

/**
 * Fork a resume. Returns the CHILD, so callers should navigate to it.
 * The list is invalidated because a new card now exists.
 */
export function useForkResume(
  options?: UseMutationOptions<ResumeOut, Error, ForkVars>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ resumeId, title, tailoredFor }: ForkVars) =>
      resumeService.fork(resumeId, title, tailoredFor),
    ...options,
    onSuccess: (resume, vars, onMutateResult, context) => {
      qc.setQueryData(keys.resume(resume.id), resume);
      qc.invalidateQueries({ queryKey: keys.resumes });
      options?.onSuccess?.(resume, vars, onMutateResult, context);
    },
  });
}

export function useRenameResume() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ resumeId, title }: { resumeId: string; title: string }) =>
      resumeService.rename(resumeId, title),
    onSuccess: (resume) => {
      qc.setQueryData(keys.resume(resume.id), resume);
      qc.invalidateQueries({ queryKey: keys.resumes });
    },
  });
}

export function useCreateResume(
  options?: UseMutationOptions<ResumeOut, Error, string | undefined>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (title?: string) => resumeService.create(title),
    ...options,
    onSuccess: (resume, vars, onMutateResult, context) => {
      qc.setQueryData(keys.resume(resume.id), resume);
      qc.invalidateQueries({ queryKey: keys.resumes });
      options?.onSuccess?.(resume, vars, onMutateResult, context);
    },
  });
}

export function useDeleteResume() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (resumeId: string) => resumeService.remove(resumeId),
    onSuccess: (_result, resumeId) => {
      qc.removeQueries({ queryKey: keys.resume(resumeId) });
      // Forks of this resume are orphaned server-side, so their rows changed
      // too -- refetch the whole list rather than patching one entry.
      qc.invalidateQueries({ queryKey: keys.resumes });
    },
  });
}

export function useUpdateResumeData(resumeId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (structuredData: StructuredData) =>
      resumeService.updateData(resumeId!, structuredData),
    onSuccess: (resume) => {
      qc.setQueryData(keys.resume(resume.id), resume);
      // Content changed: the score and the version list are both stale.
      // analysisSteps nests under keys.analysis, so prefix matching
      // invalidates the guided-editor steps too — that is what makes a fixed
      // recommendation disappear right after autosave.
      qc.invalidateQueries({ queryKey: keys.analysis(resume.id) });
      qc.invalidateQueries({ queryKey: keys.versions(resume.id) });
    },
  });
}

/* ---------- analysis ---------- */

/**
 * The latest stored analysis. 404 means "never analysed", which is a normal
 * state and not an error — retrying it would just burn requests on a resume
 * that has no report yet.
 */
export function useAnalysis(resumeId: string | null) {
  return useQuery({
    queryKey: keys.analysis(resumeId ?? "none"),
    queryFn: () => analysisService.latest(resumeId!),
    enabled: Boolean(resumeId),
    retry: (failureCount, error) =>
      error instanceof ApiError && error.isNotFound ? false : failureCount < 2,
  });
}

export function useRunAnalysis(resumeId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => analysisService.run(resumeId!),
    onSuccess: (analysis) => {
      qc.setQueryData(keys.analysis(analysis.resume_id), analysis);
      // The library card shows overall_score, so its row is now stale.
      qc.invalidateQueries({ queryKey: keys.resumes });
    },
  });
}

/**
 * Guided-editor steps. Recomputed server-side from structured_data, so it is
 * invalidated by every content mutation rather than only by re-analysing.
 */
export function useAnalysisSteps(resumeId: string | null) {
  return useQuery({
    queryKey: keys.analysisSteps(resumeId ?? "none"),
    queryFn: () => analysisService.steps(resumeId!),
    enabled: Boolean(resumeId),
  });
}

/* ---------- tailoring ---------- */

export function useTailorSessions(resumeId: string | null) {
  return useQuery({
    queryKey: keys.tailor(resumeId ?? "none"),
    queryFn: () => tailorService.listSessions(resumeId!),
    enabled: Boolean(resumeId),
  });
}

export function useSuggestions(resumeId: string | null, sessionId: string | null) {
  return useQuery({
    queryKey: keys.suggestions(sessionId ?? "none"),
    queryFn: () => tailorService.suggestions(resumeId!, sessionId!),
    enabled: Boolean(resumeId && sessionId),
  });
}

/**
 * Start a tailoring session.
 *
 * This forks server-side by default, so the returned session's resume_id is
 * a NEW resume — callers must navigate to it rather than assuming the id
 * they passed in.
 */
export function useStartTailoring(
  options?: UseMutationOptions<TailorSessionOut, Error, StartTailorVars>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ resumeId, jd }: StartTailorVars) => tailorService.start(resumeId, jd),
    ...options,
    onSuccess: (session, vars, onMutateResult, context) => {
      qc.invalidateQueries({ queryKey: keys.resumes });
      qc.invalidateQueries({ queryKey: keys.tailor(session.resume_id) });
      qc.invalidateQueries({ queryKey: keys.resume(session.resume_id) });
      options?.onSuccess?.(session, vars, onMutateResult, context);
    },
  });
}

/**
 * Accept / reject / edit one suggestion.
 *
 * Accepting is a five-step server transaction that rewrites the resume, so
 * everything downstream of the content is invalidated: the resume itself,
 * its score, its versions, and the session's keyword match.
 */
export function useSuggestionAction(resumeId: string | null, sessionId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, editedText }: SuggestionActionVars) => {
      if (action === "accept") return suggestionService.accept(id);
      if (action === "reject") return suggestionService.reject(id);
      return suggestionService.edit(id, editedText ?? "");
    },
    onSuccess: () => {
      if (sessionId) qc.invalidateQueries({ queryKey: keys.suggestions(sessionId) });
      if (resumeId) {
        qc.invalidateQueries({ queryKey: keys.resume(resumeId) });
        qc.invalidateQueries({ queryKey: keys.analysis(resumeId) });
        qc.invalidateQueries({ queryKey: keys.versions(resumeId) });
        qc.invalidateQueries({ queryKey: keys.tailor(resumeId) });
      }
      qc.invalidateQueries({ queryKey: keys.resumes });
    },
  });
}

/* ---------------------------------------------------------------- chat --- */

/**
 * Chat history + backend-owned quick actions + remaining message budget.
 *
 * The quick_actions strings are rendered verbatim as chips; the backend owns
 * that copy so the list can change without a frontend release.
 */
export function useChat(resumeId: string | null) {
  return useQuery({
    queryKey: keys.chat(resumeId ?? "none"),
    queryFn: () => chatService.history(resumeId as string),
    enabled: Boolean(resumeId),
  });
}

/**
 * Send a message.
 *
 * Optimistic: the user's own line appears immediately, because waiting on a
 * multi-agent round trip before echoing what they typed feels broken. The
 * assistant reply is not faked -- only the user turn is, and it rolls back if
 * the POST fails.
 *
 * A reply may carry a `suggestion`, which is the Epic 5 contract: chat never
 * edits the resume directly, it emits a Suggestion that goes through the same
 * Accept/Reject/Edit review as tailoring. Invalidating the resume here would
 * be wrong -- nothing has been applied yet.
 */
export function useSendMessage(resumeId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (content: string) =>
      chatService.send(resumeId as string, content),
    onMutate: async (content: string) => {
      const key = keys.chat(resumeId ?? "none");
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<ChatHistoryOut>(key);
      if (previous) {
        const optimistic: ChatMessageOut = {
          id: `optimistic-${Date.now()}`,
          resume_id: resumeId as string,
          role: "user",
          content,
          suggestion_id: null,
          created_at: new Date().toISOString(),
        };
        qc.setQueryData<ChatHistoryOut>(key, {
          ...previous,
          messages: [...previous.messages, optimistic],
        });
      }
      return { previous };
    },
    onError: (_err, _content, ctx) => {
      // Put the history back exactly as it was; a half-sent message left on
      // screen would imply the assistant is thinking about it.
      const restore = (ctx as { previous?: ChatHistoryOut } | undefined)?.previous;
      if (restore) qc.setQueryData(keys.chat(resumeId ?? "none"), restore);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: keys.chat(resumeId ?? "none") });
    },
  });
}
