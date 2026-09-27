import { http } from "@/api/client";
import type {
  DeleteOut,
  ParseStatusOut,
  ResumeOut,
  ResumeSummaryOut,
  StructuredData,
} from "./types";

/** Mirrors backend/app/api/resumes.py — 10 routes. */
export const resumeService = {
  /** GET /api/resumes — scoped to X-User-Id server-side. */
  list: () => http.get<ResumeSummaryOut[]>("/resumes"),

  /** GET /api/resumes/{id} — 404 both when missing and when owned by
   *  another user; the backend does not distinguish, by design. */
  get: (resumeId: string) => http.get<ResumeOut>(`/resumes/${resumeId}`),

  /**
   * POST /api/resumes/upload — multipart {file, title}.
   *
   * Parses INLINE and returns the finished ResumeOut ("Parse inline: a local
   * 5-user app does not need a job queue" — resumes.py). The resolved value
   * already carries structured_data, so there is nothing to poll afterwards.
   *
   * Accepts .pdf/.docx/.txt up to max_upload_mb (10). A bad type is a 400.
   */
  upload: (file: File, title = "") =>
    http.upload<ResumeOut>("/resumes/upload", file, title ? { title } : {}),

  /** GET /api/resumes/{id}/parse-status — only useful for a resume stranded
   *  at "pending" by an interrupted upload. */
  parseStatus: (resumeId: string) =>
    http.get<ParseStatusOut>(`/resumes/${resumeId}/parse-status`),

  /** PUT /api/resumes/{id}/data — replaces structured_data wholesale. */
  updateData: (resumeId: string, structuredData: StructuredData) =>
    http.put<ResumeOut>(`/resumes/${resumeId}/data`, {
      structured_data: structuredData,
    }),

  /** POST /api/resumes/{id}/template — non-destructive; moves template_key
   *  only, because content and layout are decoupled. */
  applyTemplate: (resumeId: string, templateKey: string) =>
    http.post<ResumeOut>(`/resumes/${resumeId}/template`, {
      template_key: templateKey,
    }),

  /**
   * POST /api/resumes/{id}/fork — copy a resume into a new row.
   *
   * Copies structured_data, template and grounding VECTORS; versions and
   * analyses start fresh. This is what lets one base resume serve many job
   * applications without the base ever being mutated.
   */
  fork: (resumeId: string, title = "", tailoredFor = "") =>
    http.post<ResumeOut>(`/resumes/${resumeId}/fork`, {
      title,
      tailored_for: tailoredFor,
    }),

  /** PATCH /api/resumes/{id} — rename. Empty titles are rejected (400). */
  rename: (resumeId: string, title: string) =>
    http.patch<ResumeOut>(`/resumes/${resumeId}`, { title }),

  /** POST /api/resumes — blank resume for the library's "New Resume" card. */
  create: (title = "Untitled resume") =>
    http.post<ResumeOut>("/resumes", { title }),

  /** DELETE /api/resumes/{id} — also drops vectors and stored objects.
   *  Forks are ORPHANED, never cascaded: deleting a base must not delete the
   *  tailored versions already sent to employers. */
  remove: (resumeId: string) => http.delete<DeleteOut>(`/resumes/${resumeId}`),
};
