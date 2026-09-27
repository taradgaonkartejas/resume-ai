import { http } from "@/api/client";
import type { TemplateOut } from "./types";

/**
 * Mirrors backend/app/api/templates.py.
 *
 * Note applying a template lives on the resume router, not here:
 * resumeService.applyTemplate().
 */
export const templateService = {
  /** GET /api/templates — 5 seeded: modern, classic, compact, executive,
   *  minimal. design_tokens is {} for all of them. */
  list: () => http.get<TemplateOut[]>("/templates"),
};
