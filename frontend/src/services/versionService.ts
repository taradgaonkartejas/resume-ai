import { http } from "@/api/client";
import type { ResumeOut, VersionOut } from "./types";

/**
 * Mirrors backend/app/api/versions.py.
 *
 * History is a cursor over a linear list, not a tree: recording a new version
 * truncates everything with seq greater than the current cursor. So editing
 * after an undo discards the redo branch — same as a text editor.
 */
export const versionService = {
  /** GET /api/resumes/{id}/versions */
  list: (resumeId: string) => http.get<VersionOut[]>(`/resumes/${resumeId}/versions`),

  /** POST /api/resumes/{id}/undo → the resume at the previous cursor. */
  undo: (resumeId: string) => http.post<ResumeOut>(`/resumes/${resumeId}/undo`),

  /** POST /api/resumes/{id}/redo */
  redo: (resumeId: string) => http.post<ResumeOut>(`/resumes/${resumeId}/redo`),
};
